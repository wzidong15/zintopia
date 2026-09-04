const KEY = "zintopia.watchlist";
const SORT_KEY = "zintopia.watchlist.sort";
const LEGACY_KEYS = ["fintopia.watchlist", "utopia.watchlist"];

export const DEFAULT_WATCHLIST = [
  "AAPL",
  "MSFT",
  "NVDA",
  "AMZN",
  "GOOGL",
  "META",
  "TSLA",
  "JPM",
  "UNH",
  "XOM",
];

export type WatchSortBy = "added" | "name" | "pct";
export type WatchSortDir = "asc" | "desc";
export type WatchSort = { by: WatchSortBy; dir: WatchSortDir };

const DEFAULT_SORT: WatchSort = { by: "added", dir: "asc" };

export const WATCH_SORT_OPTIONS: { id: string; by: WatchSortBy; dir: WatchSortDir; label: string }[] = [
  { id: "added", by: "added", dir: "asc", label: "Added order" },
  { id: "name-asc", by: "name", dir: "asc", label: "Ticker A to Z" },
  { id: "name-desc", by: "name", dir: "desc", label: "Ticker Z to A" },
  { id: "pct-desc", by: "pct", dir: "desc", label: "Day % high to low" },
  { id: "pct-asc", by: "pct", dir: "asc", label: "Day % low to high" },
];

export function watchSortId(sort: WatchSort): string {
  if (sort.by === "added") return "added";
  return `${sort.by}-${sort.dir}`;
}

export function watchSortFromId(id: string): WatchSort {
  const opt = WATCH_SORT_OPTIONS.find((o) => o.id === id);
  return opt ? { by: opt.by, dir: opt.dir } : { ...DEFAULT_SORT };
}

export function loadWatchlist(): string[] {
  try {
    const raw = localStorage.getItem(KEY) ?? LEGACY_KEYS.map((k) => localStorage.getItem(k)).find(Boolean);
    if (!raw) return [...DEFAULT_WATCHLIST];
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed) && parsed.every((x) => typeof x === "string")) {
      return [...new Set(parsed.map((s) => s.trim().toUpperCase()).filter(Boolean))];
    }
  } catch {
    /* ignore */
  }
  return [...DEFAULT_WATCHLIST];
}

export function saveWatchlist(symbols: string[]) {
  localStorage.setItem(KEY, JSON.stringify(symbols));
}

export function removeFromWatchlist(symbols: string[], symbol: string): string[] {
  const sym = symbol.trim().toUpperCase();
  return symbols.filter((s) => s !== sym);
}

export function toggleWatchlistSymbol(symbols: string[], symbol: string): string[] {
  const sym = symbol.trim().toUpperCase();
  if (!sym) return symbols;
  if (symbols.includes(sym)) return symbols.filter((s) => s !== sym);
  return [...symbols, sym];
}

export function loadWatchSort(): WatchSort {
  try {
    const raw = localStorage.getItem(SORT_KEY);
    if (!raw) return { ...DEFAULT_SORT };
    const parsed = JSON.parse(raw) as Partial<WatchSort>;
    const by: WatchSortBy =
      parsed.by === "name" || parsed.by === "pct" || parsed.by === "added" ? parsed.by : "added";
    const dir: WatchSortDir = parsed.dir === "desc" ? "desc" : "asc";
    return { by, dir: by === "added" ? "asc" : dir };
  } catch {
    return { ...DEFAULT_SORT };
  }
}

export function saveWatchSort(sort: WatchSort) {
  localStorage.setItem(SORT_KEY, JSON.stringify(sort));
}

function asWatchSort(raw: unknown): WatchSort {
  if (!raw || typeof raw !== "object") return { ...DEFAULT_SORT };
  const parsed = raw as Partial<WatchSort>;
  const by: WatchSortBy =
    parsed.by === "name" || parsed.by === "pct" || parsed.by === "added" ? parsed.by : "added";
  const dir: WatchSortDir = parsed.dir === "desc" ? "desc" : "asc";
  return { by, dir: by === "added" ? "asc" : dir };
}

export function sameWatchlist(a: string[], b: string[]) {
  return a.length === b.length && a.every((s, i) => s === b[i]);
}

export function isDefaultWatchlist(symbols: string[]) {
  return sameWatchlist(symbols, DEFAULT_WATCHLIST);
}

export function isDefaultWatchSort(sort: WatchSort) {
  return sort.by === "added";
}

export function hasFactoryWatchlist(symbols: string[]) {
  return DEFAULT_WATCHLIST.every((s) => symbols.includes(s));
}

export function mergeWatchState(
  server: { symbols?: string[]; sort?: WatchSort; persisted?: boolean },
  localSymbols: string[],
  localSort: WatchSort,
): { symbols: string[]; sort: WatchSort; shouldSave: boolean } {
  const serverSort = asWatchSort(server.sort);
  if (!server.persisted) {
    const useLocal = !isDefaultWatchlist(localSymbols) || !isDefaultWatchSort(localSort);
    if (useLocal) return { symbols: localSymbols, sort: localSort, shouldSave: true };
    return { symbols: [...DEFAULT_WATCHLIST], sort: { ...DEFAULT_SORT }, shouldSave: false };
  }
  const serverSymbols = [
    ...new Set((server.symbols || []).map((s) => s.trim().toUpperCase()).filter(Boolean)),
  ];
  let sort = serverSort;
  let shouldSave = false;
  if (isDefaultWatchSort(serverSort) && !isDefaultWatchSort(localSort)) {
    sort = localSort;
    shouldSave = true;
  }
  const extras = hasFactoryWatchlist(localSymbols)
    ? localSymbols.filter((s) => !DEFAULT_WATCHLIST.includes(s))
    : localSymbols;
  const ordered = [...serverSymbols];
  for (const s of extras) {
    const sym = s.trim().toUpperCase();
    if (sym && !ordered.includes(sym)) {
      ordered.push(sym);
      shouldSave = true;
    }
  }
  return { symbols: ordered, sort, shouldSave };
}

export function defaultWatchSymbol(symbols: string[], sort: WatchSort = DEFAULT_SORT): string {
  if (!symbols.length) return "AAPL";
  if (sort.by === "name") {
    const copy = [...symbols].sort((a, b) => (sort.dir === "asc" ? 1 : -1) * a.localeCompare(b));
    return copy[0] || "AAPL";
  }
  return symbols[0] || "AAPL";
}

export function sortWatchlist<T extends { symbol: string; name?: string; change_pct?: number | null }>(
  quotes: T[],
  addedOrder: string[],
  sort: WatchSort,
): T[] {
  const index = new Map(addedOrder.map((s, i) => [s, i]));
  const rows = [...quotes];
  const mul = sort.dir === "asc" ? 1 : -1;
  rows.sort((a, b) => {
    if (sort.by === "name") {
      const an = (a.symbol || a.name || "").toUpperCase();
      const bn = (b.symbol || b.name || "").toUpperCase();
      return mul * an.localeCompare(bn);
    }
    if (sort.by === "pct") {
      const av = a.change_pct;
      const bv = b.change_pct;
      if (av == null && bv == null) return (a.symbol || "").localeCompare(b.symbol || "");
      if (av == null) return 1;
      if (bv == null) return -1;
      if (av === bv) return (a.symbol || "").localeCompare(b.symbol || "");
      return mul * (av - bv);
    }
    return (index.get(a.symbol) ?? 999) - (index.get(b.symbol) ?? 999);
  });
  return rows;
}
