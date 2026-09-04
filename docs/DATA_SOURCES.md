# Zintopia data sources

What this local terminal actually calls, which env keys it uses, and the **paid / subscription products** that exist for the same vendor. Prices and plan names are **as of August 2026**; vendors change them often — confirm on the official page before buying.

Zintopia is a research UI, not a broker. **Not financial advice.** Unsigned TradingView and Yahoo prints are **not** exchange-realtime.

**Not in this app:** OpenInsider, Robinhood, Vibe-Trading MCP, Unusual Whales, and other agent MCPs. Those are Cursor tools. The website never logs into a broker and never spawns those MCPs.

---

## Quick map

| What you see | Upstream used today | Key / login |
|---|---|---|
| Last price (header, watchlist, indices, paper fills in RTH) | Polygon snapshot if keyed, else TradingView scanner, else Yahoo `yfinance`, else Stooq | `POLYGON_API_KEY` or `MASSIVE_API_KEY` optional |
| Paper NAV when NYSE cash session is closed | Yahoo pre-market / after-hours last | none |
| OHLCV chart | Yahoo `yf.download`; daily/weekly bars fall back to Polygon aggregates if Yahoo 429s | Polygon optional |
| Paper strategies (SMA, RSI, 200-day, dual momentum, sector rotation) | Yahoo daily history (same cache as charts); Polygon daily aggs if Yahoo is rate-limited | Polygon optional |
| Movers, screener, search, peers | TradingView scanner `POST https://scanner.tradingview.com/america/scan` | none (unsigned) |
| Daily TA rating | `tradingview-ta` → `https://scanner.tradingview.com/{screener}/scan` | none (unsigned) |
| Profile, financials, holders, short interest, Form 4, options, analyst targets, ticker news | Yahoo via `yfinance` (`query1` / `query2.finance.yahoo.com`) | none |
| Market news tape | Yahoo RSS | none |
| SEC 10-K / 10-Q / 8-K links | Yahoo `get_sec_filings()` (URLs usually point at EDGAR) | none |
| Congress PTR trades | House Clerk ZIP/PDFs + Senate eFD | none |
| LLM research + Vibe dialog | OpenAI Chat Completions and/or Anthropic Messages | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` |
| Chart widget | TradingView **Lightweight Charts** npm library (renders bars we already fetched; no TV market-data feed) | none |

---

## 1. Polygon.io / Massive

- **Official site:** [massive.com](https://massive.com) (Polygon.io rebrand). Keys still work as `POLYGON_API_KEY` or `MASSIVE_API_KEY`. Base URL: `MASSIVE_API_BASE_URL` or `https://api.polygon.io`.
- **Signup:** [polygon.io/dashboard/signup](https://polygon.io/dashboard/signup) / Massive dashboard.
- **What Zintopia calls**
  - `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` — last trade + session OHLC
  - `GET /v2/snapshot/locale/us/markets/stocks/{gainers\|losers}` — movers fallback
  - `GET /v2/aggs/ticker/{ticker}/prev` — previous-day bar (quote last resort)
  - `GET /v2/aggs/ticker/{ticker}/range/1/{day\|week}/{from}/{to}` — daily/weekly history when Yahoo is rate-limited
- **Used for:** quotes/indices first if a key is set; movers if the plan allows; chart/strategy history fallback.
- **Delay in this app:** snapshot is labeled `realtime` when the call succeeds. A **free** plan often returns `NOT_AUTHORIZED` on snapshot; quotes then fall back to TradingView/Yahoo. Do not assume the key means live NBBO.

### Paid / subscription tiers (stocks, individual)

Confirm live: [massive.com/pricing](https://massive.com/pricing).

| Plan | List price (monthly, Aug 2026) | Latency | Snapshot / what matters here |
|---|---|---|---|
| Stocks Basic | $0 | End of day | 5 calls/min, 2y history, **no** snapshot on typical free accounts |
| Stocks Starter | $29 | 15-minute delayed | Unlimited calls, snapshot, 5y history, WebSockets (delayed) |
| Stocks Developer | $79 | 15-minute delayed | Starter + trades, 10y history |
| Stocks Advanced | $199 | **Real-time** | Snapshot + quotes + 20y+ history + financials |
| Business | Custom | Real-time + SLA | Company use, not the individual plans |

**Separate paid add-ons (not wired in Zintopia):** Options, Indices, Currencies, Futures (each has its own plan ladder); Financials & Ratios alone (~$29/mo); partner feeds (Benzinga news, TMX events, ETF Global, NYSE imbalances, etc.). Options chain in this app comes from **Yahoo**, not Polygon Options.

A ChatGPT / Claude subscription does **not** include a Polygon key.

---

## 2. TradingView scanner (unsigned)

- **Library:** `tradingview-screener` → `POST https://scanner.tradingview.com/america/scan` with `Origin`/`Referer` of `https://www.tradingview.com`.
- **What Zintopia uses it for:** quotes when Polygon is missing or unauthorized; movers; `/api/search`; `/api/screener`; `/api/peers`; overlay fields (P/E, RSI, sector, earnings date) on watchlist/screen rows.
- **Auth in this repo:** none. No `sessionid` cookie. Typical delay **~15 minutes**.
- **Not used:** TradingView chart iframe, broker WebSocket, Pine, or official “TradingView Data API” SaaS.

### Paid products that exist (Zintopia does not subscribe)

These improve **tradingview.com in a browser**, not this app, unless we later wire a logged-in scanner cookie (we do not).

| Product | What it is | Typical 2026 list price | Effect on Zintopia today |
|---|---|---|---|
| TradingView Basic | Free website | $0 | Same unsigned scanner we hit |
| Essential / Plus / Premium / Ultimate | Website features (charts per tab, alerts, ads off) | ~$15–$240/mo ([tradingview.com/pricing](https://www.tradingview.com/pricing/)) | **None** — we do not send your TV login |
| Exchange real-time **data packages** (NASDAQ, NYSE, Arca, …) | Extra fee on a TV account; exchanges charge TV | Often a few USD/mo per US tape for non-pro; pro rates much higher. See [How to purchase additional market data](https://www.tradingview.com/support/solutions/43000471705-how-to-purchase-additional-market-data/) | **None** unless scanner cookies are added later |
| Third-party “TradingView API” hosts | Unofficial paid proxies of the same scanner | Varies | Not used |

Passing a logged-in `sessionid` into `tradingview-screener` can reduce delay toward what you see on the website. That is still unofficial, can violate TV ToS, and is **not implemented**.

---

## 3. TradingView technical analysis (`tradingview-ta`)

- **Library:** `tradingview-ta` → `https://scanner.tradingview.com/{screener}/scan` (screener `america`), optional `https://symbol-search.tradingview.com/symbol_search`.
- **What Zintopia uses:** `GET /api/ta/{symbol}` daily (and other intervals) summary / oscillators / moving averages. Same unsigned delay as the scanner.
- **Paid:** same TradingView website + data-package story as above. No separate TA API key. A paid TV plan does not change this endpoint without cookies.

---

## 4. Yahoo Finance via `yfinance` (unofficial)

Yahoo **shut the official public Finance API in 2017**. There is no Yahoo API key in `.env`. Zintopia uses the community library `yfinance`, which talks to Yahoo’s **undocumented** JSON/chart hosts:

- `https://query1.finance.yahoo.com` and `https://query2.finance.yahoo.com` (crumb, chart, quoteSummary, options, screeners, fundamentals timeseries)
- `https://finance.yahoo.com` and `https://fc.yahoo.com` (cookie / crumb bootstrap)
- Session uses `curl_cffi` Chrome impersonation when that package is available

**What Zintopia uses it for**

| Feature | yfinance / Yahoo surface |
|---|---|
| Quote fallback | `Ticker.fast_info`, `history`, `info` |
| Charts + strategy history | `yf.download` / `Ticker.history` (`RANGE_TO_YF` intervals) |
| Pre/post paper marks | `yf.download(..., prepost=True)` and `info` pre/post fields |
| Profile | `Ticker.info` |
| Financials | income / cash flow / balance sheet + earnings history |
| Ownership | institutional holders, short interest fields on `info`, `get_sec_filings()` |
| Insiders | `insider_transactions` (Form 4 via Yahoo) |
| Options | `option_chain` for the next few expiries |
| Analyst targets | `info` target mean/high/low |
| Ticker news | `Ticker.news` |
| Movers last resort | `yf.screen("day_gainers" \| "day_losers" \| "most_actives")` |
| Network probe | `GET https://query1.finance.yahoo.com/v8/finance/chart/AAPL?...` |

Yahoo **rate-limits** (`YFRateLimitError` / empty history). Daily bars are cached ~15 minutes; a 429 can fall back to Polygon aggregates if a key is set.

### Paid products that exist (none of them feed this app)

| Product | What it is | Typical price | Effect on Zintopia |
|---|---|---|---|
| Yahoo Finance **Plus** (Bronze / Silver / Gold) | Consumer website: ad-free, research, AlphaSpace charts, longer downloadable history on the site | About $10 / $25 / $50 per month ([finance.yahoo.com/plus](https://finance.yahoo.com/plus/select-plan/)) | **None** — Plus is not an API key |
| RapidAPI “Yahoo Finance” listings | Third-party wrappers of similar unofficial endpoints | Free tier + paid request packs | Not used |
| Licensed vendors (Polygon, Finnhub, Tiingo, Intrinio, EODHD, …) | Official APIs meant to replace Yahoo scrape | Free + paid plans | Only Polygon is wired, and only as above |

Do not treat `yfinance` as a licensed exchange feed. Redistribution and commercial display are restricted by Yahoo and the exchanges.

---

## 5. Yahoo Finance RSS (market tape)

- `https://finance.yahoo.com/news/rssindex`
- `https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US`

Used by `GET /api/market-news`. Breaking / Alert tags are **title heuristics**, not a news desk.

**Paid:** Yahoo Plus premium newsfeed is a website feature, not these RSS URLs. Benzinga and similar newswires are sold elsewhere (including as Polygon partner data); Zintopia does not subscribe.

---

## 6. Stooq

- Last-quote CSV: `https://stooq.com/q/l/?s={sym.us}&f=sd2t2ohlcv&h=&e=csv`
- Daily CSV fallback: `https://stooq.com/q/d/l/?s={sym.us}&i=d`

**What Zintopia uses:** last-resort **quote** only (after Polygon, TradingView, Yahoo). Not used for charts.

**Paid:** Stooq itself publishes **no paid API tier**. Access is free with an unpublished daily quota; some 2026 write-ups say a CAPTCHA API key is required on their download forms. This app does not send a Stooq apikey. Third-party hosts that wrap Stooq (credit-priced APIs) are not used.

---

## 7. U.S. House Clerk (STOCK Act PTR)

- Index ZIP: `https://disclosures-clerk.house.gov/public_disc/financial-pdfs/{year}FD.zip`
- PTR PDF: `https://disclosures-clerk.house.gov/public_disc/ptr-pdfs/{year}/{doc_id}.pdf`

**What Zintopia uses:** periodic transaction **trades** (not live holdings). Cached in `~/.zintopia/congress_ptr.json`. Filers have up to 45 days to disclose.

**Paid:** none. Official public records. Commercial aggregators (Quiver, Capitol Trades, Unusual Whales, …) sell cleaned feeds; the website does not call them.

---

## 8. U.S. Senate eFD

- `https://efdsearch.senate.gov/search/home/`
- `https://efdsearch.senate.gov/search/`
- `https://efdsearch.senate.gov/search/report/data/`
- PTR HTML under `/search/view/...`

Same role as House PTRs. **Paid:** none official. Same aggregator market as above; not used.

---

## 9. SEC EDGAR (indirect)

Filings shown in ownership / deep analysis come through Yahoo `get_sec_filings()`. Links typically open `sec.gov` / EDGAR exhibits.

**Paid:** EDGAR is free (`data.sec.gov`). Paid products (Bloomberg, FactSet, EDGAR Online, …) are not used. Do not hammer EDGAR; Zintopia does not call it in a tight loop.

---

## 10. OpenAI

- **Endpoint used:** `POST https://api.openai.com/v1/chat/completions`
- **Env:** `OPENAI_API_KEY`, optional `OPENAI_MODEL` (default `gpt-4.1`), `LLM_PROVIDER`
- **What Zintopia uses:** ticker LLM research + paper-fund Vibe dialogs (JSON then follow-up chat)

### Paid products

| Product | Billing | Unlocks Zintopia LLM? |
|---|---|---|
| OpenAI **API** (usage tiers 1–5) | Pay per token; rate limits rise with spend. See [platform.openai.com/docs/pricing](https://platform.openai.com/docs/pricing) | **Yes** — this is what `OPENAI_API_KEY` is |
| ChatGPT Free / Go / Plus / Pro / Business | Flat subscription for chat.openai.com | **No** — a Plus login is not an API key |
| Azure OpenAI | Enterprise, per-token | Not wired |

Plus (~$20/mo) and Pro (~$100–$200/mo) do **not** include API quota.

---

## 11. Anthropic

- **Endpoint used:** `POST https://api.anthropic.com/v1/messages` (`anthropic-version: 2023-06-01`)
- **Env:** `ANTHROPIC_API_KEY`, optional `ANTHROPIC_MODEL`, `LLM_PROVIDER`
- **What Zintopia uses:** same LLM surfaces as OpenAI when selected

### Paid products

| Product | Billing | Unlocks Zintopia LLM? |
|---|---|---|
| Anthropic **API** (Console) | Pay per token. See [docs.anthropic.com](https://docs.anthropic.com) / Console billing | **Yes** |
| Claude.ai Free / Pro (~$20/mo) / Max 5x–20x (~$100–$200/mo) / Team / Enterprise | Chat + Claude Code subscriptions | **No** — Pro explicitly does **not** include Console API usage |
| Amazon Bedrock / Google Vertex Claude | Cloud marketplace | Not wired |

---

## 12. Chart library (not a market-data feed)

- npm `lightweight-charts` (TradingView). Draws candles from `/api/history`.
- Footer may still say “Quotes: TradingView scanner…” even when Polygon is first; the **delay disclaimer** is the part that must stay honest.

TradingView’s paid Supercharts subscription is unrelated to this widget.

---

## 13. Local only (no vendor API)

| Store | Path |
|---|---|
| Paper funds + imported snapshots | `~/.zintopia/portfolios.json` (`ZINTOPIA_DATA_DIR`) |
| Watchlist | `~/.zintopia/watchlist.json` (browser `localStorage` is a cache; first load can copy an older origin) |
| Congress PTR cache | `~/.zintopia/congress_ptr.json` |
| Broker import | User-supplied CSV/TSV parsed locally (`broker_import.py`) |

---

## Quote fallback order (RTH)

1. Polygon snapshot (if key present and authorized)
2. TradingView scanner
3. Yahoo `fast_info` / `history` / `info`
4. Yahoo `yf.download` last close
5. Stooq last CSV
6. Polygon previous close (if key present)

When the NYSE cash session is closed (America/New_York), paper marks overlay Yahoo extended-hours last on top of that stack.

---

## Chart / history fallback

1. Yahoo `yf.download` for the selected range
2. On Yahoo rate limit or empty daily/weekly bars: Polygon aggregates (if key present)
3. Else HTTP **429** (or last cached bars if we already had a good chart)

Intraday ranges (`1d` / `5d` / `1mo`) stay on a short cache so the UI can still poll every 30s (`ZINTOPIA_CHART_REFRESH_SEC`). Daily/weekly ranges cache about **15 minutes**. Monte Carlo (`POST /api/monte-carlo`) uses Yahoo **monthly** history (`range=max`), with Polygon monthly aggregates if Yahoo 429s and a key is set.

---

## Env keys that buy data vs keys that do not

| Variable | Buys access to |
|---|---|
| `POLYGON_API_KEY` / `MASSIVE_API_KEY` | Massive/Polygon REST (tier = whatever you subscribed on their dashboard) |
| `OPENAI_API_KEY` | OpenAI Chat Completions (token bill) |
| `ANTHROPIC_API_KEY` | Anthropic Messages (token bill) |
| Yahoo Plus, TradingView Essential+, Claude Pro, ChatGPT Plus | **Nothing in this process** |

---

## Related files

| File | Role |
|---|---|
| `backend/app.py` | Quotes, history, TV scanner, Polygon, Yahoo, Stooq, TA, search, deep |
| `backend/newsfeed.py` | Yahoo ticker news + RSS tape |
| `backend/ownership.py` | Holders, short interest, filings via Yahoo |
| `backend/fundamentals.py` | Statements / EPS via Yahoo |
| `backend/congress_ptr.py` | House + Senate PTR |
| `backend/llm_advice.py` | OpenAI / Anthropic HTTP |
| `backend/portfolios.py` | Paper funds; history via the same cached Yahoo/Polygon helper |
| `.env.example` | Empty key names |
