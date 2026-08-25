<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="docs/logo-stacked.svg">
    <img src="docs/logo-stacked-dark.svg" width="280" alt="Zintopia">
  </picture>
</p>

<p align="center">
  <b>English</b>
  ·
  <a href="README.zh.md">中文</a>
</p>

# Zintopia

Local US-stock research terminal: quotes, charts, movers, watchlist, market-wide news, a stock portfolio simulator, and optional LLM / heuristic analysis.

Open [http://localhost:5173](http://localhost:5173) after starting the app. Click a ticker (or search) to load its quote and chart. **Market News** sits under the session clock and does not change when you switch names. Use **Stock portfolio** to create a paper fund (name + starting dollars), simulate share trades, or attach a simple automatic strategy. Options are not supported. Deep analysis loads when you select a stock. The LLM research dialog stays on the ticker page (starter chips plus follow-ups) when a key is set. Click the header logo to reload.

This is a research UI, not a broker. **Not financial advice.** Data can be delayed, incomplete, or wrong.

## Features

- GitHub-style dark UI: watchlist, US movers (gainers / losers / active / screen), index strip (SPY, QQQ, DIA, IWM, VIX)
- Watchlist persisted in the browser (`localStorage`); add with ★, remove with ×; sort by name or % day
- Search by ticker or name
- OHLCV chart in Eastern Time; default range is **1D** (`1h` / `3h` / `1d` / `5d` / `1mo` / `3mo` / `6mo` / `1y` / `5y`); optional **vs** overlay (off by default, ticker defaults to SPY)
- **Market News**: session-wide Yahoo tape (three-column grid); polls every 60s (`ZINTOPIA_NEWS_REFRESH_SEC`, or `ZINTOPIA_MARKET_NEWS_REFRESH_SEC`). **Breaking** only if the headline contains words such as breaking / just in / flash / developing / alert; **Alert** is a separate severe-phrase heuristic
- Daily TradingView technical rating, Yahoo **ticker** news (same 60s poll, or `ZINTOPIA_TICKER_NEWS_REFRESH_SEC`), company profile, financials, and ownership / SEC filings (10-K / 10-Q / 8-K, holders, short interest)
- **Stock portfolio simulation**: virtual funds that buy and sell **shares** of US stocks and ETFs, with optional auto strategies, live NAV / P/L, and a **Vibe dialog** (Yahoo last/news + TradingView daily TA, then an LLM review you can follow up in the same conversation). Requires `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. No options. Not a broker.
- **Deep analysis**: insider Form 4 flow, option volume / put-call, official Senate and House **periodic transaction reports** (not live holdings), analyst targets, headlines, and a heuristic stance (`ACCUMULATE` … `AVOID`)
- **LLM research dialog**: type a question or use the BUY / SELL / LONG CALL / LONG PUT starter, with macro context (SPY, QQQ, DIA, IWM, VIX) via OpenAI or Anthropic, then follow-ups in the same conversation. Requires a key; use **Stop** to cancel an in-flight reply.

Clicking a stock shows the header quote immediately when the ticker is already on the strip, watchlist, or movers. Charts and quotes cache briefly so switching back is faster.

## Quick start

Needs [Python 3.12+](https://www.python.org/), [uv](https://docs.astral.sh/uv/), Node.js, and npm.

```bash
chmod +x start.sh
./start.sh
```

| | URL |
|---|---|
| UI | http://localhost:5173 |
| API docs | http://localhost:8000/docs |

`start.sh` loads `.env` if present, creates `backend/.venv`, installs Python and npm deps, starts FastAPI on port 8000 (`--host ::`), then Vite on 5173 (Vite proxies `/api` to the backend).

On macOS, `start.sh` sets `ZINTOPIA_BIND_INTERFACE=en0` so outbound HTTPS can bind to Wi-Fi when automatic source-address selection fails (`Errno 49` / “Can't assign requested address”). Override with `ZINTOPIA_BIND_INTERFACE=` or `ZINTOPIA_BIND_IP=`. `FINTOPIA_*` and `UTOPIA_*` names still work as aliases.

## Stock portfolio simulation

The **Stock portfolio** tab is a local paper-trading sandbox for **US stocks and ETFs**. It does not support options (calls, puts, or spreads). Nothing is sent to a broker. During regular hours, fills and NAV use the same quote stack as the research UI. When the NYSE cash session is closed (Eastern time), NAV and paper fills mark to Yahoo **pre-market** (4:00–9:30) or **after hours** (16:00–20:00); overnight and weekends use the last extended print.

1. Open **Stock portfolio** in the header.
2. Create a fund with a name and starting cash (for example `100000`).
3. Place simulated **buy** / **sell** share orders by quantity or dollar amount, or attach an automatic strategy and turn **Auto** on.
4. Watch NAV, cash, unrealized P/L, max drawdown, the NAV chart, holdings, and the trade log.
5. Use the **Vibe dialog** on the fund page. **Analyze fund** posts a structured review; type in the box to follow up on the same conversation (Yahoo quotes/news and daily TA, same US stack as [Vibe-Trading MCP](https://github.com/HKUDS/Vibe-Trading)). Requires `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`. Click a ticker in the notes to load it into the paper order ticket.

Performance (NAV chart and marked-to-market P/L) refreshes every 30 seconds (`ZINTOPIA_CHART_REFRESH_SEC`). Auto strategies try a step every hour while `./start.sh` is running (`ZINTOPIA_STRATEGY_INTERVAL_SEC`, default `3600`). Use **Run one step now** to force a strategy tick immediately.

| Strategy | What it does |
|---|---|
| Manual | You place paper buy/sell orders in shares (no options). |
| Buy & hold | Invests remaining cash in one ticker and holds. |
| SMA crossover | Buys when SMA20 > SMA50; sells on a cross down. |
| Momentum | Rotates into the top 3 US gainers, equal weight. |
| RSI mean reversion | Buys ~25% of cash when RSI < 30; sells when RSI > 70. |

Funds are stored locally in `~/.zintopia/portfolios.json` (outside the git repo). The same directory holds the Congress PTR cache (`congress_ptr.json`). Override with `ZINTOPIA_DATA_DIR`. Deleting a fund in the UI removes it. Restarting the app does not reset paper cash or trades.

This is research / simulation only, and **shares only** (no options). **Not financial advice.** You can lose real money if you copy these ideas in a live account.

## Optional keys

The app runs with no keys. Unsigned TradingView scanner quotes are typically **~15 minutes delayed**.

Copy the template and fill in what you use:

```bash
cp .env.example .env
```

| Variable | Purpose |
|---|---|
| `ZINTOPIA_LIVE_REFRESH_SEC` | Selected ticker **price** poll interval in seconds (default `10`). Daily TA stays on-demand. |
| `ZINTOPIA_CHART_REFRESH_SEC` | Stock charts, NAV chart, and portfolio performance poll in seconds (default `30`). |
| `ZINTOPIA_NEWS_REFRESH_SEC` | Market news + ticker news poll in seconds (default `60`). |
| `ZINTOPIA_MARKET_NEWS_REFRESH_SEC` | Market news tape only (falls back to `ZINTOPIA_NEWS_REFRESH_SEC`). |
| `ZINTOPIA_TICKER_NEWS_REFRESH_SEC` | Selected-ticker news only (falls back to `ZINTOPIA_NEWS_REFRESH_SEC`). |
| `ZINTOPIA_STRATEGY_INTERVAL_SEC` | How often auto paper strategies try a step while the server is up (default `3600` = 1 hour). |
| `ZINTOPIA_DATA_DIR` | Local JSON dir for paper funds and the Congress PTR cache (default `~/.zintopia`). |
| `ZINTOPIA_HTTP_POOL_SIZE` | Keep-alive connection pool for outbound quote HTTP (default `20`, clamp 2–128). |
| `POLYGON_API_KEY` or `MASSIVE_API_KEY` | Last-trade snapshots (realtime when the plan allows) |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | LLM research and Vibe dialogs (default model `gpt-4.1`) |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` | LLM research and Vibe dialogs (default `claude-opus-4-20250514`) |
| `LLM_PROVIDER` | `auto` (OpenAI first if both keys are set), `openai`, or `anthropic` |

Free Polygon signup: https://polygon.io/dashboard/signup

A free Polygon plan may still reject some snapshot endpoints (`NOT_AUTHORIZED`). Quotes then fall back automatically.

## Data sources

| Panel | Source |
|---|---|
| Quotes / indices / watchlist | Polygon snapshot (if keyed) → TradingView scanner → yfinance download → Yahoo ticker → Stooq |
| Charts | Yahoo Finance `yf.download` |
| Movers | TradingView scanner, then Polygon gainers/losers if the plan allows, then Yahoo day gainers / losers / most actives |
| Daily TA | tradingview-ta |
| Profile, ticker news, financials, ownership / filings, insiders, options, analyst targets | Yahoo Finance (`yfinance`) |
| Market news | Yahoo Finance RSS (`/news/rssindex` and GSPC headlines). Breaking is title-keyword only (not recency). Alert tags are a severe-phrase heuristic, not a news desk |
| Senate / House PTR trades | Official STOCK Act filings: House Clerk `YYYYFD.zip` + PTR PDFs; Senate eFD search (`efdsearch.senate.gov`). These are **trades**, not live holdings; filers have up to 45 days to disclose. Cached in `~/.zintopia/congress_ptr.json` (refreshed in the background, default 120-day lookback) |
| LLM research / Vibe dialogs | OpenAI or Anthropic when a key is set |
| Paper stock-portfolio marks / fills | Regular hours: same quote stack as `/api/quote`. When the NYSE cash session is closed (Eastern time): Yahoo **pre-market** (4:00–9:30), **after hours** (16:00–20:00), or last extended print overnight/weekend. SMA strategies use Yahoo `yf.download` history |

Do not treat unsigned TradingView or Yahoo prints as exchange-realtime.

## API

| Route | Role |
|---|---|
| `GET /api/health` | Liveness, Polygon flag, LLM provider flags, NYSE session (`market`) |
| `GET /api/network-test` | Outbound HTTPS diagnostic |
| `GET /api/indices` | SPY, QQQ, DIA, IWM, VIX |
| `GET /api/snapshot` | Indices + mover boards |
| `GET /api/quote/{symbol}` | One quote |
| `GET /api/quotes?symbols=AAPL,MSFT` | Watchlist |
| `GET /api/movers?kind=gainers\|losers\|active` | US stocks |
| `GET /api/history/{symbol}?range=1d\|5d\|1mo\|3mo\|6mo\|1y\|5y` | OHLCV |
| `GET /api/profile/{symbol}` | Company profile (includes beta, float, short interest when Yahoo has it) |
| `GET /api/market-news` | Session-wide Yahoo headlines (`limit`, default 24) |
| `GET /api/news/{symbol}` | Ticker headlines |
| `GET /api/fundamentals/{symbol}` | Income, cash flow, balance sheet, EPS vs estimate |
| `GET /api/ownership/{symbol}` | Holders, short interest, SEC 10-K / 10-Q / 8-K links |
| `GET /api/peers/{symbol}` | Same-sector peers |
| `GET /api/screener` | Universe screen (sector, cap, PE, RSI, % change) |
| `GET /api/ta/{symbol}` | Daily TA summary |
| `GET /api/search?q=` | Symbol search |
| `GET /api/deep/{symbol}` | Insiders, options, official House/Senate PTRs, news, forecast, heuristic suggestion |
| `POST /api/llm-advice/{symbol}` | Start LLM research conversation (BUY/SELL/LONG CALL/LONG PUT) |
| `POST /api/llm-advice/{symbol}/chat` | Follow-up in the same `conversation_id` |
| `GET /api/portfolios` | Stock portfolio summaries (marked to market) |
| `POST /api/portfolios` | Create fund `{name, amount}` |
| `GET /api/portfolios/{id}` | Holdings, trades, NAV snapshots |
| `DELETE /api/portfolios/{id}` | Delete fund |
| `POST /api/portfolios/{id}/orders` | Paper buy/sell (`shares` or `notional`) |
| `PUT /api/portfolios/{id}/strategy` | `manual` / `buy_hold` / `sma_cross` / `momentum` / `rsi_reversion` |
| `POST /api/portfolios/{id}/vibe` | Start Vibe paper-fund conversation (Yahoo + daily TA, then LLM) |
| `POST /api/portfolios/{id}/vibe/chat` | Follow-up on the same `conversation_id` |

## Repo layout

```
backend/app.py           FastAPI app
backend/newsfeed.py      Yahoo ticker news + market RSS tape
backend/ownership.py     Holders, short interest, SEC filings
backend/congress_ptr.py  House Clerk + Senate eFD PTR cache
backend/portfolios.py    Stock portfolio simulation (shares only, no options)
backend/llm_advice.py    OpenAI / Anthropic calls
backend/requirements.txt
frontend/                Vite + React + Lightweight Charts
start.sh                 Dev launcher (loads .env if present)
.env.example             Key placeholders — copy to .env locally
~/.zintopia/             Local paper funds + PTR cache (not in git)
```

## Secrets

Never commit `.env` or API keys. `.env` is gitignored. `.env.example` only shows empty variable names and example model ids.

Deep analysis and LLM output are research aids over public feeds. You can lose money.

## License

[MIT](LICENSE) © 2026 Zidong
