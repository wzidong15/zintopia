---
name: zintopia
description: Builds and extends Zintopia, the US equity terminal (FastAPI + Vite React). Use when changing backend/app.py, the frontend, market-data sources, Polygon/Massive realtime quotes, TradingView screener, yfinance charts, or when the user asks to add panels to the stock website.
---

# Zintopia

Local US-stock visualization app in this repo. Not OpenBB Workspace. Formerly Fintopia.

## Stack

- Backend: `backend/app.py` (FastAPI, port 8000)
- Frontend: `frontend/` (Vite React, port 5173, proxies `/api`)
- Run: `./start.sh` (loads repo-root `.env` if present). Docker: `docker compose up --build` (UI + API at http://localhost:8000; paper funds in volume `zintopia-data`).
- Quote poll: `ZINTOPIA_LIVE_REFRESH_SEC` (default 10; `FINTOPIA_*` / `UTOPIA_*` aliases still work). Chart + portfolio NAV: `ZINTOPIA_CHART_REFRESH_SEC` (default 30). Market tape + ticker news: `ZINTOPIA_NEWS_REFRESH_SEC` (default 60); override with `ZINTOPIA_MARKET_NEWS_REFRESH_SEC` / `ZINTOPIA_TICKER_NEWS_REFRESH_SEC`.
- Stock portfolio marks: Yahoo pre-market / after hours when the NYSE cash session is closed (America/New_York).
- Paper funds persist in `~/.zintopia/portfolios.json` (not in git). Congress PTRs cache in `~/.zintopia/congress_ptr.json`. Optional `ZINTOPIA_DATA_DIR`. First launch renames `~/.fintopia` if present.
- Outbound HTTP uses a process-wide httpx/requests pool (keep-alive; `ZINTOPIA_HTTP_POOL_SIZE`, default 20). Quote sources run sequentially. Do not add per-call `httpx.Client()` / `requests.Session()` / curl on the quote path.

## Data source priority

Full inventory (URLs, keys, paid tiers): `docs/DATA_SOURCES.md`.

Quotes (`/api/quote`, `/api/quotes`, `/api/indices`):

1. **Polygon / Massive** last-trade snapshot — only if `POLYGON_API_KEY` or `MASSIVE_API_KEY` is set (and the plan allows snapshot)
2. TradingView scanner via `tradingview-screener` (~15m delay unsigned)
3. Yahoo `yfinance` fallback, then Stooq

Charts: Yahoo `yf.download`; daily/weekly bars fall back to Polygon aggregates on Yahoo 429. Profile, news, options, filings: Yahoo. Daily TA: `tradingview-ta`. Movers: TradingView scanner, then Polygon gainers/losers if the plan allows, then Yahoo `day_gainers` / `day_losers` / `most_actives`. Congress PTRs: House Clerk + Senate eFD. LLM: OpenAI and/or Anthropic API keys (ChatGPT Plus / Claude Pro do not count).

Do not claim unsigned TV/Yahoo quotes are exchange-realtime. UI footer must stay honest about delay. ChatGPT/Claude/Yahoo Plus/TradingView website subscriptions are not API keys for this process.

## API (do not rename casually)

| Route | Role |
|---|---|
| `GET /api/health` | `polygon: bool` plus source labels and `market` session |
| `GET /api/quote/{symbol}` | One quote |
| `GET /api/quotes?symbols=AAPL,MSFT` | Watchlist |
| `GET /api/indices` | SPY QQQ DIA IWM VIX |
| `GET /api/movers?kind=gainers\|losers\|active` | US stocks |
| `GET /api/history/{symbol}` | OHLCV. Daily/weekly bars cache 15m; Yahoo 429 falls back to Polygon if a key is set, else HTTP 429 |
| `GET /api/profile/{symbol}` | Yahoo fundamentals |
| `GET /api/news/{symbol}` | Yahoo news |
| `GET /api/ta/{symbol}` | TradingView summary |
| `GET /api/search?q=` | Symbol search |
| `GET /api/deep/{symbol}` | Insider (Yahoo Form 4), options (next 3 expiries), Congress PTRs (House Clerk + Senate eFD), news, forecast, research stance |
| `GET /api/snapshot` | Dashboard bundle |
| `GET/POST /api/portfolios` | Stock paper funds (shares only, no options) |
| `POST /api/portfolios/import` | Imported snapshot from a read-only broker CSV/TSV (`cost_basis=mark` import-time price, or `csv` actual basis; no login) |
| `POST /api/portfolios/{id}/orders` | Simulated trades |
| `PUT /api/portfolios/{id}/strategy` | Manual or auto quant strategy (`manual`, `buy_hold`, `trend_200`, `dual_momentum`, `sector_rot`, `rsi_trend`, `sma_cross`, `momentum`, `rsi_reversion`) |
| `POST /api/portfolios/{id}/tick` | Mark-to-market / auto step |
| `POST /api/portfolios/{id}/vibe` | Start Vibe-style paper-fund conversation (Yahoo + daily TA, then LLM) |
| `POST /api/portfolios/{id}/vibe/chat` | Follow-up on the same `conversation_id` (prose, in-memory thread) |
| `POST /api/llm-advice/{symbol}` | Start LLM research conversation (structured BUY/SELL/LONG CALL/LONG PUT) |
| `POST /api/llm-advice/{symbol}/chat` | Follow-up on the same `conversation_id` (prose, in-memory thread) |

## UI conventions

GitHub dark canvas `#0D1117` with logo greens: `--accent` / `--up` `#56D364`, `--down` `#F85149`, text `#E6EDF3`. Header uses the dark horizontal lockup (Futura Bold Z mark + Zin/topia wordmark). No emoji. Keep the three-column layout (watchlist + movers | chart | TA/news).

## MCP / keys

- Polygon MCP is `polygon` in `~/.cursor/mcp.json` (binary `mcp_massive`; `POLYGON_API_KEY` still works).
- Never commit API keys. Put them in `~/.cursor/mcp.json` env and repo `.env` (gitignored).
- Do not add new MCPs (OpenBB, Unusual Whales, WeChat, etc.) unless the user approves.
- OpenInsider / Robinhood / Vibe-Trading are agent tools. The website can run a Vibe-style paper-fund review via `POST /api/portfolios/{id}/vibe` using Yahoo + TradingView TA (same US stack as those MCP tools); it does not spawn `vibe-trading-mcp`. Broker import on the portfolio page is a local CSV/TSV snapshot only — do not wire unofficial Robinhood login, IBKR passwords, or Plaid/SnapTrade unless the user approves.

## When extending

Prefer extending existing `/api/*` routes and `frontend/src/App.tsx`. Compute TA from OHLCV in-process when possible. If Polygon key is missing, keep TV+Yahoo working.
