# SPY Top Momentum Monitor

Streamlit app for monitoring a monthly SPY holdings momentum rotation strategy.

## Strategy

- Universe: current SPY holdings in `spy_current_stock_holdings.csv`
- Ranking: composite momentum across 1M, 3M, 6M, and 12M returns, with a 63-day volatility penalty
- Default portfolio: Top 10 names, equal weight
- Refresh: pulls latest Yahoo spark quotes and recalculates current rankings

## Deploy

Deploy on Streamlit Cloud with:

```text
streamlit_app.py
```
