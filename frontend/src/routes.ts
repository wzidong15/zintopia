export type View = "research" | "portfolios" | "montecarlo" | "backtest";

export const VIEW_PATHS: Record<View, string> = {
  research: "/",
  portfolios: "/portfolio",
  montecarlo: "/monte-carlo",
  backtest: "/backtest",
};

export const VIEW_TITLES: Record<View, string> = {
  research: "Research",
  portfolios: "Stock portfolio",
  montecarlo: "Portfolio MC Simulation",
  backtest: "Backtester",
};

const ALIASES: Record<string, View> = {
  "/": "research",
  "/research": "research",
  "/portfolio": "portfolios",
  "/portfolios": "portfolios",
  "/monte-carlo": "montecarlo",
  "/montecarlo": "montecarlo",
  "/mc": "montecarlo",
  "/backtest": "backtest",
  "/backtester": "backtest",
};

export function viewFromPath(pathname: string): View {
  const clean = pathname.replace(/\/+$/, "") || "/";
  return ALIASES[clean.toLowerCase()] || "research";
}

export function currentView(): View {
  if (typeof window === "undefined") return "research";
  return viewFromPath(window.location.pathname);
}

export function setViewTitle(view: View) {
  if (typeof document === "undefined") return;
  document.title = view === "research" ? "Zintopia" : `${VIEW_TITLES[view]} · Zintopia`;
}

/** Push the tab's path onto browser history (no-op when already there). */
export function pushView(view: View) {
  if (typeof window === "undefined") return;
  const path = VIEW_PATHS[view];
  if (window.location.pathname !== path) {
    window.history.pushState({ view }, "", path + window.location.search);
  }
  setViewTitle(view);
}
