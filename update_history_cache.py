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
USER_AGENT = "Mozilla/5.0"
HISTORY_DAYS_FOR_NEW_SYMBOLS = 550
OVERLAP_DAYS = 10
REQUEST_DELAY_SECONDS = 0.35
MAX_RETRIES = 3


def safe_print(message: str) -> None:
    if sys.stdout:
        print(message)


def fetch_spy_stock_holdings() -> pd.DataFrame:
    req = urllib.request.Request(HOLDINGS_URL, headers={"User-Agent": USER_AGENT})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    holdings = pd.read_html(io.StringIO(html))[0].copy()
    holdings = holdings.rename(columns={col: str(col).strip() for col in holdings.columns})
    holdings["Ticker"] = holdings["Ticker"].astype(str).str.strip().str.replace(".", "-", regex=False)
    holdings["Weight %"] = holdings["Weight %"].astype(str).str.replace("%", "", regex=False)
    holdings["weight"] = pd.to_numeric(holdings["Weight %"], errors="coerce")
    skip_words = "CASH|EQUIVALENTS|COLLATERAL|FUTURE|INDEX"
    stocks = holdings[
        holdings["Ticker"].ne("nan")
        & holdings["Ticker"].ne("")
        & ~holdings["Ticker"].isin({"USD"})
        & holdings["weight"].notna()
        & ~holdings["Name"].astype(str).str.contains(skip_words, case=False, regex=True)
    ].copy()
    stocks = stocks.rename(columns={"Name": "Description"})
    return (
        stocks[["Ticker", "Description", "weight"]]
        .drop_duplicates("Ticker")
        .sort_values("weight", ascending=False)
        .reset_index(drop=True)
    )


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
    cache_date = existing.index[-1].date().isoformat() if not existing.empty else None
    spy_latest = fetch_with_retries("SPY", today - pd.Timedelta(days=14), today)
    market_date = latest_completed_market_date(spy_latest)
    if cache_date and market_date and pd.Timestamp(cache_date) >= pd.Timestamp(market_date):
        message = f"缓存已是最新：{cache_date}，市场最新日线：{market_date}"
        safe_print(message)
        write_status("skipped", message, cache_date, market_date)
        return

    holdings = fetch_spy_stock_holdings()
    holdings.to_csv(HOLDINGS_FILE, index=False, encoding="utf-8-sig")
    symbols = holdings["Ticker"].tolist()
    updated = existing.copy()
    failures = []

    safe_print(f"Updating {len(symbols)} SPY symbols through {today.date().isoformat()}")
    for i, symbol in enumerate(symbols, start=1):
        start = symbol_start_date(existing, symbol, today)
        try:
            series = fetch_with_retries(symbol, start, today)
            if not series.empty:
                updated[symbol] = updated.get(symbol, pd.Series(dtype=float)).combine_first(series)
                updated.loc[series.index, symbol] = series
        except Exception as exc:
            failures.append({"symbol": symbol, "error": str(exc)})
        safe_print(f"{i:03d}/{len(symbols)} {symbol}")
        time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0.05, 0.25))

    updated = updated.sort_index()
    updated = updated.loc[:, [symbol for symbol in symbols if symbol in updated.columns]]
    updated.to_csv(PRICE_FILE, encoding="utf-8-sig")

    if failures:
        pd.DataFrame(failures).to_csv(FAILURE_FILE, index=False, encoding="utf-8-sig")
        safe_print(f"Failures: {len(failures)}")
        write_status(
            "completed_with_failures",
            f"更新完成，但有 {len(failures)} 个失败",
            updated.index[-1].date().isoformat() if not updated.empty else cache_date,
            market_date,
        )
    elif FAILURE_FILE.exists():
        FAILURE_FILE.unlink()
        safe_print("No failures")
        write_status(
            "completed",
            "历史缓存更新完成",
            updated.index[-1].date().isoformat() if not updated.empty else cache_date,
            market_date,
        )
    else:
        write_status(
            "completed",
            "历史缓存更新完成",
            updated.index[-1].date().isoformat() if not updated.empty else cache_date,
            market_date,
        )


if __name__ == "__main__":
    main()
