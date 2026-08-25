import type { Profile, Quote } from "./types";

export function fmt(n?: number | null, d = 2) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: d, minimumFractionDigits: d });
}

export function fmtInt(n?: number | null) {
  if (n == null) return "—";
  if (Math.abs(n) >= 1e12) return `${(n / 1e12).toFixed(2)}T`;
  if (Math.abs(n) >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
  if (Math.abs(n) >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
  if (Math.abs(n) >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
  return n.toLocaleString();
}

export function money(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e12) return `${sign}$${(abs / 1e12).toFixed(2)}T`;
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}

export function pct(n?: number | null) {
  if (n == null) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

export function cls(n?: number | null) {
  if (n == null) return "";
  return n >= 0 ? "up" : "down";
}

export function numish(v: unknown): number | null {
  if (typeof v === "number" && Number.isFinite(v)) return v;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

export function quoteSourceLabel(source?: string | null): string | null {
  const s = (source || "").toLowerCase();
  if (!s) return null;
  if (s.includes("polygon") || s.includes("massive")) return "Polygon";
  if (s.includes("yahoo") || s.includes("yfinance")) return "Yahoo";
  if (s.includes("tradingview") || s.includes("tv")) return "TradingView";
  return source || null;
}

export function dividendYieldPct(quote: Quote | null, profile: Profile | null): number | null {
  if (quote?.dividend_yield != null && Number.isFinite(quote.dividend_yield)) {
    const n = quote.dividend_yield;
    if ((quote.source || "").includes("yfinance") && n <= 1) return n * 100;
    return n;
  }
  const y = numish(profile?.dividendYield);
  if (y == null) return null;
  return y <= 1 ? y * 100 : y;
}

export const MARKET_TZ = "America/New_York";

type ChartTime = number | { year: number; month: number; day: number };

function chartTimeMs(time: ChartTime): number {
  if (typeof time === "number") return time * 1000;
  return Date.UTC(time.year, time.month - 1, time.day);
}

function etParts(time: ChartTime, opts: Intl.DateTimeFormatOptions): Record<string, string> {
  const bag: Record<string, string> = {};
  for (const p of new Intl.DateTimeFormat("en-US", { timeZone: MARKET_TZ, ...opts }).formatToParts(new Date(chartTimeMs(time)))) {
    if (p.type !== "literal") bag[p.type] = p.value;
  }
  return bag;
}

/** Crosshair time label in America/New_York. Daily bars omit midnight. */
export function formatChartTime(time: ChartTime): string {
  const p = etParts(time, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    hourCycle: "h12",
  });
  const date = `${p.month} ${p.day}, ${p.year}`;
  const hour = Number(p.hour);
  const minute = p.minute || "00";
  const am = (p.dayPeriod || "").toLowerCase().startsWith("a");
  if (hour === 12 && am && minute === "00") return date;
  return `${date} ${hour}:${minute} ${p.dayPeriod} ET`;
}

/** Time-axis ticks in America/New_York. tickMarkType matches lightweight-charts TickMarkType. */
export function formatChartTick(time: ChartTime, tickMarkType: number): string {
  if (tickMarkType === 0) return etParts(time, { year: "numeric" }).year;
  if (tickMarkType === 1) return etParts(time, { month: "short" }).month;
  if (tickMarkType === 2) {
    const p = etParts(time, { month: "short", day: "numeric" });
    return `${p.month} ${p.day}`;
  }
  const p = etParts(time, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
    hour12: true,
    hourCycle: "h12",
  });
  if (tickMarkType === 4) return `${p.hour}:${p.minute}:${p.second} ${p.dayPeriod}`;
  return `${p.hour}:${p.minute} ${p.dayPeriod}`;
}

export function pctFrac(n?: number | null, d = 2) {
  if (n == null || Number.isNaN(n)) return "—";
  const p = Math.abs(n) <= 1.5 ? n * 100 : n;
  return `${p.toFixed(d)}%`;
}

export function fmtNewsTime(ts: unknown): string {
  const n = numish(ts);
  if (n == null) {
    const s = typeof ts === "string" ? ts.trim() : "";
    return s ? s.slice(0, 16) : "";
  }
  const ms = n < 1e12 ? n * 1000 : n;
  const age = Date.now() - ms;
  if (age >= 0 && age < 60_000) return `${Math.max(1, Math.round(age / 1000))}s ago`;
  if (age >= 0 && age < 3_600_000) return `${Math.round(age / 60_000)}m ago`;
  if (age >= 0 && age < 36 * 3_600_000) return `${Math.round(age / 3_600_000)}h ago`;
  return new Date(ms).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZone: MARKET_TZ,
  });
}

export function fmtEarnings(ts: unknown): string {
  const n = numish(ts);
  if (n == null) return "—";
  const ms = n < 1e12 ? n * 1000 : n;
  return new Date(ms).toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    timeZone: "America/New_York",
  });
}

export function rvol(q: Quote): number | null {
  const avg = q.avg_volume;
  if (q.volume == null || avg == null || avg <= 0) return null;
  return q.volume / avg;
}
