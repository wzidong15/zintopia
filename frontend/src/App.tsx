import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import Chart from "./Chart";
import PortfolioPanel from "./PortfolioPanel";
import DeepPanel from "./DeepPanel";
import LlmAdvicePanel from "./LlmAdvicePanel";
import FundamentalsPanel from "./FundamentalsPanel";
import ScreenerPanel from "./ScreenerPanel";
import type { DeepAnalysis } from "./deep";
import type { Fundamentals, PeerList } from "./fundamentals";
import OwnershipPanel, { type Ownership } from "./OwnershipPanel";
import type { Bar, NewsItem, Profile, Quote, TA } from "./types";
import { defaultWatchSymbol, loadWatchlist, loadWatchSort, removeFromWatchlist, saveWatchlist, saveWatchSort, sortWatchlist, toggleWatchlistSymbol, watchSortFromId, watchSortId, WATCH_SORT_OPTIONS } from "./watchlist";
import { getCachedQuote, partialFromSearch, rememberQuote, rememberQuotes } from "./quoteCache";
import { fetchBars, getCachedBars, prefetchBars } from "./chartCache";
import { relativeReturn, scaleToPrimary } from "./chartOverlays";
import {
  CHART_REFRESH_MS,
  LIVE_REFRESH_MS,
  MARKET_NEWS_REFRESH_MS,
  MARKET_NEWS_REFRESH_SEC,
  TICKER_NEWS_REFRESH_MS,
  TICKER_NEWS_REFRESH_SEC,
  fmtRefreshSec,
} from "./config";
import { marketClock } from "./marketSession";
import { cls, dividendYieldPct, fmt, fmtEarnings, fmtInt, numish, pct, pctFrac, quoteSourceLabel, rvol } from "./format";
import NewsFeed from "./NewsFeed";
const RANGES = ["1h", "3h", "1d", "5d", "1mo", "3mo", "6mo", "1y", "5y"] as const;

function ohlcvRange(range: (typeof RANGES)[number]) {
  return range === "1h" || range === "3h" ? "1d" : range;
}

const COMPARE_KEY = "zintopia.chart.compare";

function loadCompareSymbol() {
  try {
    const raw = (localStorage.getItem(COMPARE_KEY) || "").trim().toUpperCase();
    if (/^[A-Z][A-Z.]{0,7}$/.test(raw)) return raw;
  } catch {
    /* ignore */
  }
  return "SPY";
}

function saveCompareSymbol(sym: string) {
  try {
    localStorage.setItem(COMPARE_KEY, sym);
  } catch {
    /* ignore */
  }
}
function taBadgeClass(label?: string | null) {
  if (!label) return "badge ta-neutral";
  const u = label.toUpperCase();
  if (u.includes("STRONG BUY") || u.includes("STRONG_BUY")) return "badge ta-strong-buy";
  if (u.includes("BUY")) return "badge ta-buy";
  if (u.includes("STRONG SELL") || u.includes("STRONG_SELL")) return "badge ta-strong-sell";
  if (u.includes("SELL")) return "badge ta-sell";
  return "badge ta-neutral";
}

function SessionClock() {
  const [clock, setClock] = useState(() => marketClock());
  useEffect(() => {
    const id = window.setInterval(() => setClock(marketClock()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <div
      className={`market-bar session-${clock.session}`}
      role="status"
      aria-live="polite"
      title={`US cash session · ${clock.hours}`}
    >
      <span className="market-dot" aria-hidden />
      <span className="market-label">{clock.label}</span>
      <span className="market-time">{clock.timeEt}</span>
      <span className="market-meta">
        <span className="market-day">{clock.weekday}</span>
        <span className="market-hours">{clock.hours}</span>
        <span className="market-until">{clock.until}</span>
      </span>
    </div>
  );
}

function WatchIcon({ active, title }: { active: boolean; title: string }) {
  return (
    <svg
      className={`watch-icon ${active ? "on" : ""}`}
      viewBox="0 0 16 16"
      width="14"
      height="14"
      aria-hidden
    >
      <title>{title}</title>
      <path
        d="M8 1.8l1.9 3.85 4.25.62-3.08 3 .73 4.23L8 11.77 3.2 13.5l.73-4.23-3.08-3 4.25-.62L8 1.8z"
        fill={active ? "currentColor" : "none"}
        stroke="currentColor"
        strokeWidth="1.1"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function RemoveIcon() {
  return (
    <svg className="remove-icon" viewBox="0 0 16 16" width="14" height="14" aria-hidden>
      <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

function QuoteRow({
  q,
  selected,
  onPick,
  watched,
  onToggleWatch,
  onRemoveWatch,
  dense,
}: {
  q: Quote;
  selected?: boolean;
  onPick: (s: string, preview?: Quote) => void;
  watched?: boolean;
  onToggleWatch?: (s: string) => void;
  onRemoveWatch?: (s: string) => void;
  dense?: boolean;
}) {
  const rv = rvol(q);
  return (
    <div className={`row ${selected ? "sel" : ""}`}>
      <button type="button" className={`row-main${dense ? " dense" : ""}`} onClick={() => onPick(q.symbol, q)}>
        <span className="sym">{q.symbol}</span>
        <span>
          <div className="px">{fmt(q.price)}</div>
          {dense ? (
            <div className="row-metrics">
              <span>P/E {fmt(q.pe, 1)}</span>
              <span className={rv == null ? "" : cls(rv - 1)}>{rv == null ? "RVOL —" : `${rv.toFixed(2)}×`}</span>
              <span>{fmtEarnings(q.earnings_at)}</span>
            </div>
          ) : (
            <div className="meta">{q.name}</div>
          )}
        </span>
        <span className={`px ${cls(q.change_pct)}`}>{pct(q.change_pct)}</span>
      </button>
      {onRemoveWatch ? (
        <button
          type="button"
          className="remove-btn"
          title="Remove from watchlist"
          aria-label={`Remove ${q.symbol} from watchlist`}
          onClick={(e) => {
            e.stopPropagation();
            onRemoveWatch(q.symbol);
          }}
        >
          <RemoveIcon />
        </button>
      ) : (
        onToggleWatch && (
          <button
            type="button"
            className={`watch-btn ${watched ? "on" : ""}`}
            title={watched ? "Remove from watchlist" : "Add to watchlist"}
            aria-label={watched ? `Remove ${q.symbol} from watchlist` : `Add ${q.symbol} to watchlist`}
            onClick={(e) => {
              e.stopPropagation();
              onToggleWatch(q.symbol);
            }}
          >
            <WatchIcon
              active={!!watched}
              title={watched ? "Remove from watchlist" : "Add to watchlist"}
            />
          </button>
        )
      )}
    </div>
  );
}

function applyLiveLast(bars: Bar[], quote: Quote | null, symbol: string): Bar[] {
  if (!quote || quote.price == null || !Number.isFinite(quote.price) || bars.length === 0) return bars;
  const qsym = (quote.symbol || quote.ticker || "").toUpperCase().split(":").pop();
  if (qsym && qsym !== symbol.trim().toUpperCase()) return bars;
  const px = quote.price;
  const last = bars[bars.length - 1];
  const sess =
    quote.session === "pre" || quote.session === "post" || quote.session === "rth"
      ? quote.session
      : last.session;
  const now = quote.as_of && quote.as_of > 1_000_000_000 ? quote.as_of : Math.floor(Date.now() / 1000);
  const minute = now - (now % 60);
  if (typeof last.time === "number" && last.time < minute && minute - last.time <= 5 * 60) {
    const prev = last.close ?? px;
    return [
      ...bars,
      {
        time: minute,
        open: prev,
        high: Math.max(prev, px),
        low: Math.min(prev, px),
        close: px,
        volume: 0,
        session: sess || last.session,
      },
    ];
  }
  const high = last.high != null ? Math.max(last.high, px) : px;
  const low = last.low != null ? Math.min(last.low, px) : px;
  return [...bars.slice(0, -1), { ...last, close: px, high, low, session: sess || last.session }];
}

export default function App() {
  const [symbol, setSymbol] = useState(() => defaultWatchSymbol(loadWatchlist(), loadWatchSort()));
  const [range, setRange] = useState<(typeof RANGES)[number]>("1d");
  const [board, setBoard] = useState<"gainers" | "losers" | "active" | "screen">("gainers");
  const [indices, setIndices] = useState<Quote[]>([]);
  const [watchSymbols, setWatchSymbols] = useState<string[]>(() => loadWatchlist());
  const [watch, setWatch] = useState<Quote[]>([]);
  const [watchSort, setWatchSort] = useState(() => loadWatchSort());
  const [movers, setMovers] = useState<Quote[]>([]);
  const [moversLoading, setMoversLoading] = useState(false);
  const [moversErr, setMoversErr] = useState<string | null>(null);
  const [quote, setQuote] = useState<Quote | null>(null);
  const [quoteRefreshing, setQuoteRefreshing] = useState(false);
  const [bars, setBars] = useState<Bar[]>([]);
  const [barsLoading, setBarsLoading] = useState(false);
  const [news, setNews] = useState<NewsItem[]>([]);
  const [marketNews, setMarketNews] = useState<NewsItem[]>([]);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [ta, setTa] = useState<TA | null>(null);
  const [deep, setDeep] = useState<DeepAnalysis | null>(null);
  const [deepLoading, setDeepLoading] = useState(false);
  const [deepErr, setDeepErr] = useState<string | null>(null);
  const [fundamentals, setFundamentals] = useState<Fundamentals | null>(null);
  const [fundamentalsLoading, setFundamentalsLoading] = useState(false);
  const [ownership, setOwnership] = useState<Ownership | null>(null);
  const [ownershipLoading, setOwnershipLoading] = useState(false);
  const [peers, setPeers] = useState<PeerList | null>(null);
  const [compareOn, setCompareOn] = useState(false);
  const [compareSym, setCompareSym] = useState(() => loadCompareSymbol());
  const [compareDraft, setCompareDraft] = useState(() => loadCompareSymbol());
  const [compareBars, setCompareBars] = useState<Bar[]>([]);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<
    { symbol: string; name: string; exchange?: string; change_pct?: number }[]
  >([]);
  const [err, setErr] = useState<string | null>(null);
  const [asOf, setAsOf] = useState<number | null>(null);
  const [view, setView] = useState<"research" | "portfolios">("research");

  useEffect(() => {
    let live = true;
    const loadIndices = () => {
      api
        .indices()
        .then((r) => {
          if (!live) return;
          const items = r.items || [];
          rememberQuotes(items);
          setIndices(items);
          setAsOf(Math.floor(Date.now() / 1000));
        })
        .catch(() => undefined);
    };
    loadIndices();
    const id = setInterval(loadIndices, 30_000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (board === "screen") return;
    let live = true;
    setMoversLoading(true);
    setMoversErr(null);
    const loadMovers = () => {
      api
        .movers(board)
        .then((r) => {
          if (!live) return;
          const items = r.items || [];
          rememberQuotes(items);
          setMovers(items);
          setMoversErr(r.error || (items.length ? null : "No movers returned"));
          setMoversLoading(false);
        })
        .catch((e) => {
          if (!live) return;
          setMovers([]);
          setMoversErr(String(e.message || e));
          setMoversLoading(false);
        });
    };
    loadMovers();
    const id = setInterval(loadMovers, 30_000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [board]);

  useEffect(() => {
    let live = true;
    const loadWatch = () => {
      if (watchSymbols.length === 0) {
        if (live) setWatch([]);
        return;
      }
      api
        .quotes(watchSymbols)
        .then((r) => {
          if (!live) return;
          const items = r.items || [];
          rememberQuotes(items);
          setWatch(items);
        })
        .catch(() => live && setWatch([]));
    };
    loadWatch();
    const id = setInterval(loadWatch, 30_000);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [watchSymbols]);

  useEffect(() => {
    let live = true;
    setErr(null);
    setNews([]);
    setProfile(null);
    setTa(null);

    const cached = getCachedQuote(symbol);
    if (cached) setQuote(cached);
    else if (quote?.symbol?.toUpperCase() !== symbol) setQuote(null);

    setQuoteRefreshing(true);
    api
      .quote(symbol)
      .then((q) => {
        if (!live) return;
        rememberQuote(q);
        setQuote(q);
        setQuoteRefreshing(false);
      })
      .catch((e) => {
        if (!live) return;
        if (!cached) setErr(String(e.message || e));
        setQuoteRefreshing(false);
      });

    const quotePoll = setInterval(() => {
      api
        .quote(symbol)
        .then((x) => {
          if (!live) return;
          rememberQuote(x);
          setQuote(x);
        })
        .catch((e) => live && !getCachedQuote(symbol) && setErr(String(e.message || e)));
    }, LIVE_REFRESH_MS);

    const secondary = window.setTimeout(() => {
      api.ta(symbol).then((t) => live && setTa(t)).catch(() => live && setTa(null));
      api.profile(symbol).then((p) => live && setProfile(p)).catch(() => live && setProfile(null));
    }, 300);

    return () => {
      live = false;
      clearInterval(quotePoll);
      window.clearTimeout(secondary);
    };
  }, [symbol]);

  useEffect(() => {
    let live = true;
    const load = () => {
      api
        .news(symbol)
        .then((n) => live && setNews(n.items || []))
        .catch(() => live && setNews([]));
    };
    load();
    const id = window.setInterval(load, TICKER_NEWS_REFRESH_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, [symbol]);

  useEffect(() => {
    let live = true;
    const load = () => {
      api
        .marketNews()
        .then((n) => live && setMarketNews(n.items || []))
        .catch(() => live && setMarketNews([]));
    };
    load();
    const id = window.setInterval(load, MARKET_NEWS_REFRESH_MS);
    return () => {
      live = false;
      window.clearInterval(id);
    };
  }, []);

  useEffect(() => {
    let live = true;
    const histRange = ohlcvRange(range);
    const cached = getCachedBars(symbol, histRange);
    if (cached) {
      setBars(cached);
      setBarsLoading(false);
    } else {
      setBarsLoading(true);
    }

    fetchBars(symbol, histRange)
      .then((bars) => {
        if (!live) return;
        setBars(bars);
        setBarsLoading(false);
      })
      .catch(() => {
        if (!live) return;
        if (!cached) setBars([]);
        setBarsLoading(false);
      });

    const chartPoll = setInterval(() => {
      fetchBars(symbol, histRange, { force: true })
        .then((next) => {
          if (!live) return;
          setBars(next);
        })
        .catch(() => undefined);
    }, CHART_REFRESH_MS);

    return () => {
      live = false;
      clearInterval(chartPoll);
    };
  }, [symbol, range]);

  useEffect(() => {
    const bench = compareSym.trim().toUpperCase();
    if (!compareOn || !bench || bench === symbol.trim().toUpperCase()) {
      setCompareBars([]);
      return;
    }
    let live = true;
    const histRange = ohlcvRange(range);
    const cached = getCachedBars(bench, histRange);
    if (cached) setCompareBars(cached);
    fetchBars(bench, histRange)
      .then((next) => live && setCompareBars(next))
      .catch(() => live && setCompareBars([]));
    return () => {
      live = false;
    };
  }, [symbol, range, compareSym, compareOn]);

  useEffect(() => {
    let live = true;
    setDeep(null);
    setDeepErr(null);
    setDeepLoading(true);
    api
      .deep(symbol)
      .then((d) => {
        if (!live) return;
        setDeep(d);
        setDeepLoading(false);
      })
      .catch((e) => {
        if (!live) return;
        setDeepErr(String(e.message || e));
        setDeepLoading(false);
      });
    return () => {
      live = false;
    };
  }, [symbol]);

  useEffect(() => {
    let live = true;
    setFundamentals(null);
    setFundamentalsLoading(true);
    setOwnership(null);
    setOwnershipLoading(true);
    setPeers(null);
    const t = window.setTimeout(() => {
      api
        .fundamentals(symbol)
        .then((f) => {
          if (!live) return;
          setFundamentals(f);
          setFundamentalsLoading(false);
        })
        .catch(() => {
          if (!live) return;
          setFundamentals(null);
          setFundamentalsLoading(false);
        });
      api
        .ownership(symbol)
        .then((o) => {
          if (!live) return;
          setOwnership(o);
          setOwnershipLoading(false);
        })
        .catch(() => {
          if (!live) return;
          setOwnership(null);
          setOwnershipLoading(false);
        });
      api
        .peers(symbol)
        .then((p) => live && setPeers(p))
        .catch(() => live && setPeers(null));
    }, 400);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [symbol]);

  useEffect(() => {
    if (!q.trim()) {
      setHits([]);
      return;
    }
    const t = setTimeout(() => {
      api.search(q).then((r) => setHits(r.items)).catch(() => setHits([]));
    }, 220);
    return () => clearTimeout(t);
  }, [q]);

  const pick = (s: string, preview?: Quote) => {
    const sym = s.trim().toUpperCase();
    const instant = preview ?? getCachedQuote(sym);
    if (instant) {
      rememberQuote(instant);
      setQuote(instant);
    }
    const cachedBars = getCachedBars(sym, ohlcvRange(range));
    if (cachedBars) setBars(cachedBars);
    prefetchBars(sym, ohlcvRange(range));
    setSymbol(sym);
  };
  const isWatched = (s: string) => watchSymbols.includes(s.trim().toUpperCase());
  const toggleWatch = (s: string) => {
    setWatchSymbols((prev) => {
      const next = toggleWatchlistSymbol(prev, s);
      saveWatchlist(next);
      return next;
    });
  };
  const removeWatch = (s: string) => {
    setWatchSymbols((prev) => {
      const next = removeFromWatchlist(prev, s);
      saveWatchlist(next);
      return next;
    });
  };
  const setWatchSortId = (id: string) => {
    const next = watchSortFromId(id);
    saveWatchSort(next);
    setWatchSort(next);
  };
  const sortedWatch = useMemo(
    () => sortWatchlist(watch, watchSymbols, watchSort),
    [watch, watchSymbols, watchSort],
  );
  const moversSource = useMemo(() => quoteSourceLabel(movers[0]?.source), [movers]);
  const stats = useMemo(() => {
    const avgVol = quote?.avg_volume ?? numish(profile?.averageVolume);
    const vol = quote?.volume;
    const relVol = vol != null && avgVol != null && avgVol > 0 ? vol / avgVol : null;
    const eps = quote?.eps ?? numish(profile?.trailingEps);
    const pe = quote?.pe ?? numish(profile?.trailingPE);
    const yieldPct = dividendYieldPct(quote, profile);
    const earn = fmtEarnings(quote?.earnings_at ?? profile?.earnings_at ?? profile?.earningsTimestampStart ?? profile?.earningsTimestamp);
    return [
      ["Close", fmt(quote?.regular_close)],
      ["Prev close", fmt(quote?.prev_close)],
      ["Open", fmt(quote?.open)],
      ["High", fmt(quote?.high)],
      ["Low", fmt(quote?.low)],
      ["Volume", fmtInt(quote?.volume)],
      ["RVOL", relVol == null ? "—" : `${relVol.toFixed(2)}×`, relVol == null ? undefined : cls(relVol - 1)],
      ["Mkt cap", fmtInt(quote?.market_cap ?? numish(profile?.marketCap))],
      ["P/E", fmt(pe)],
      ["EPS", fmt(eps)],
      ["Yield", yieldPct == null ? "—" : `${yieldPct.toFixed(2)}%`],
      ["Earnings", earn],
      ["RSI", fmt(quote?.rsi, 1)],
      ["Beta", fmt(numish(profile?.beta))],
      ["Float", fmtInt(numish(profile?.floatShares))],
      ["Short %", pctFrac(numish(profile?.shortPercentOfFloat))],
      ["52w high", fmt(quote?.year_high ?? numish(profile?.fiftyTwoWeekHigh))],
      ["52w low", fmt(quote?.year_low ?? numish(profile?.fiftyTwoWeekLow))],
    ] as [string, string, string?][];
  }, [quote, profile]);
  const chartBars = useMemo(() => applyLiveLast(bars, quote, symbol), [bars, quote, symbol]);
  const comparePoints = useMemo(
    () => (compareOn ? scaleToPrimary(chartBars, compareBars) : []),
    [compareOn, chartBars, compareBars],
  );
  const compareRel = useMemo(
    () => (compareOn ? relativeReturn(chartBars, compareBars) : null),
    [compareOn, chartBars, compareBars],
  );

  const commitCompare = (raw: string) => {
    const next = raw.trim().toUpperCase().split(":")[0] || "SPY";
    setCompareDraft(next);
    setCompareSym(next);
    saveCompareSymbol(next);
    setCompareOn(true);
  };

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <a
            className="brand-home"
            href="/"
            title="Reload Zintopia"
            onClick={(e) => {
              e.preventDefault();
              window.location.reload();
            }}
          >
            <img
              className="brand-logo"
              src="/logo-horizontal.svg"
              width={138}
              height={32}
              alt="Zintopia"
            />
          </a>
          <span>US equities · stock portfolio</span>
        </div>
        <div className="view-tabs">
          <button type="button" className={view === "research" ? "on" : ""} onClick={() => setView("research")}>
            Research
          </button>
          <button
            type="button"
            className={view === "portfolios" ? "on" : ""}
            onClick={() => setView("portfolios")}
          >
            Stock portfolio
          </button>
        </div>
        <div className="search">
          <svg className="search-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden>
            <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
            <path d="M10.4 10.4L14 14" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          <input
            value={q}
            placeholder="Search ticker or name"
            aria-label="Search ticker or name"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && q.trim()) {
                const sym = q.trim().toUpperCase();
                const instant = getCachedQuote(sym) ?? partialFromSearch({ symbol: sym, name: sym });
                rememberQuote(instant);
                setQuote(instant);
                setSymbol(sym);
                setQ("");
                setHits([]);
                setView("research");
              }
            }}
          />
          {hits.length > 0 && (
            <div className="search-hits">
              {hits.map((h) => (
                <div key={h.symbol} className="search-hit">
                  <button
                    type="button"
                    onClick={() => {
                      pick(h.symbol, partialFromSearch(h));
                      setQ("");
                      setHits([]);
                      setView("research");
                    }}
                  >
                    <span>
                      <b className="sym">{h.symbol}</b> <span className="muted">{h.name}</span>
                    </span>
                    <span className={cls(h.change_pct)}>{pct(h.change_pct)}</span>
                  </button>
                  <button
                    type="button"
                    className={`watch-btn ${isWatched(h.symbol) ? "on" : ""}`}
                    title={isWatched(h.symbol) ? "Remove from watchlist" : "Add to watchlist"}
                    aria-label={
                      isWatched(h.symbol)
                        ? `Remove ${h.symbol} from watchlist`
                        : `Add ${h.symbol} to watchlist`
                    }
                    onClick={() => toggleWatch(h.symbol)}
                  >
                    <WatchIcon
                      active={isWatched(h.symbol)}
                      title={isWatched(h.symbol) ? "Remove from watchlist" : "Add to watchlist"}
                    />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </header>
      <SessionClock />
      <NewsFeed
        items={marketNews}
        empty="No market headlines"
        variant="tape"
        hint={`Yahoo · ${fmtRefreshSec(MARKET_NEWS_REFRESH_SEC)}`}
      />

      {view === "research" && (
        <>
      <nav className="strip">
        {indices.map((i) => (
          <button
            key={i.ticker}
            className={symbol === i.symbol ? "active" : ""}
            onClick={() => pick(i.symbol === "VIX" ? "VIX" : i.symbol, i)}
          >
            <span className="sym">{i.symbol}</span>
            <span className="px">{fmt(i.price)}</span>
            <span className={cls(i.change_pct)}>{pct(i.change_pct)}</span>
          </button>
        ))}
      </nav>

      <div className="layout">
        <aside className="col">
          <div className="watch-toolbar">
            <div className="watch-toolbar-k">Watchlist</div>
            <label className="watch-sort">
              <span>Sort</span>
              <select
                value={watchSortId(watchSort)}
                onChange={(e) => setWatchSortId(e.target.value)}
                aria-label="Sort watchlist"
              >
                {WATCH_SORT_OPTIONS.map((opt) => (
                  <option key={opt.id} value={opt.id}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {sortedWatch.length > 0 && (
            <div className="watch-cols" aria-hidden>
              <span className={watchSort.by === "name" ? "on" : ""}>
                Ticker{watchSort.by === "name" ? (watchSort.dir === "asc" ? " A–Z" : " Z–A") : ""}
              </span>
              <span>Last</span>
              <span className={watchSort.by === "pct" ? "on" : ""}>
                {watchSort.by === "pct"
                  ? watchSort.dir === "desc"
                    ? "% day high"
                    : "% day low"
                  : "% day"}
              </span>
            </div>
          )}
          {sortedWatch.length === 0 && (
            <div className="watch-empty">Click the star on a symbol to add it. Click x to remove.</div>
          )}
          {sortedWatch.map((w) => (
            <QuoteRow
              key={w.ticker}
              q={w}
              selected={w.symbol === symbol}
              onPick={pick}
              onRemoveWatch={removeWatch}
              dense
            />
          ))}
          <div className="section-h">
            Universe
            <div className="tabs">
              {(["gainers", "losers", "active", "screen"] as const).map((k) => (
                <button key={k} className={board === k ? "on" : ""} onClick={() => setBoard(k)}>
                  {k}
                </button>
              ))}
            </div>
          </div>
          {board === "screen" ? (
            <>
              <div className="universe-src">Source · TradingView</div>
              <ScreenerPanel
                selected={symbol}
                onPick={pick}
                watched={isWatched}
                onToggleWatch={toggleWatch}
              />
            </>
          ) : (
            <>
              {moversSource && <div className="universe-src">Source · {moversSource}</div>}
              {moversLoading && movers.length === 0 && (
                <div className="watch-empty">Loading {board}…</div>
              )}
              {moversErr && movers.length === 0 && !moversLoading && (
                <div className="watch-empty movers-err">Movers unavailable.</div>
              )}
              {movers.map((m) => (
                <QuoteRow
                  key={m.ticker}
                  q={m}
                  selected={m.symbol === symbol}
                  onPick={pick}
                  watched={isWatched(m.symbol)}
                  onToggleWatch={toggleWatch}
                  dense
                />
              ))}
            </>
          )}
        </aside>

        <main className="center">
          {err && <div className="err">{err}</div>}
          <div className="header">
            <div>
              <div className="title-row">
                <h1>{quote?.symbol || symbol}</h1>
                <button
                  type="button"
                  className={`watch-btn header-watch ${isWatched(symbol) ? "on" : ""}`}
                  title={isWatched(symbol) ? "Remove from watchlist" : "Add to watchlist"}
                  aria-label={
                    isWatched(symbol)
                      ? `Remove ${symbol} from watchlist`
                      : `Add ${symbol} to watchlist`
                  }
                  onClick={() => toggleWatch(symbol)}
                >
                  <WatchIcon
                    active={isWatched(symbol)}
                    title={isWatched(symbol) ? "Remove from watchlist" : "Add to watchlist"}
                  />
                </button>
              </div>
              <div className="name">
                {quote?.name} {quote?.exchange ? `· ${quote.exchange}` : ""}{" "}
                {quote?.sector ? `· ${quote.sector}` : ""}
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div className={`bigpx ${cls(quote?.change_pct)}${quoteRefreshing ? " refreshing" : ""}`}>
                {fmt(quote?.price)}
              </div>
              {(quote?.session === "pre" || quote?.session === "post" || quote?.session === "closed") &&
                quote?.regular_close != null && (
                  <div className="muted">
                    {quote.session === "pre"
                      ? "Pre-market last"
                      : quote.session === "post"
                        ? "After hours last"
                        : "After hours last · ended 8:00 PM ET"}
                    {" · "}
                    Close {fmt(quote.regular_close)}
                    {quote.vs_close_pct != null ? ` · AH ${pct(quote.vs_close_pct)}` : ""}
                  </div>
                )}
              <div className={cls(quote?.change_pct)}>
                {fmt(quote?.change)} ({pct(quote?.change_pct)})
              </div>
            </div>
          </div>
          <div className="range">
            {RANGES.map((r) => (
              <button key={r} className={range === r ? "on" : ""} onClick={() => setRange(r)}>
                {r.toUpperCase()}
              </button>
            ))}
            <label className={`range-vs${compareOn ? " on" : ""}`}>
              <button
                type="button"
                className={compareOn ? "on" : ""}
                onClick={() => setCompareOn((v) => !v)}
              >
                vs
                {compareRel != null
                  ? ` ${compareRel >= 0 ? "+" : ""}${(compareRel * 100).toFixed(1)}%`
                  : ""}
              </button>
              <input
                value={compareDraft}
                aria-label="Compare ticker"
                maxLength={8}
                onChange={(e) => setCompareDraft(e.target.value.toUpperCase())}
                onBlur={() => commitCompare(compareDraft)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") {
                    e.preventDefault();
                    (e.target as HTMLInputElement).blur();
                  }
                }}
              />
            </label>
          </div>
          <div className="stats">
            {stats.map(([k, v, tone]) => (
              <div className="stat" key={k}>
                <div className="k">{k}</div>
                <div className={`v ${tone || ""}`}>{v}</div>
              </div>
            ))}
          </div>
          <div className={`chart-wrap${barsLoading ? " chart-loading-active" : ""}`}>
            {barsLoading && bars.length === 0 && <div className="chart-loading">Loading chart…</div>}
            <Chart
              bars={chartBars}
              showVwap={range === "1d" || range === "1h" || range === "3h"}
              focusHours={range === "1h" ? 1 : range === "3h" ? 3 : undefined}
              compare={comparePoints}
              compareLabel={compareSym}
            />
            <div className="muted chart-note">
              SMA 20 / 50 / 200 on this chart interval
              {range === "1d" || range === "1h" || range === "3h" ? " · VWAP regular session" : ""}
              {range === "1h" || range === "3h" ? ` · zoomed to last ${range === "1h" ? "1 hour" : "3 hours"}` : ""}
              {compareOn && comparePoints.length
                ? ` · ${compareSym} scaled to first overlapping close`
                : ""}
              {" · times in ET"}
              {chartBars.some((b) => b.session === "pre" || b.session === "post")
                ? " · Yahoo pre-market (amber) and post-market (blue) candles."
                : "."}
            </div>
          </div>
          <FundamentalsPanel data={fundamentals} loading={fundamentalsLoading} />
          <OwnershipPanel data={ownership} loading={ownershipLoading} />
          <LlmAdvicePanel symbol={symbol} />
          <DeepPanel data={deep} loading={deepLoading} error={deepErr} peers={peers} onPickPeer={pick} />
        </main>

        <aside className="col">
          <div className="section-h">Daily TA (TradingView)</div>
          {ta && (
            <div className="stats" style={{ gridTemplateColumns: "1fr 1fr", padding: "0 12px 10px" }}>
              <div className="stat">
                <div className="k">Summary</div>
                <div className={`v ${taBadgeClass(ta.summary.RECOMMENDATION)}`}>
                  {(ta.summary.RECOMMENDATION ?? "—").replace(/_/g, " ")}
                </div>
              </div>
              <div className="stat">
                <div className="k">Buy / Neutral / Sell</div>
                <div className="v">
                  {ta.summary.BUY ?? 0} / {ta.summary.NEUTRAL ?? 0} / {ta.summary.SELL ?? 0}
                </div>
              </div>
            </div>
          )}
          <div className="section-h">
            News (Yahoo)
            <span className="muted">{symbol} · {fmtRefreshSec(TICKER_NEWS_REFRESH_SEC)}</span>
          </div>
          <NewsFeed items={news} empty={`No Yahoo headlines for ${symbol}`} />
          {profile?.longBusinessSummary && (
            <>
              <div className="section-h">Profile</div>
              <div className="summary">{String(profile.longBusinessSummary).slice(0, 700)}</div>
            </>
          )}
        </aside>
      </div>
        </>
      )}
      {view === "portfolios" && (
        <PortfolioPanel
          onOpenSymbol={(s) => {
            pick(s);
            setView("research");
          }}
        />
      )}
      <footer className="foot">
        <span>
          Quotes: TradingView scanner (unsigned ≈ 15m delay). Charts/news: Yahoo Finance. Stock
          portfolio is paper shares only — no options. Not financial advice.
        </span>
        <span>{asOf ? new Date(asOf * 1000).toLocaleTimeString() : ""}</span>
      </footer>
    </div>
  );
}
