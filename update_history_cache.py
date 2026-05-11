from __future__ import annotations

import io
import json
import random
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


ROOT = Path(__file__).resolve().parent
PRICE_FILE = ROOT / "spy_holdings_prices.csv"
HOLDINGS_FILE = ROOT / "spy_current_stock_holdings.csv"
FAILURE_FILE = ROOT / "spy_history_update_failures.csv"
STATUS_FILE = ROOT / "spy_history_update_status.json"
HOLDINGS_URL = "https://companiesmarketcap.com/eur/spdr-sp-500-etf/holdings/"
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
USER_AGENT = "Mozilla/5.0"
HISTORY_DAYS_FOR_NEW_SYMBOLS = 550
OVERLAP_DAYS = 10
REQUEST_DELAY_SECONDS = 0.35
MAX_RETRIES = 3


def safe_print(message: str) -> None:
    if sys.stdout:
        print(message)


def fetch_sp500_sectors() -> pd.DataFrame:
    try:
        req = urllib.request.Request(SP500_WIKI_URL, headers={"User-Agent": USER_AGENT})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        tables = pd.read_html(io.StringIO(html))
    except Exception as exc:
        safe_print(f"Sector lookup failed: {exc}")
        return pd.DataFrame(columns=["Ticker", "Sector"])

    for table in tables:
        if {"Symbol", "GICS Sector"}.issubset(table.columns):
            sectors = table[["Symbol", "GICS Sector"]].copy()
            sectors = sectors.rename(columns={"Symbol": "Ticker", "GICS Sector": "Sector"})
            sectors["Ticker"] = sectors["Ticker"].astype(str).str.strip().str.replace(".", "-", regex=False)
            sectors["Sector"] = sectors["Sector"].astype(str).str.strip()
            return sectors.drop_duplicates("Ticker")

    safe_print("Sector lookup failed: S&P 500 table not found")
    return pd.DataFrame(columns=["Ticker", "Sector"])


def fetch_spy_stock_holdings() -> pd.DataFrame:
    req = urllib.request.Request(HOLDINGS_URL, headers={"User-Agent": USER_AGENT})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    holdings = pd.read_html(io.StringIO(html))[0].copy()
    holdings = holdings.rename(columns={col: str(col).strip() for col in holdings.columns})
    holdings["Ticker"] = holdings["Ticker"].astype(str).str.strip().str.replace(".", "-", regex=False)
    holdings["Weight %"] = holdings["Weight %"].astype(str).str.replace("%", "", regex=False)
    holdings["weight"] = pd.to_numeric(holdings["Weight %"], errors="coerce")
    skip_words = "CASH|EQUIVALENTS|COLLATERAL|FUTURE|INDEX|CONTRA"
    stocks = holdings[
        holdings["Ticker"].ne("nan")
        & holdings["Ticker"].ne("")
        & ~holdings["Ticker"].isin({"-", "USD"})
        & holdings["weight"].notna()
        & holdings["Name"].astype(str).str.upper().ne("US DOLLAR")
        & ~holdings["Name"].astype(str).str.contains(skip_words, case=False, regex=True)
    ].copy()
    stocks = stocks.rename(columns={"Name": "Description"})
    stocks = (
        stocks[["Ticker", "Description", "weight"]]
        .drop_duplicates("Ticker")
        .sort_values("weight", ascending=False)
        .reset_index(drop=True)
    )
    sectors = fetch_sp500_sectors()
    stocks = stocks.merge(sectors, on="Ticker", how="left")
    stocks["Sector"] = stocks["Sector"].fillna("Unknown")
    return stocks[["Ticker", "Description", "Sector", "weight"]]


def yahoo_chart(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    period1 = int(pd.Timestamp(start, tz="UTC").timestamp())
    period2 = int(pd.Timestamp(end, tz="UTC").timestamp())
    encoded = urllib.parse.quote(symbol, safe="")
    url = (
        f"https://query2.finance.yahoo.com/v8/finance/chart/{encoded}"
        f"?period1={period1}&period2={period2}&interval=1d"
        "&events=history&includeAdjustedClose=true"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    result = payload["chart"]["result"]
    if not result:
        raise ValueError(f"No chart data for {symbol}")
    item = result[0]
    index = [
        pd.Timestamp(datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat())
        for ts in item["timestamp"]
    ]
    values = item["indicators"]["adjclose"][0]["adjclose"]
    return pd.Series(values, index=index, name=symbol).dropna().sort_index()


def fetch_with_retries(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.Series:
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return yahoo_chart(symbol, start, end)
        except Exception as exc:
            last_error = exc
            sleep_for = REQUEST_DELAY_SECONDS * attempt + random.uniform(0.2, 0.8)
            safe_print(f"{symbol}: attempt {attempt} failed: {exc}; sleeping {sleep_for:.1f}s")
            time.sleep(sleep_for)
    raise RuntimeError(f"{symbol}: failed after {MAX_RETRIES} attempts: {last_error}")


def load_existing_prices() -> pd.DataFrame:
    if PRICE_FILE.exists():
        return pd.read_csv(PRICE_FILE, index_col=0, parse_dates=True).sort_index()
    return pd.DataFrame()


def load_existing_holdings() -> pd.DataFrame:
    if not HOLDINGS_FILE.exists():
        return pd.DataFrame(columns=["Ticker"])
    holdings = pd.read_csv(HOLDINGS_FILE)
    if "Ticker" not in holdings.columns:
        return pd.DataFrame(columns=["Ticker"])
    holdings["Ticker"] = holdings["Ticker"].astype(str).str.strip().str.replace(".", "-", regex=False)
    return holdings


def holdings_symbol_changes(previous: pd.DataFrame, current: pd.DataFrame) -> tuple[list[str], list[str]]:
    previous_symbols = set(previous.get("Ticker", pd.Series(dtype=str)).dropna().astype(str))
    current_symbols = list(current["Ticker"].dropna().astype(str))
    current_symbol_set = set(current_symbols)
    added = [symbol for symbol in current_symbols if symbol not in previous_symbols]
    removed = sorted(previous_symbols - current_symbol_set)
    return added, removed


def write_status(status: str, message: str, cache_date: str | None = None, market_date: str | None = None) -> None:
    payload = {
        "status": status,
        "message": message,
        "cacheDate": cache_date,
        "marketDate": market_date,
        "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def symbol_start_date(existing: pd.DataFrame, symbol: str, today: pd.Timestamp) -> pd.Timestamp:
    if symbol in existing.columns:
        series = existing[symbol].dropna()
        if not series.empty:
            return max(series.index[-1] - pd.Timedelta(days=OVERLAP_DAYS), pd.Timestamp("2010-02-11"))
    return today - pd.Timedelta(days=HISTORY_DAYS_FOR_NEW_SYMBOLS)


def latest_completed_market_date(spy_series: pd.Series) -> str | None:
    if spy_series.empty:
        return None
    now_ny = datetime.now(ZoneInfo("America/New_York"))
    cutoff_date = now_ny.date()
    if now_ny.hour < 18 or (now_ny.hour == 18 and now_ny.minute < 30):
        cutoff_date = (pd.Timestamp(cutoff_date) - pd.Timedelta(days=1)).date()
    eligible = spy_series[spy_series.index.date <= cutoff_date]
    if eligible.empty:
        return None
    return eligible.index[-1].date().isoformat()


def main() -> None:
    today = pd.Timestamp.now("UTC").normalize().tz_localize(None) + pd.Timedelta(days=1)
    existing = load_existing_prices()
    previous_holdings = load_existing_holdings()
    cache_date = existing.index[-1].date().isoformat() if not existing.empty else None
    spy_latest = fetch_with_retries("SPY", today - pd.Timedelta(days=14), today)
    market_date = latest_completed_market_date(spy_latest)
    market_ts = pd.Timestamp(market_date) if market_date else None
    trimmed_future_rows = False
    if market_ts is not None and not existing.empty:
        future_rows = existing.index > market_ts
        if future_rows.any():
            safe_print(f"Trimming {future_rows.sum()} rows after completed market date {market_date}")
            existing = existing.loc[~future_rows].copy()
            cache_date = existing.index[-1].date().isoformat() if not existing.empty else None
            trimmed_future_rows = True

    holdings = fetch_spy_stock_holdings()
    added_symbols, removed_symbols = holdings_symbol_changes(previous_holdings, holdings)
    holdings_changed = bool(added_symbols or removed_symbols)
    symbols = holdings["Ticker"].tolist()
    holdings.to_csv(HOLDINGS_FILE, index=False, encoding="utf-8-sig")

    safe_print(
        "Fetched latest SPY holdings: "
        f"{len(symbols)} symbols; added={added_symbols or 'none'}; removed={removed_symbols or 'none'}"
    )

    cache_is_current = bool(cache_date and market_date and pd.Timestamp(cache_date) >= pd.Timestamp(market_date))
    if cache_is_current and not holdings_changed:
        message = f"缓存已是最新：{cache_date}，市场最新日线：{market_date}，SPY 持仓股无变化"
        safe_print(message)
        write_status("skipped", message, cache_date, market_date)
        if trimmed_future_rows:
            existing = existing.loc[:, [symbol for symbol in symbols if symbol in existing.columns]]
            existing.to_csv(PRICE_FILE, encoding="utf-8-sig")
        return

    updated = existing.copy()
    failures = []
    if cache_is_current:
        symbols_to_update = [
            symbol
            for symbol in symbols
            if symbol not in existing.columns or existing[symbol].dropna().empty
        ]
        if symbols_to_update:
            safe_print(
                "SPY holdings changed while price cache is current; "
                f"fetching history only for new/missing symbols: {symbols_to_update}"
            )
        else:
            safe_print("SPY holdings changed, but no new symbols need price downloads")
    else:
        symbols_to_update = symbols

    safe_print(f"Updating {len(symbols_to_update)} SPY symbols through {today.date().isoformat()}")
    for i, symbol in enumerate(symbols_to_update, start=1):
        start = symbol_start_date(existing, symbol, today)
        try:
            series = fetch_with_retries(symbol, start, today)
            if market_ts is not None:
                series = series.loc[series.index <= market_ts]
            if not series.empty:
                updated = updated.reindex(updated.index.union(series.index))
                if symbol not in updated.columns:
                    updated[symbol] = pd.NA
                updated.loc[series.index, symbol] = series
        except Exception as exc:
            failures.append({"symbol": symbol, "error": str(exc)})
        safe_print(f"{i:03d}/{len(symbols_to_update)} {symbol}")
        time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0.05, 0.25))

    updated = updated.sort_index()
    updated = updated.loc[:, [symbol for symbol in symbols if symbol in updated.columns]]
    updated.to_csv(PRICE_FILE, encoding="utf-8-sig")
    new_cache_date = updated.index[-1].date().isoformat() if not updated.empty else cache_date
    holdings_change_text = ""
    if holdings_changed:
        holdings_change_text = (
            f"；新增：{','.join(added_symbols) or '无'}"
            f"；移除：{','.join(removed_symbols) or '无'}"
        )

    if failures:
        pd.DataFrame(failures).to_csv(FAILURE_FILE, index=False, encoding="utf-8-sig")
        safe_print(f"Failures: {len(failures)}")
        write_status(
            "completed_with_failures",
            f"更新完成，但有 {len(failures)} 个失败{holdings_change_text}",
            new_cache_date,
            market_date,
        )
    elif FAILURE_FILE.exists():
        FAILURE_FILE.unlink()
        safe_print("No failures")
        write_status(
            "completed",
            f"历史缓存更新完成{holdings_change_text}",
            new_cache_date,
            market_date,
        )
    else:
        write_status(
            "completed",
            f"历史缓存更新完成{holdings_change_text}",
            new_cache_date,
            market_date,
        )


if __name__ == "__main__":
    main()
