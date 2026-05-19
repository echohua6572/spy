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

## Deploy On Cloudflare Pages

Cloudflare Pages cannot run the Streamlit Python server directly, so this repo also includes a Pages-native version:

- `public/`: static monitor UI
- `functions/api/quotes.js`: Yahoo quote proxy
- `functions/api/latest-holdings.js`: latest SPY holdings check
- `functions/api/dispatch-update.js`: GitHub Actions trigger for history cache updates

Cloudflare Pages settings:

```text
Build command: npm run build:cloudflare
Build output directory: public
Root directory: /
```

Optional environment variables for the update button:

```text
GITHUB_TOKEN = your fine-grained GitHub token
GITHUB_REPOSITORY = echohua6572/spy
GITHUB_WORKFLOW = update-history.yml
GITHUB_REF = main
```

The build script copies `spy_holdings_prices.csv` and `spy_current_stock_holdings.csv` into `public/data/` during deployment.
