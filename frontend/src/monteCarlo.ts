export type McAssetClass = { id: string; name: string; symbol: string };
export type McLazy = { id: string; name: string; weights: Record<string, number> };

export type McAssetRow = {
  asset_id: string;
  symbol: string;
  weight: string;
  mean: string;
  volatility: string;
};

export type McMeta = {
  asset_classes: McAssetClass[];
  lazy_portfolios: McLazy[];
  max_assets: number;
  max_sims: number;
  history?: string;
  disclaimer?: string;
};

export type McResult = {
  ok: boolean;
  note?: string | null;
  disclaimer?: string;
  n_sims: number;
  months: number;
  years: number;
  history_months: number;
  history_start?: string | null;
  history_end?: string | null;
  source?: string;
  assets: { label: string; symbol: string; weight: number; mean: number; volatility: number }[];
  years_axis: number[];
  percentiles: Record<string, number[]>;
  yearly: Record<string, number>[];
  success_rate: number;
  terminal: Record<string, number | null>;
  cagr: Record<string, number | null>;
  max_drawdown: Record<string, number | null>;
  portfolio_sample?: { mean: number; volatility: number };
};

export const EMPTY_ROW = (): McAssetRow => ({
  asset_id: "",
  symbol: "",
  weight: "",
  mean: "",
  volatility: "",
});

export function rowsFromLazy(lazy: McLazy, n = 10): McAssetRow[] {
  const entries = Object.entries(lazy.weights);
  const rows = Array.from({ length: n }, () => EMPTY_ROW());
  entries.forEach(([id, w], i) => {
    if (i >= n) return;
    rows[i] = { asset_id: id, symbol: "", weight: String(w), mean: "", volatility: "" };
  });
  return rows;
}

export function rowsFromHoldings(
  holdings: { symbol: string; market_value?: number | null; shares?: number; last_price?: number | null }[],
  cash: number,
  n = 10,
): McAssetRow[] {
  const parts: { symbol: string; mv: number }[] = [];
  for (const h of holdings) {
    const sym = (h.symbol || "").trim().toUpperCase();
    let mv = h.market_value;
    if (mv == null && h.shares != null && h.last_price != null) mv = h.shares * h.last_price;
    if (sym && mv != null && mv > 0) parts.push({ symbol: sym, mv });
  }
  if (cash > 0) parts.push({ symbol: "SHV", mv: cash });
  parts.sort((a, b) => b.mv - a.mv);
  const top = parts.slice(0, n);
  const total = top.reduce((s, p) => s + p.mv, 0) || 1;
  const rows = Array.from({ length: n }, () => EMPTY_ROW());
  top.forEach((p, i) => {
    rows[i] = {
      asset_id: "",
      symbol: p.symbol,
      weight: ((100 * p.mv) / total).toFixed(2),
      mean: "",
      volatility: "",
    };
  });
  return rows;
}

export function equalWeight(rows: McAssetRow[], filled: number): McAssetRow[] {
  const n = Math.max(1, filled);
  const w = (100 / n).toFixed(2);
  return rows.map((r, i) => (i < filled || r.asset_id || r.symbol ? { ...r, weight: w } : r));
}

export function normalizeWeights(rows: McAssetRow[]): McAssetRow[] {
  const nums = rows.map((r) => Number(r.weight) || 0);
  const s = nums.reduce((a, b) => a + b, 0);
  if (s <= 0) return rows;
  return rows.map((r, i) => (nums[i] > 0 ? { ...r, weight: ((100 * nums[i]) / s).toFixed(2) } : r));
}
