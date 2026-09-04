# Zintopia 数据来源

本机终端实际调用的接口、环境变量，以及同一家厂商的 **付费 / 订阅产品**。价格与套餐名以 **2026 年 8 月** 公开页为准，会变，购买前请看官网。

Zintopia 是研究界面，不是券商。**不构成投资建议。** 未登录的 TradingView 与 Yahoo 报价 **不是** 交易所实时行情。

**本网站不用：** OpenInsider、Robinhood、Vibe-Trading MCP、Unusual Whales 等 Cursor 里的 agent 工具。页面不会登录任何券商，也不会拉起这些 MCP。

英文全文： [DATA_SOURCES.md](DATA_SOURCES.md)。

---

## 对照表

| 界面 | 当前上游 | 密钥 |
|---|---|---|
| 最新价（顶栏、自选、指数、常规时段模拟成交） | 有密钥则 Polygon snapshot，否则 TradingView scanner，再 Yahoo `yfinance`，再 Stooq | 可选 `POLYGON_API_KEY` / `MASSIVE_API_KEY` |
| 纽交所现金时段关闭时的模拟净值 | Yahoo 盘前 / 盘后最新价 | 无 |
| K 线 | Yahoo `yf.download`；日/周线在 Yahoo 429 时回退 Polygon aggregates | Polygon 可选 |
| 组合蒙特卡洛 | Yahoo 月线 `range=max`；429 时 Polygon monthly | Polygon 可选 |
| 纸上策略（均线、RSI、200 日、双动量、行业轮动） | 与图表同一套日线缓存；Yahoo 限流则 Polygon 日线 | Polygon 可选 |
| 涨跌榜、筛选、搜索、同业 | TradingView `POST https://scanner.tradingview.com/america/scan` | 无（未登录） |
| 日线技术评级 | `tradingview-ta` → `https://scanner.tradingview.com/.../scan` | 无 |
| 资料、财务、持有人、空头、Form 4、期权、分析师目标、个股新闻 | Yahoo / `yfinance`（`query1` / `query2.finance.yahoo.com`） | 无 |
| 市场新闻带 | Yahoo RSS | 无 |
| 10-K / 10-Q / 8-K 链接 | Yahoo `get_sec_filings()`（链接多为 EDGAR） | 无 |
| 国会 PTR 交易 | 众议院书记官 ZIP/PDF + 参议院 eFD | 无 |
| LLM 研究与 Vibe | OpenAI Chat Completions 和/或 Anthropic Messages | `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` |
| 图组件 | npm `lightweight-charts`（只画已拉到的 K 线，不是 TV 行情源） | 无 |

---

## 1. Polygon.io / Massive

- 官网：[massive.com](https://massive.com)（原 Polygon.io）。密钥仍用 `POLYGON_API_KEY` 或 `MASSIVE_API_KEY`。基址 `MASSIVE_API_BASE_URL` 或 `https://api.polygon.io`。
- 本仓库调用：美股 snapshot、涨跌幅 snapshot、前日 bar、日/周 aggregates（Yahoo 限流时）。
- 免费套餐常对 snapshot 返回 `NOT_AUTHORIZED`，然后自动回退 TV/Yahoo。有密钥不等于实时。

### 付费档（美股个人，2026-08 标价，以 [massive.com/pricing](https://massive.com/pricing) 为准）

| 套餐 | 月费 | 延迟 | 与本应用相关 |
|---|---|---|---|
| Stocks Basic | $0 | 收盘 | 5 次/分钟，一般 **无** snapshot |
| Stocks Starter | $29 | 延迟 15 分钟 | 不限次数、snapshot、5 年历史 |
| Stocks Developer | $79 | 延迟 15 分钟 | 另含成交明细、10 年历史 |
| Stocks Advanced | $199 | **实时** | snapshot + quotes + 20 年+ |
| Business | 议价 | 实时 + SLA | 企业 |

**未接入：** Options / Indices / FX / Futures 各自的套餐；单独卖的 Financials；Benzinga 等合作数据。本应用期权链来自 **Yahoo**。

---

## 2. TradingView 扫描（未登录）

- 库：`tradingview-screener` → `POST https://scanner.tradingview.com/america/scan`
- 用途：无 Polygon 或未授权时的报价、涨跌榜、搜索、筛选、同业、P/E RSI 等覆盖字段。
- 本仓库 **不** 传 `sessionid`。延迟大约 **15 分钟**。

### 存在但未订阅的付费产品

| 产品 | 作用 | 大约价格 | 对本应用 |
|---|---|---|---|
| TradingView 网站 Essential / Plus / Premium / Ultimate | 多图、预警、去广告 | 约 $15–$240/月 | **无**（未登录） |
| 交易所实时数据包（NASDAQ、NYSE 等） | 在 TV 账号上另付交易所费 | 美股非专业常为每月数美元；专业户更高 | **无**，除非以后接 cookie |
| 第三方 “TradingView API” 托管 | 非官方代理 | 不等 | 未用 |

---

## 3. TradingView 技术分析（`tradingview-ta`）

- `GET /api/ta/{symbol}`。同样未登录、约 15 分钟延迟。
- 没有单独的 TA API 密钥。付费 TV 套餐不会改变本接口。

---

## 4. Yahoo Finance / `yfinance`（非官方）

Yahoo 已于 **2017** 关闭公开 Finance API。`.env` 里没有 Yahoo 密钥。`yfinance` 访问未文档化的 `query1` / `query2.finance.yahoo.com`。

用途：报价回退、K 线与策略历史、盘前盘后计价、资料/财务/持股/内部人/期权/分析师/个股新闻、涨跌榜最后一档。会 429；日线缓存约 15 分钟，可回退 Polygon。

### 存在但喂不进本应用的付费产品

| 产品 | 说明 | 对本应用 |
|---|---|---|
| Yahoo Finance Plus（Bronze / Silver / Gold） | 网站去广告、研报、更长下载历史等，约 $10 / $25 / $50 每月 | **无**，Plus 不是 API 密钥 |
| RapidAPI 上的 “Yahoo Finance” | 第三方封装同类非官方接口 | 未用 |
| 持牌数据商（Finnhub、Tiingo 等） | 正规 API | 仅 Polygon 已接线 |

---

## 5. Yahoo RSS 市场新闻

- `https://finance.yahoo.com/news/rssindex`
- `https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US`

Breaking / Alert 只是标题启发式。Yahoo Plus 付费新闻流是网站功能，不是这两条 RSS。

---

## 6. Stooq

- `https://stooq.com/q/l/` 与 `/q/d/l/`，仅作报价最后一档。
- **无官方付费 API 档。** 有未公开日配额；本应用不传 Stooq apikey。

---

## 7–9. 官方公开记录

| 来源 | URL / 方式 | 付费 |
|---|---|---|
| 众议院书记官 PTR | `disclosures-clerk.house.gov` ZIP + PDF | 无 |
| 参议院 eFD | `efdsearch.senate.gov` | 无 |
| SEC EDGAR | 经 Yahoo filings 链接到 sec.gov | EDGAR 免费；彭博等付费库未用 |

申报的是 **交易** 不是实时持仓，最多 45 天披露期。Quiver / Capitol Trades 等清洗源未接入。

---

## 10. OpenAI

- `POST https://api.openai.com/v1/chat/completions`
- **API 按 token 计费** 才能驱动本应用。ChatGPT Plus / Pro **不含** `OPENAI_API_KEY` 额度。

---

## 11. Anthropic

- `POST https://api.anthropic.com/v1/messages`
- **Console API 按 token 计费** 才能驱动本应用。Claude.ai Pro / Max **不含** API 用量。

---

## 环境变量买到什么

| 变量 | 买到 |
|---|---|
| `POLYGON_API_KEY` / `MASSIVE_API_KEY` | Massive/Polygon REST（档位以你在控制台订的为准） |
| `OPENAI_API_KEY` | OpenAI Chat Completions |
| `ANTHROPIC_API_KEY` | Anthropic Messages |
| Yahoo Plus、TradingView 付费站、Claude Pro、ChatGPT Plus | **本进程里什么也买不到** |
