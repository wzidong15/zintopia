export type PortfolioStrategyKind =
  | "manual"
  | "buy_hold"
  | "sma_cross"
  | "momentum"
  | "rsi_reversion"
  | "trend_200"
  | "dual_momentum"
  | "sector_rot"
  | "rsi_trend";

export type PortfolioStrategy = {
  kind: PortfolioStrategyKind;
  auto?: boolean;
  symbol?: string;
  last_run_at?: number;
  next_run_at?: number;
  interval_sec?: number;
  note?: string;
};

export type PortfolioHolding = {
  symbol: string;
  shares: number;
  avg_cost: number;
  last_price?: number | null;
  market_value?: number | null;
  unrealized_pnl?: number | null;
};

export type PortfolioTrade = {
  t: number;
  symbol: string;
  side: "buy" | "sell";
  shares: number;
  price: number;
  notional: number;
  source?: string;
};

export type PortfolioSnapshot = {
  t: number;
  nav: number;
  cash: number;
};

export type PortfolioOrigin = "paper" | "import";
export type PortfolioCostBasis = "mark" | "csv";

export type Portfolio = {
  id: string;
  name: string;
  initial_cash: number;
  cash: number;
  nav: number;
  pnl: number;
  return_pct: number;
  max_drawdown_pct?: number;
  created_at: number;
  updated_at?: number;
  holdings: PortfolioHolding[];
  trades?: PortfolioTrade[];
  snapshots?: PortfolioSnapshot[];
  strategy?: PortfolioStrategy;
  last_error?: string | null;
  tick_note?: string | null;
  import_note?: string | null;
  import_skipped?: { symbol: string; reason: string }[];
  origin?: PortfolioOrigin;
  cost_basis?: PortfolioCostBasis | null;
  mark_session?: "pre" | "rth" | "post" | "closed" | string;
};

export type PortfolioSummary = {
  id: string;
  name: string;
  initial_cash: number;
  cash: number;
  nav: number;
  pnl: number;
  return_pct: number;
  strategy?: PortfolioStrategy;
  updated_at?: number;
  created_at: number;
  holdings_count: number;
  last_error?: string | null;
  origin?: PortfolioOrigin;
  cost_basis?: PortfolioCostBasis | null;
};

export const STRATEGY_OPTIONS: {
  id: PortfolioStrategyKind;
  label: string;
  hint: string;
  usesSymbol?: boolean;
}[] = [
  { id: "manual", label: "Manual", hint: "You place paper buy/sell orders in shares (no options)." },
  { id: "buy_hold", label: "Buy & hold", hint: "Automatically invest cash in one ticker and hold." },
  {
    id: "trend_200",
    label: "200-day trend",
    hint: "Long the ticker when price is above the 200-day SMA; otherwise cash. Classic time-series trend (Faber).",
  },
  {
    id: "dual_momentum",
    label: "Dual momentum",
    hint: "Hold the stronger of your risk-on ticker vs EFA when 1/3/6-month momentum beats SHY and zero; otherwise SHY. Antonacci-style GEM.",
  },
  {
    id: "sector_rot",
    label: "Sector rotation",
    hint: "Equal-weight the top 3 US sector ETFs (XLK, XLF, …) by 6-month return if that return is positive; otherwise cash.",
    usesSymbol: false,
  },
  {
    id: "rsi_trend",
    label: "RSI + trend filter",
    hint: "Buy ~25% cash when RSI < 30 and price is above SMA200; sell when RSI > 70 or the 200-day trend fails.",
  },
  { id: "sma_cross", label: "SMA crossover", hint: "Buy when SMA20 > SMA50; sell when it crosses down." },
  {
    id: "momentum",
    label: "Day-gainers",
    hint: "Rotate into the top 3 US day-gainers, equal weight. Noisy vs dual momentum or sector rotation.",
    usesSymbol: false,
  },
  {
    id: "rsi_reversion",
    label: "RSI mean reversion",
    hint: "Buy ~25% cash when RSI < 30; sell when RSI > 70. No trend filter.",
  },
];

const SORT_KEY = "zintopia.portfolios.sort";
const IMPORT_SORT_KEY = "zintopia.portfolios.import.sort";

export type PortfolioSortBy = "added" | "name" | "perf";
export type PortfolioSortDir = "asc" | "desc";
export type PortfolioSort = { by: PortfolioSortBy; dir: PortfolioSortDir };

const DEFAULT_PORTFOLIO_SORT: PortfolioSort = { by: "name", dir: "asc" };

export const PORTFOLIO_SORT_OPTIONS: {
  id: string;
  by: PortfolioSortBy;
  dir: PortfolioSortDir;
  label: string;
}[] = [
  { id: "name-asc", by: "name", dir: "asc", label: "Name A to Z" },
  { id: "name-desc", by: "name", dir: "desc", label: "Name Z to A" },
  { id: "perf-desc", by: "perf", dir: "desc", label: "Return high to low" },
  { id: "perf-asc", by: "perf", dir: "asc", label: "Return low to high" },
  { id: "added", by: "added", dir: "asc", label: "Added order" },
];

export function portfolioSortId(sort: PortfolioSort): string {
  if (sort.by === "added") return "added";
  return `${sort.by}-${sort.dir}`;
}

export function portfolioSortFromId(id: string): PortfolioSort {
  const opt = PORTFOLIO_SORT_OPTIONS.find((o) => o.id === id);
  return opt ? { by: opt.by, dir: opt.dir } : { ...DEFAULT_PORTFOLIO_SORT };
}

export function loadPortfolioSort(key = SORT_KEY): PortfolioSort {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return { ...DEFAULT_PORTFOLIO_SORT };
    const parsed = JSON.parse(raw) as Partial<PortfolioSort>;
    const by: PortfolioSortBy =
      parsed.by === "name" || parsed.by === "perf" || parsed.by === "added" ? parsed.by : "name";
    const dir: PortfolioSortDir = parsed.dir === "desc" ? "desc" : "asc";
    return { by, dir: by === "added" ? "asc" : dir };
  } catch {
    return { ...DEFAULT_PORTFOLIO_SORT };
  }
}

export function savePortfolioSort(sort: PortfolioSort, key = SORT_KEY) {
  localStorage.setItem(key, JSON.stringify(sort));
}

export function loadImportSort(): PortfolioSort {
  return loadPortfolioSort(IMPORT_SORT_KEY);
}

export function saveImportSort(sort: PortfolioSort) {
  savePortfolioSort(sort, IMPORT_SORT_KEY);
}

export function isImportedPortfolio(p: {
  origin?: string | null;
  trades?: { source?: string }[] | null;
}): boolean {
  if (p.origin === "import") return true;
  if (p.origin === "paper") return false;
  return (p.trades || []).some((t) => t.source === "broker-csv");
}

export function sortPortfolios(items: PortfolioSummary[], sort: PortfolioSort): PortfolioSummary[] {
  const rows = [...items];
  const mul = sort.dir === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    if (sort.by === "name") {
      const cmp = (a.name || "").localeCompare(b.name || "", undefined, { sensitivity: "base" });
      return mul * cmp || a.created_at - b.created_at;
    }
    if (sort.by === "perf") {
      const av = a.return_pct;
      const bv = b.return_pct;
      const aMiss = av == null || Number.isNaN(av);
      const bMiss = bv == null || Number.isNaN(bv);
      if (aMiss && bMiss) return (a.name || "").localeCompare(b.name || "");
      if (aMiss) return 1;
      if (bMiss) return -1;
      if (av === bv) return (a.name || "").localeCompare(b.name || "");
      return mul * (av - bv);
    }
    return a.created_at - b.created_at;
  });
  return rows;
}
