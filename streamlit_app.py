from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st


ROOT = Path(__file__).resolve().parent
PRICE_FILE = ROOT / "spy_holdings_prices.csv"
HOLDINGS_FILE = ROOT / "spy_current_stock_holdings.csv"
DEFAULT_TOP_N = 10
TRADE_COST = 0.001
DEFAULT_GITHUB_REPOSITORY = "echohua6572/spy"
DEFAULT_GITHUB_WORKFLOW = "update-history.yml"
DEFAULT_GITHUB_REF = "main"


st.set_page_config(
    page_title="SPY Top10 动量持仓监控",
    page_icon="📈",
    layout="wide",
)


@st.cache_data(show_spinner=False)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    prices = pd.read_csv(PRICE_FILE, index_col=0, parse_dates=True).sort_index()
    holdings = pd.read_csv(HOLDINGS_FILE)
    holdings["Ticker"] = holdings["Ticker"].astype(str).str.strip()
    holdings = holdings.drop_duplicates("Ticker").set_index("Ticker")
    common = [ticker for ticker in prices.columns if ticker in holdings.index]
    return prices[common], holdings


def yahoo_spark_quotes(symbols: list[str]) -> dict[str, dict[str, object]]:
    quotes: dict[str, dict[str, object]] = {}
    progress = st.progress(0, text="正在刷新 Yahoo 最新报价...")
    total_chunks = max(1, math.ceil(len(symbols) / 20))

    for chunk_index, start in enumerate(range(0, len(symbols), 20), start=1):
        chunk = symbols[start : start + 20]
        encoded = urllib.parse.quote(",".join(chunk), safe=",")
        url = f"https://query1.finance.yahoo.com/v7/finance/spark?symbols={encoded}&range=5d&interval=1d"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            progress.progress(chunk_index / total_chunks, text=f"部分报价失败，继续刷新 {chunk_index}/{total_chunks}")
            continue

        for item in payload.get("spark", {}).get("result", []):
            symbol = item.get("symbol")
            responses = item.get("response") or []
            if not symbol or not responses:
                continue
            meta = responses[0].get("meta") or {}
            price = meta.get("regularMarketPrice")
            market_time = meta.get("regularMarketTime")
            if price is None:
                close = responses[0].get("indicators", {}).get("quote", [{}])[0].get("close", [])
                price = next((value for value in reversed(close) if value is not None), None)
            if price is None:
                continue
            quotes[symbol] = {
                "price": float(price),
                "marketTime": int(market_time) if market_time else None,
                "currency": meta.get("currency", "USD"),
            }
        progress.progress(chunk_index / total_chunks, text=f"正在刷新 Yahoo 最新报价 {chunk_index}/{total_chunks}")
        time.sleep(0.05)

    progress.empty()
    return quotes


def secret_value(name: str, default: str | None = None) -> str | None:
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default


def trigger_history_update() -> tuple[bool, str]:
    token = secret_value("GITHUB_TOKEN")
    repository = secret_value("GITHUB_REPOSITORY", DEFAULT_GITHUB_REPOSITORY)
    workflow = secret_value("GITHUB_WORKFLOW", DEFAULT_GITHUB_WORKFLOW)
    ref = secret_value("GITHUB_REF", DEFAULT_GITHUB_REF)

    if not token:
        return (
            False,
            "未配置 GITHUB_TOKEN。请在 Streamlit Cloud 的 App settings -> Secrets 中添加后再使用网页按钮。",
        )

    url = f"https://api.github.com/repos/{repository}/actions/workflows/{workflow}/dispatches"
    payload = json.dumps({"ref": ref}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "streamlit-spy-momentum-monitor",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            if response.status == 204:
                return True, "已触发 GitHub Actions 后台更新。通常需要几分钟完成。"
            return False, f"GitHub 返回非预期状态：{response.status}"
    except Exception as exc:
        return False, f"触发失败：{exc}"


def updated_prices_with_quotes(prices: pd.DataFrame, quotes: dict[str, dict[str, object]]) -> tuple[pd.DataFrame, str]:
    quote_times = [quote["marketTime"] for quote in quotes.values() if quote.get("marketTime")]
    if quote_times:
        latest_date = datetime.fromtimestamp(max(quote_times), tz=timezone.utc).date().isoformat()
    else:
        latest_date = prices.index[-1].date().isoformat()

    latest_ts = pd.Timestamp(latest_date)
    updated = prices.copy()
    if latest_ts not in updated.index:
        updated.loc[latest_ts] = updated.iloc[-1]
    for symbol, quote in quotes.items():
        if symbol in updated.columns:
            updated.loc[latest_ts, symbol] = quote["price"]
    return updated.sort_index(), latest_date


def last_month_end(index: pd.DatetimeIndex) -> pd.Timestamp:
    dates = list(pd.Series(index, index=index).groupby(index.to_period("M")).max().values)
    if dates and pd.Timestamp(dates[-1]) == index[-1] and index[-1].day < 25:
        dates = dates[:-1]
    return pd.Timestamp(dates[-1])


def composite_momentum(prices: pd.DataFrame, date: pd.Timestamp, top_n: int) -> pd.DataFrame:
    history = prices.loc[:date]
    if len(history) < 253:
        return pd.DataFrame()

    windows = [21, 63, 126, 252]
    raw = pd.DataFrame({f"mom_{window}": history.iloc[-1] / history.shift(window).iloc[-1] - 1 for window in windows})
    raw["vol_63"] = history.pct_change().rolling(63).std().iloc[-1] * math.sqrt(252)
    raw = raw.dropna()
    if len(raw) < top_n:
        return pd.DataFrame()

    score = pd.Series(0.0, index=raw.index)
    for col in [f"mom_{window}" for window in windows]:
        std = raw[col].std()
        if std:
            score += (raw[col] - raw[col].mean()) / std
    vol_std = raw["vol_63"].std()
    if vol_std:
        score -= 0.5 * (raw["vol_63"] - raw["vol_63"].mean()) / vol_std

    raw["score"] = score
    raw = raw.sort_values("score", ascending=False)
    raw["rank"] = range(1, len(raw) + 1)
    return raw.head(top_n)


def build_positions(
    scores: pd.DataFrame,
    prices: pd.DataFrame,
    holdings: pd.DataFrame,
    date: pd.Timestamp,
    capital: float,
    top_n: int,
) -> tuple[pd.DataFrame, float, float]:
    target_value = capital / top_n
    rows = []
    invested = 0.0

    for symbol, row in scores.iterrows():
        price = float(prices.loc[date, symbol])
        shares = int(target_value // price) if price > 0 else 0
        market_value = shares * price
        invested += market_value
        name = str(holdings.loc[symbol].get("Description", "")) if symbol in holdings.index else ""
        spy_weight = float(holdings.loc[symbol].get("weight", 0)) if symbol in holdings.index else 0
        rows.append(
            {
                "排名": int(row["rank"]),
                "代码": symbol,
                "名称": name,
                "价格": price,
                "目标权重": 1 / top_n,
                "目标金额": target_value,
                "建议股数": shares,
                "实际金额": market_value,
                "剩余差额": target_value - market_value,
                "综合分": float(row["score"]),
                "1M动量": float(row["mom_21"]),
                "3M动量": float(row["mom_63"]),
                "6M动量": float(row["mom_126"]),
                "12M动量": float(row["mom_252"]),
                "63日波动率": float(row["vol_63"]),
                "SPY权重": spy_weight / 100,
            }
        )

    return pd.DataFrame(rows), invested, capital - invested


def format_positions(frame: pd.DataFrame) -> pd.io.formats.style.Styler:
    return frame.style.format(
        {
            "价格": "${:,.2f}",
            "目标权重": "{:.1%}",
            "目标金额": "${:,.0f}",
            "实际金额": "${:,.0f}",
            "剩余差额": "${:,.0f}",
            "综合分": "{:.2f}",
            "1M动量": "{:.1%}",
            "3M动量": "{:.1%}",
            "6M动量": "{:.1%}",
            "12M动量": "{:.1%}",
            "63日波动率": "{:.1%}",
            "SPY权重": "{:.2%}",
        }
    )


def main() -> None:
    prices, holdings = load_data()
    cache_date = prices.index[-1].date()
    today = pd.Timestamp.utcnow().date()
    cache_age_days = max(0, (today - cache_date).days)

    st.title("SPY 持仓股 Top10 综合动量监控")
    st.caption(
        "月度轮动策略：每月最后一个交易日，选择 SPY 当前持仓股中综合动量排名前 N 的股票，等权持有。"
        "页面打开时会刷新最新报价；仓库里的历史价格文件只用于计算 1M/3M/6M/12M 动量窗口。"
    )

    with st.sidebar:
        st.header("参数")
        capital = st.number_input("当前总资产（美元）", min_value=1_000.0, value=100_000.0, step=1_000.0)
        top_n = st.selectbox("持仓数量", [5, 10, 15, 20], index=1)
        refresh = st.button("刷新最新报价", type="primary", use_container_width=True)
        st.divider()
        st.write(f"股票池数量：{len(prices.columns)}")
        st.write(f"交易成本假设：{TRADE_COST:.2%} 换手额")
        st.info(
            "最新价格会在打开页面或点击刷新时实时拉取。"
            "历史价格缓存不是当前报价，而是为了避免每次打开页面都重新下载 500 只股票的一年以上历史K线。"
        )
        st.divider()
        st.subheader("历史缓存")
        if st.button("触发后台更新历史缓存", use_container_width=True):
            ok, message = trigger_history_update()
            if ok:
                st.success(message)
            else:
                st.warning(message)
                st.caption(
                    "需要一个 GitHub fine-grained token，至少允许该仓库的 Actions: Read and write。"
                    "配置到 Streamlit Secrets 后，按钮会触发 update-history.yml。"
                )

    if "quotes" not in st.session_state or refresh:
        quotes = yahoo_spark_quotes(list(prices.columns))
        st.session_state["quotes"] = quotes
        st.session_state["quote_refreshed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    else:
        quotes = st.session_state["quotes"]

    live_prices, quote_date = updated_prices_with_quotes(prices, quotes)
    rebalance_date = last_month_end(live_prices.index)
    latest_date = live_prices.index[-1]

    rebalance_scores = composite_momentum(live_prices, rebalance_date, top_n)
    latest_scores = composite_momentum(live_prices, latest_date, top_n)
    rebalance_positions, invested, cash = build_positions(
        rebalance_scores,
        live_prices,
        holdings,
        rebalance_date,
        capital,
        top_n,
    )
    latest_positions, latest_invested, latest_cash = build_positions(
        latest_scores,
        live_prices,
        holdings,
        latest_date,
        capital,
        top_n,
    )

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("最近月末调仓日", rebalance_date.date().isoformat())
    col2.metric("最新报价日期", quote_date)
    col3.metric("成功报价数", f"{len(quotes)} / {len(prices.columns)}")
    col4.metric("历史缓存截至", cache_date.isoformat(), delta=f"{cache_age_days} 天前")
    col5.metric("刷新时间", st.session_state.get("quote_refreshed_at", "--"))

    tab1, tab2, tab3 = st.tabs(["月度策略应持仓", "今日重算候选", "说明"])

    with tab1:
        st.subheader("月度策略应持仓")
        m1, m2, m3 = st.columns(3)
        m1.metric("目标投入", f"${invested:,.0f}")
        m2.metric("剩余现金", f"${cash:,.0f}")
        m3.metric("单股目标金额", f"${capital / top_n:,.0f}")
        st.dataframe(format_positions(rebalance_positions), use_container_width=True, hide_index=True)

    with tab2:
        st.subheader("今日重算候选")
        st.caption("这是用最新报价行重算的候选名单，用于观察，不代表月度策略每天调仓。")
        m1, m2, m3 = st.columns(3)
        m1.metric("目标投入", f"${latest_invested:,.0f}")
        m2.metric("剩余现金", f"${latest_cash:,.0f}")
        m3.metric("单股目标金额", f"${capital / top_n:,.0f}")
        st.dataframe(format_positions(latest_positions), use_container_width=True, hide_index=True)

    with tab3:
        st.markdown(
            """
            **综合动量公式**

            `1个月动量 + 3个月动量 + 6个月动量 + 12个月动量 - 0.5 * 近63日波动率惩罚`

            每一项先在股票池内做横截面标准化，再合成综合分。

            **实盘口径**

            - 月度策略默认使用最近一次完整月末的动量排名。
            - 页面首次打开和刷新按钮都会重新拉取 Yahoo 最新报价，并重算月度持仓和今日候选。
            - `spy_holdings_prices.csv` 是历史动量窗口缓存，不是当前报价缓存。
            - 股数按整股向下取整，剩余金额显示为现金。
            - 当前股票池使用本项目内的 `spy_current_stock_holdings.csv`，属于当前成分股口径，存在幸存者偏差。
            """
        )


if __name__ == "__main__":
    main()
