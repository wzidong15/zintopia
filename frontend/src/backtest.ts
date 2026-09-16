export type BtParamSpec = {
  key: string;
  label: string;
  type: "int_list" | "float_list" | "symbol";
  default: number[] | string;
  help?: string;
};

export type BtStrategySpec = {
  id: string;
  label: string;
  group: "portfolio" | "per_symbol";
  description: string;
  params: BtParamSpec[];
};

export type BtPreset = {
  id: string;
  label: string;
  symbols: string[];
  strategy: string;
  params: Record<string, unknown>;
};

export type BtPaperKind = {
  strategy: string;
  symbols: string[];
  params: Record<string, unknown>;
};

export type BtMeta = {
  engine: string;
  strategies: BtStrategySpec[];
  presets: BtPreset[];
  paper_kinds: Record<string, BtPaperKind>;
  sector_etfs: string[];
  rank_keys: string[];
  limits: {
    max_symbols: number;
    max_runs: number;
    max_strategies: number;
    equity_runs: number;
    history_range: string;
  };
  defaults: { start: string; initial_cash: number; fees_bps: number; slippage_bps: number };
};

export type BtTrade = {
  symbol?: string | null;
  entry?: string | null;
  exit?: string | null;
  return: number;
  reason?: string | null;
};

export type BtRun = {
  id: string;
  rank: number;
  strategy: string;
  strategy_label: string;
  params: Record<string, unknown>;
  label: string;
  symbols: string[];
  total_return: number;
  cagr: number;
  volatility: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  calmar: number | null;
  trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  avg_trade_return: number | null;
  best_trade: number | null;
  worst_trade: number | null;
  exposure: number;
  final_value: number;
  years: number;
  excess_cagr: number;
  equity?: number[];
  recent_trades?: BtTrade[];
};

export type BtBenchmark = {
  label: string;
  equity: number[];
  total_return: number;
  cagr: number;
  volatility: number;
  sharpe: number;
  sortino: number;
  max_drawdown: number;
  calmar: number | null;
};

export type BtFoldStats = {
  total_return?: number | null;
  cagr?: number | null;
  sharpe?: number | null;
  sortino?: number | null;
  max_drawdown?: number | null;
  trades?: number | null;
  exposure?: number | null;
};

export type BtSegment = {
  fold: number;
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  train_bars: number;
  test_bars: number;
  chosen_id: string;
  chosen_label: string;
  chosen_strategy_label: string;
  chosen_full_rank: number | null;
  is: BtFoldStats;
  oos: BtFoldStats;
  oos_benchmark: BtFoldStats;
  beat_benchmark: boolean;
};

export type BtWalkForward = {
  mode: "anchored" | "rolling";
  folds: number;
  train_pct: number;
  rank_by: string;
  train_bars: number;
  oos_start: string;
  oos_end: string;
  oos_equity: (number | null)[];
  oos: BtFoldStats & { volatility?: number; calmar?: number | null; final_value?: number };
  oos_benchmark: BtFoldStats & { volatility?: number; calmar?: number | null };
  is_best_full: ({ id: string; label: string; strategy_label: string } & BtFoldStats) | null;
  segments: BtSegment[];
  folds_beating_benchmark: number;
  distinct_selections: number;
  avg_is_cagr: number | null;
  efficiency: number | null;
};

export type BtResult = {
  engine: string;
  symbols: string[];
  fetched: string[];
  sources: Record<string, string>;
  start: string;
  end: string;
  requested_start: string;
  data_start: string;
  bars: number;
  dates: number[];
  initial_cash: number;
  fees_bps: number;
  slippage_bps: number;
  rank_by: string;
  combination_count: number;
  equity_runs: number;
  best_id: string | null;
  runs: BtRun[];
  benchmark: BtBenchmark;
  walk_forward?: BtWalkForward | null;
  warnings: string[];
  assumptions: Record<string, string>;
  elapsed_ms: number;
  note?: string;
};

export const RANK_LABELS: Record<string, string> = {
  sharpe: "Sharpe",
  cagr: "CAGR",
  total_return: "Total return",
  calmar: "Calmar",
  sortino: "Sortino",
  max_drawdown: "Max drawdown (shallowest)",
};

/** Text-box representation of a strategy's parameters ("10, 20, 50"). */
export function defaultParamText(spec: BtStrategySpec): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of spec.params) out[p.key] = paramValueToText(p.default);
  return out;
}

export function paramValueToText(v: unknown): string {
  if (v == null) return "";
  if (Array.isArray(v)) return v.map((x) => String(x)).join(", ");
  return String(v);
}

export function paramTextFor(spec: BtStrategySpec, params: Record<string, unknown>): Record<string, string> {
  const out = defaultParamText(spec);
  for (const p of spec.params) {
    if (params[p.key] != null) out[p.key] = paramValueToText(params[p.key]);
  }
  return out;
}

/** Parse the text boxes back into the API shape (lists of numbers, or a symbol string). */
export function paramsFromText(spec: BtStrategySpec, text: Record<string, string>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const p of spec.params) {
    const raw = (text[p.key] ?? "").trim();
    if (p.type === "symbol") {
      out[p.key] = raw.toUpperCase();
      continue;
    }
    const vals = raw
      .split(/[,;\s]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map(Number)
      .filter((n) => Number.isFinite(n));
    out[p.key] = vals.length ? vals : p.default;
  }
  return out;
}

export function parseSymbols(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of text.split(/[,;\s]+/)) {
    const s = raw.trim().toUpperCase();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    out.push(s);
  }
  return out;
}

/** Combination count for a grid, so the UI can warn before hitting the server cap. */
export function gridSize(spec: BtStrategySpec, params: Record<string, unknown>): number {
  let n = 1;
  for (const p of spec.params) {
    if (p.type === "symbol") continue;
    if (spec.id === "sma_cross" && (p.key === "fast" || p.key === "slow")) continue;
    const v = params[p.key];
    n *= Array.isArray(v) ? Math.max(1, v.length) : 1;
  }
  if (spec.id === "sma_cross") {
    const fast = Array.isArray(params.fast) ? (params.fast as number[]) : [];
    const slow = Array.isArray(params.slow) ? (params.slow as number[]) : [];
    let pairs = 0;
    for (const f of fast) for (const s of slow) if (f < s) pairs++;
    n *= Math.max(1, pairs);
  }
  return n;
}
