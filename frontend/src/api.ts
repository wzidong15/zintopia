import type { DeepAnalysis } from "./deep";
import type { Fundamentals, PeerList, ScreenerResult } from "./fundamentals";
import type { Ownership } from "./OwnershipPanel";
import type { LlmAdviceChatResponse, LlmAdviceResponse, VibePortfolioChatResponse, VibePortfolioResponse } from "./llm";
import type { Portfolio, PortfolioStrategyKind, PortfolioSummary } from "./portfolio";
import type { Bar, NewsItem, Profile, Quote, TA } from "./types";

function errorFromBody(text: string, fallback: string) {
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail.trim()) return body.detail;
  } catch {
    /* raw text */
  }
  return text || fallback;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(path);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(errorFromBody(text, res.statusText));
  }
  return res.json() as Promise<T>;
}

async function sendJson<T>(path: string, method: string, body?: unknown, signal?: AbortSignal): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(errorFromBody(text, res.statusText));
  }
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export function isAbortError(e: unknown) {
  return (
    (e instanceof DOMException && e.name === "AbortError") ||
    (e instanceof Error && e.name === "AbortError")
  );
}

export type SearchHit = {
  symbol: string;
  name: string;
  exchange?: string;
  type?: string;
  price?: number;
  change_pct?: number;
};

export const api = {
  health: () =>
    getJson<{
      ok: boolean;
      polygon: boolean;
      llm: { openai: boolean; anthropic: boolean; any: boolean };
      market?: {
        session: string;
        label: string;
        hours: string;
        time_et: string;
        tz: string;
      };
    }>("/api/health"),
  indices: () => getJson<{ items: Quote[] }>("/api/indices"),
  snapshot: () =>
    getJson<{
      indices: Quote[];
      gainers: Quote[];
      losers: Quote[];
      active: Quote[];
      as_of: number;
      errors?: Record<string, string>;
    }>("/api/snapshot"),
  quote: (symbol: string) => getJson<Quote>(`/api/quote/${encodeURIComponent(symbol)}`),
  quotes: (symbols: string[]) =>
    getJson<{ items: Quote[] }>(`/api/quotes?symbols=${encodeURIComponent(symbols.join(","))}`),
  movers: (kind: "gainers" | "losers" | "active") =>
    getJson<{ kind: string; items: Quote[]; error?: string }>(`/api/movers?kind=${kind}`),
  history: (symbol: string, range: string) =>
    getJson<{ bars: Bar[]; interval: string; source: string }>(
      `/api/history/${encodeURIComponent(symbol)}?range=${range}`,
    ),
  profile: (symbol: string) => getJson<Profile>(`/api/profile/${encodeURIComponent(symbol)}`),
  fundamentals: (symbol: string) =>
    getJson<Fundamentals>(`/api/fundamentals/${encodeURIComponent(symbol)}`),
  peers: (symbol: string) => getJson<PeerList>(`/api/peers/${encodeURIComponent(symbol)}`),
  ownership: (symbol: string) =>
    getJson<Ownership>(`/api/ownership/${encodeURIComponent(symbol)}`),
  screener: (query: string) => getJson<ScreenerResult>(`/api/screener${query ? `?${query}` : ""}`),
  news: (symbol: string) =>
    getJson<{ items: NewsItem[] }>(`/api/news/${encodeURIComponent(symbol)}`),
  marketNews: () => getJson<{ items: NewsItem[]; source?: string }>("/api/market-news?limit=24"),
  ta: (symbol: string) => getJson<TA>(`/api/ta/${encodeURIComponent(symbol)}?interval=1d`),
  deep: (symbol: string) => getJson<DeepAnalysis>(`/api/deep/${encodeURIComponent(symbol)}`),
  llmAdvice: (symbol: string, signal?: AbortSignal): Promise<LlmAdviceResponse> =>
    sendJson<LlmAdviceResponse>(`/api/llm-advice/${encodeURIComponent(symbol)}`, "POST", undefined, signal),
  llmAdviceChat: (
    symbol: string,
    conversationId: string,
    message: string,
    signal?: AbortSignal,
  ): Promise<LlmAdviceChatResponse> =>
    sendJson<LlmAdviceChatResponse>(
      `/api/llm-advice/${encodeURIComponent(symbol)}/chat`,
      "POST",
      {
        conversation_id: conversationId,
        message,
      },
      signal,
    ),
  search: (q: string) => getJson<{ items: SearchHit[] }>(`/api/search?q=${encodeURIComponent(q)}`),
  portfolios: (opts?: { live?: boolean }) =>
    getJson<{ items: PortfolioSummary[] }>(
      `/api/portfolios${opts?.live ? "?live=true" : ""}`,
    ),
  portfolio: (id: string, opts?: { live?: boolean }) =>
    getJson<Portfolio>(
      `/api/portfolios/${encodeURIComponent(id)}${opts?.live === false ? "?live=false" : ""}`,
    ),
  createPortfolio: (name: string, amount: number) =>
    sendJson<Portfolio>("/api/portfolios", "POST", { name, amount }),
  importPortfolio: async (opts: {
    name: string;
    cash?: number;
    file?: File;
    csv?: string;
    costBasis: "mark" | "csv";
  }) => {
    const fd = new FormData();
    fd.append("name", opts.name);
    fd.append("cost_basis", opts.costBasis);
    if (opts.cash != null && Number.isFinite(opts.cash)) fd.append("cash", String(opts.cash));
    if (opts.file) fd.append("file", opts.file);
    if (opts.csv) fd.append("csv_text", opts.csv);
    const res = await fetch("/api/portfolios/import", { method: "POST", body: fd });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(errorFromBody(text, res.statusText));
    }
    return res.json() as Promise<Portfolio>;
  },
  deletePortfolio: (id: string) =>
    sendJson<{ ok: boolean }>(`/api/portfolios/${encodeURIComponent(id)}`, "DELETE"),
  portfolioOrder: (
    id: string,
    body: { symbol: string; side: "buy" | "sell"; shares?: number; notional?: number },
  ) => sendJson<Portfolio>(`/api/portfolios/${encodeURIComponent(id)}/orders`, "POST", body),
  setPortfolioStrategy: (
    id: string,
    body: { kind: PortfolioStrategyKind; auto: boolean; symbol: string },
  ) => sendJson<Portfolio>(`/api/portfolios/${encodeURIComponent(id)}/strategy`, "PUT", body),
  tickPortfolio: (id: string, force = false) =>
    sendJson<Portfolio>(
      `/api/portfolios/${encodeURIComponent(id)}/tick${force ? "?force=true" : ""}`,
      "POST",
    ),
  vibePortfolio: (id: string, signal?: AbortSignal) =>
    sendJson<VibePortfolioResponse>(`/api/portfolios/${encodeURIComponent(id)}/vibe`, "POST", undefined, signal),
  vibePortfolioChat: (id: string, conversationId: string, message: string, signal?: AbortSignal) =>
    sendJson<VibePortfolioChatResponse>(
      `/api/portfolios/${encodeURIComponent(id)}/vibe/chat`,
      "POST",
      {
        conversation_id: conversationId,
        message,
      },
      signal,
    ),
};
