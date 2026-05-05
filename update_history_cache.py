from __future__ import annotations

import io
import json
import random
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent
PRICE_FILE = ROOT / "spy_holdings_prices.csv"
HOLDINGS_FILE = ROOT / "spy_current_stock_holdings.csv"
FAILURE_FILE = ROOT / "spy_history_update_failures.csv"
HOLDINGS_URL = "https://companiesmarketcap.com/eur/spdr-sp-500-etf/holdings/"
USER_AGENT = "Mozilla/5.0"
HISTORY_DAYS_FOR_NEW_SYMBOLS = 550
OVERLAP_DAYS = 10
REQUEST_DELAY_SECONDS = 0.35
MAX_RETRIES = 3


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
            print(f"{symbol}: attempt {attempt} failed: {exc}; sleeping {sleep_for:.1f}s")
            time.sleep(sleep_for)
    raise RuntimeError(f"{symbol}: failed after {MAX_RETRIES} attempts: {last_error}")


def load_existing_prices() -> pd.DataFrame:
    if PRICE_FILE.exists():
        return pd.read_csv(PRICE_FILE, index_col=0, parse_dates=True).sort_index()
    return pd.DataFrame()


def symbol_start_date(existing: pd.DataFrame, symbol: str, today: pd.Timestamp) -> pd.Timestamp:
    if symbol in existing.columns:
        series = existing[symbol].dropna()
        if not series.empty:
            return max(series.index[-1] - pd.Timedelta(days=OVERLAP_DAYS), pd.Timestamp("2010-02-11"))
    return today - pd.Timedelta(days=HISTORY_DAYS_FOR_NEW_SYMBOLS)


def main() -> None:
    today = pd.Timestamp.utcnow().normalize().tz_localize(None) + pd.Timedelta(days=1)
    holdings = fetch_spy_stock_holdings()
    holdings.to_csv(HOLDINGS_FILE, index=False, encoding="utf-8-sig")
    symbols = holdings["Ticker"].tolist()
    existing = load_existing_prices()
    updated = existing.copy()
    failures = []

    print(f"Updating {len(symbols)} SPY symbols through {today.date().isoformat()}")
    for i, symbol in enumerate(symbols, start=1):
        start = symbol_start_date(existing, symbol, today)
        try:
            series = fetch_with_retries(symbol, start, today)
            if not series.empty:
                updated[symbol] = updated.get(symbol, pd.Series(dtype=float)).combine_first(series)
                updated.loc[series.index, symbol] = series
        except Exception as exc:
            failures.append({"symbol": symbol, "error": str(exc)})
        print(f"{i:03d}/{len(symbols)} {symbol}")
        time.sleep(REQUEST_DELAY_SECONDS + random.uniform(0.05, 0.25))

    updated = updated.sort_index()
    updated = updated.loc[:, [symbol for symbol in symbols if symbol in updated.columns]]
    updated.to_csv(PRICE_FILE, encoding="utf-8-sig")

    if failures:
        pd.DataFrame(failures).to_csv(FAILURE_FILE, index=False, encoding="utf-8-sig")
        print(f"Failures: {len(failures)}")
    elif FAILURE_FILE.exists():
        FAILURE_FILE.unlink()
        print("No failures")


if __name__ == "__main__":
    main()
