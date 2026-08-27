import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "./api";
import { CHART_REFRESH_MS } from "./config";
import NavChart from "./NavChart";
import SymbolSearch from "./SymbolSearch";
import VibePortfolioPanel from "./VibePortfolioPanel";
import {
  isImportedPortfolio,
  loadImportSort,
  loadPortfolioSort,
  PORTFOLIO_SORT_OPTIONS,
  portfolioSortFromId,
  portfolioSortId,
  saveImportSort,
  savePortfolioSort,
  sortPortfolios,
  STRATEGY_OPTIONS,
  type Portfolio,
  type PortfolioCostBasis,
  type PortfolioSummary,
  type PortfolioStrategyKind,
} from "./portfolio";
import { sessionTitle } from "./marketSession";
import type { Quote } from "./types";

function money(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 });
}
function pct(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}
function cls(n?: number | null) {
  if (n == null) return "";
  return n >= 0 ? "up" : "down";
}

function roundShares(n: number) {
  return Math.round(n * 1e6) / 1e6;
}

function fmtShares(n: number) {
  return n.toLocaleString(undefined, { maximumFractionDigits: 6 });
}

function stubFromSummary(s: PortfolioSummary): Portfolio {
  return {
    id: s.id,
    name: s.name,
    initial_cash: s.initial_cash,
    cash: s.cash,
    nav: s.nav,
    pnl: s.pnl,
    return_pct: s.return_pct,
    created_at: s.created_at,
    updated_at: s.updated_at,
    holdings: [],
    trades: [],
    snapshots: [],
    strategy: s.strategy,
    last_error: s.last_error,
    origin: s.origin,
    cost_basis: s.cost_basis,
  };
}

export default function PortfolioPanel({
  onOpenSymbol,
}: {
  onOpenSymbol: (symbol: string) => void;
}) {
  const [items, setItems] = useState<PortfolioSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Portfolio | null>(null);
  const [name, setName] = useState("");
  const [amount, setAmount] = useState("100000");
  const [importName, setImportName] = useState("");
  const [importCash, setImportCash] = useState("");
  const [importPaste, setImportPaste] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);
  const [importBasis, setImportBasis] = useState<PortfolioCostBasis | "">("");
  const [importNote, setImportNote] = useState<string | null>(null);
  const importFileRef = useRef<HTMLInputElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tradeSym, setTradeSym] = useState("AAPL");
  const [tradeSide, setTradeSide] = useState<"buy" | "sell">("buy");
  const [tradeQty, setTradeQty] = useState("");
  const [tradeNotional, setTradeNotional] = useState("");
  const [tradeQuote, setTradeQuote] = useState<Quote | null>(null);
  const [stratKind, setStratKind] = useState<PortfolioStrategyKind>("manual");
  const [stratAuto, setStratAuto] = useState(false);
  const [stratSym, setStratSym] = useState("SPY");
  const [fundSort, setFundSort] = useState(() => loadPortfolioSort());
  const [importSort, setImportSort] = useState(() => loadImportSort());
  const cacheRef = useRef(new Map<string, Portfolio>());

  const applyPortfolio = (p: Portfolio) => {
    cacheRef.current.set(p.id, p);
    setDetail(p);
    setStratKind(p.strategy?.kind || "manual");
    setStratAuto(!!p.strategy?.auto);
    setStratSym(p.strategy?.symbol || "SPY");
    if ((p.snapshots?.length || 0) > 0 || (p.holdings?.length || 0) > 0) {
      setItems((prev) =>
        prev.map((x) =>
          x.id === p.id
            ? {
                ...x,
                nav: p.nav,
                cash: p.cash,
                pnl: p.pnl,
                return_pct: p.return_pct,
                strategy: p.strategy,
                updated_at: p.updated_at,
                holdings_count: p.holdings?.length ?? x.holdings_count,
                last_error: p.last_error,
                origin: p.origin ?? x.origin,
                cost_basis: p.cost_basis ?? x.cost_basis,
              }
            : x,
        ),
      );
    }
  };

  const pickFund = (id: string) => {
    setSelectedId(id);
    setErr(null);
    const cached = cacheRef.current.get(id);
    if (cached) {
      applyPortfolio(cached);
      return;
    }
    const summary = items.find((x) => x.id === id);
    if (summary) applyPortfolio(stubFromSummary(summary));
  };

  const loadList = (liveMarks = false) =>
    api
      .portfolios({ live: liveMarks })
      .then((r) => {
        const next = r.items || [];
        setItems(next);
        setSelectedId((cur) => cur || next[0]?.id || null);
        if (liveMarks) {
          setDetail((cur) => {
            if (!cur) return cur;
            const row = next.find((x) => x.id === cur.id);
            if (!row) return cur;
            const snaps = [...(cur.snapshots || [])];
            const now = Math.floor(Date.now() / 1000);
            if (row.nav != null) {
              const last = snaps[snaps.length - 1];
              if (last && now - last.t < 25) {
                snaps[snaps.length - 1] = { ...last, t: now, nav: row.nav, cash: row.cash };
              } else {
                snaps.push({ t: now, nav: row.nav, cash: row.cash });
              }
            }
            return {
              ...cur,
              nav: row.nav,
              cash: row.cash,
              pnl: row.pnl,
              return_pct: row.return_pct,
              updated_at: row.updated_at,
              snapshots: snaps,
            };
          });
        }
      })
      .catch((e) => setErr(String(e.message || e)));

  useEffect(() => {
    let cancelled = false;
    loadList(false).then(() => {
      if (!cancelled) return loadList(true);
    });
    const id = setInterval(() => loadList(true), CHART_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let live = true;
    const accept = (p: Portfolio) => {
      if (!live || p.id !== selectedId) return;
      applyPortfolio(p);
    };
    api
      .portfolio(selectedId, { live: false })
      .then(accept)
      .catch((e) => live && setErr(String(e.message || e)))
      .finally(() => {
        if (!live) return;
        api.portfolio(selectedId).then(accept).catch(() => undefined);
      });
    const id = setInterval(() => {
      api.portfolio(selectedId).then(accept).catch(() => undefined);
    }, CHART_REFRESH_MS);
    return () => {
      live = false;
      clearInterval(id);
    };
  }, [selectedId]);

  const create = () => {
    const dollars = Number(amount.replace(/,/g, ""));
    if (!name.trim() || !Number.isFinite(dollars) || dollars <= 0) {
      setErr("Enter a fund name and a positive dollar amount.");
      return;
    }
    setBusy(true);
    setErr(null);
    api
      .createPortfolio(name.trim(), dollars)
      .then((p) => {
        setName("");
        setSelectedId(p.id);
        applyPortfolio(p);
        return loadList(true);
      })
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setBusy(false));
  };

  const importSnapshot = () => {
    const leftover = importCash.trim() ? Number(importCash.replace(/,/g, "")) : undefined;
    if (leftover != null && (!Number.isFinite(leftover) || leftover < 0)) {
      setErr("Leftover cash must be zero or a positive number.");
      return;
    }
    if (!importFile && !importPaste.trim()) {
      setErr("Upload a broker CSV or paste symbol, shares, average cost.");
      return;
    }
    if (importBasis !== "mark" && importBasis !== "csv") {
      setErr("Choose how to measure performance: import-time price or CSV cost basis.");
      return;
    }
    setBusy(true);
    setErr(null);
    setImportNote(null);
    api
      .importPortfolio({
        name: importName.trim() || importFile?.name.replace(/\.[^.]+$/, "") || "Broker snapshot",
        cash: leftover,
        file: importFile || undefined,
        csv: importPaste.trim() || undefined,
        costBasis: importBasis,
      })
      .then((p) => {
        setImportName("");
        setImportCash("");
        setImportPaste("");
        setImportFile(null);
        setImportBasis("");
        if (importFileRef.current) importFileRef.current.value = "";
        setSelectedId(p.id);
        applyPortfolio(p);
        if (p.import_note) setImportNote(p.import_note);
        return loadList(true);
      })
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setBusy(false));
  };

  const remove = (id: string) => {
    if (!window.confirm("Delete this stock portfolio?")) return;
    api
      .deletePortfolio(id)
      .then(() => {
        cacheRef.current.delete(id);
        if (selectedId === id) {
          setSelectedId(null);
          setDetail(null);
        }
        return loadList(true);
      })
      .catch((e) => setErr(String(e.message || e)));
  };

  const ticket = useMemo(() => {
    const cash = Number(detail?.cash ?? 0);
    const px = tradeQuote?.price != null && Number.isFinite(tradeQuote.price) && tradeQuote.price > 0
      ? tradeQuote.price
      : null;
    const qty = tradeQty.trim() ? Number(tradeQty) : NaN;
    const dollars = tradeNotional.trim() ? Number(tradeNotional) : NaN;
    let shares: number | null = null;
    if (Number.isFinite(qty) && qty > 0) shares = roundShares(qty);
    else if (Number.isFinite(dollars) && dollars > 0 && px) shares = roundShares(dollars / px);
    const total = shares != null && px != null ? roundShares(shares) * px : null;
    const sym = tradeSym.trim().toUpperCase();
    const held = detail?.holdings?.find((h) => h.symbol === sym)?.shares ?? 0;
    const maxBuy = px ? roundShares(cash / px) : null;
    const cashOk = total == null ? null : total <= cash + 0.01;
    const shortfall = total != null && cashOk === false ? total - cash : 0;
    const cashAfter = total == null ? null : tradeSide === "buy" ? cash - total : cash + total;
    const sharesOk = shares == null ? null : shares <= held + 1e-8;
    return { cash, px, shares, total, held, maxBuy, cashOk, shortfall, cashAfter, sharesOk, sym };
  }, [detail, tradeQuote, tradeQty, tradeNotional, tradeSym, tradeSide]);

  const submitTrade = () => {
    if (!selectedId) return;
    const shares = tradeQty.trim() ? Number(tradeQty) : undefined;
    const notional = tradeNotional.trim() ? Number(tradeNotional) : undefined;
    if (!tradeSym.trim() || (!shares && !notional)) {
      setErr("Enter a ticker and either shares or dollar amount.");
      return;
    }
    if (tradeSide === "buy" && ticket.total != null && ticket.cashOk === false) {
      setErr(
        `Insufficient cash (${money(ticket.cash)}) for a ${money(ticket.total)} buy. Need ${money(ticket.shortfall)} more.`,
      );
      return;
    }
    setBusy(true);
    setErr(null);
    api
      .portfolioOrder(selectedId, {
        symbol: tradeSym.trim().toUpperCase(),
        side: tradeSide,
        shares,
        notional,
      })
      .then((p) => {
        applyPortfolio(p);
        setTradeQty("");
        setTradeNotional("");
        return loadList(true);
      })
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setBusy(false));
  };

  const saveStrategy = () => {
    if (!selectedId) return;
    setBusy(true);
    setErr(null);
    api
      .setPortfolioStrategy(selectedId, {
        kind: stratKind,
        auto: stratKind !== "manual" && stratAuto,
        symbol: stratSym.trim().toUpperCase() || "SPY",
      })
      .then((p) => {
        applyPortfolio(p);
        return loadList(true);
      })
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setBusy(false));
  };

  const runNow = () => {
    if (!selectedId) return;
    setBusy(true);
    api
      .tickPortfolio(selectedId, true)
      .then((p) => {
        applyPortfolio(p);
        return loadList(true);
      })
      .catch((e) => setErr(String(e.message || e)))
      .finally(() => setBusy(false));
  };

  const hint = STRATEGY_OPTIONS.find((s) => s.id === stratKind)?.hint;
  const usesSymbol = STRATEGY_OPTIONS.find((s) => s.id === stratKind)?.usesSymbol !== false;
  const paperFunds = useMemo(
    () => sortPortfolios(items.filter((p) => !isImportedPortfolio(p)), fundSort),
    [items, fundSort],
  );
  const importedFunds = useMemo(
    () => sortPortfolios(items.filter((p) => isImportedPortfolio(p)), importSort),
    [items, importSort],
  );
  const fundName =
    (detail?.id === selectedId && detail?.name) || items.find((x) => x.id === selectedId)?.name || null;
  const mark = sessionTitle(detail?.id === selectedId ? detail?.mark_session : null);
  const importedDetail = detail ? isImportedPortfolio(detail) : false;
  const basisLabel =
    detail?.cost_basis === "mark" ? "import-time prices" : detail?.cost_basis === "csv" ? "CSV cost basis" : null;

  const renderFundRow = (p: PortfolioSummary) => (
    <div key={p.id} className={`row ${p.id === selectedId ? "sel" : ""}`}>
      <button type="button" className="row-main" onClick={() => pickFund(p.id)}>
        <span className="sym">{p.name}</span>
        <span>
          <div className="px">{money(p.nav)}</div>
          <div className={`meta ${cls(p.return_pct)}`}>{pct(p.return_pct)}</div>
        </span>
        <span className="muted">
          {isImportedPortfolio(p)
            ? p.cost_basis === "mark"
              ? "import px"
              : "csv basis"
            : p.strategy?.kind === "manual"
              ? "manual"
              : p.strategy?.kind}
        </span>
      </button>
      <button
        type="button"
        className="remove-btn"
        title="Delete portfolio"
        onClick={() => remove(p.id)}
      >
        ×
      </button>
    </div>
  );

  const sortSelect = (
    value: string,
    onChange: (id: string) => void,
    label: string,
  ) => (
    <label className="watch-sort">
      <span>Sort</span>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label={label}
      >
        {PORTFOLIO_SORT_OPTIONS.map((opt) => (
          <option key={opt.id} value={opt.id}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <div className="layout pf-layout">
      <aside className="col">
        <div className="section-h">
          Paper funds
          {sortSelect(portfolioSortId(fundSort), (id) => {
            const next = portfolioSortFromId(id);
            savePortfolioSort(next);
            setFundSort(next);
          }, "Sort paper funds")}
        </div>
        <div className="pf-create">
          <input
            value={name}
            placeholder="Fund name"
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
          <input
            value={amount}
            placeholder="Starting dollars"
            inputMode="decimal"
            onChange={(e) => setAmount(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && create()}
          />
          <button type="button" className="llm-btn" onClick={create} disabled={busy}>
            Create fund
          </button>
        </div>
        {paperFunds.length === 0 && (
          <div className="watch-empty">
            Create a paper fund with a name and starting cash. Saved on this machine only. US
            stocks and ETFs only — options are not supported.
          </div>
        )}
        {paperFunds.map(renderFundRow)}

        <div className="section-h pf-block-split">
          Imported snapshots
          {sortSelect(portfolioSortId(importSort), (id) => {
            const next = portfolioSortFromId(id);
            saveImportSort(next);
            setImportSort(next);
          }, "Sort imported snapshots")}
        </div>
        <div className="pf-create">
          <div className="muted pf-hint">
            Read-only copy from a broker export. Zintopia does not log into Robinhood, IBKR, or
            any broker. Stocks and ETFs only — options are skipped.
          </div>
          <input
            value={importName}
            placeholder="Snapshot name"
            onChange={(e) => setImportName(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && importSnapshot()}
          />
          <input
            ref={importFileRef}
            type="file"
            accept=".csv,.tsv,.txt,text/csv,text/tab-separated-values,text/plain"
            onChange={(e) => setImportFile(e.target.files?.[0] ?? null)}
            aria-label="Broker positions CSV"
          />
          <textarea
            className="pf-paste"
            rows={4}
            value={importPaste}
            placeholder={"Paste CSV or TSV, e.g.\nsymbol,shares,avg_cost\nAAPL,10,150"}
            onChange={(e) => setImportPaste(e.target.value)}
          />
          <input
            value={importCash}
            placeholder="Leftover cash (optional)"
            inputMode="decimal"
            onChange={(e) => setImportCash(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && importSnapshot()}
          />
          <fieldset className="pf-basis">
            <legend>Performance vs</legend>
            <label className="pf-check">
              <input
                type="radio"
                name="import-basis"
                checked={importBasis === "mark"}
                onChange={() => setImportBasis("mark")}
              />
              Price at import time
            </label>
            <label className="pf-check">
              <input
                type="radio"
                name="import-basis"
                checked={importBasis === "csv"}
                onChange={() => setImportBasis("csv")}
              />
              Actual cost basis from CSV
            </label>
          </fieldset>
          <button type="button" className="llm-btn" onClick={importSnapshot} disabled={busy}>
            Import snapshot
          </button>
          {importNote && <div className="muted pf-hint">{importNote}</div>}
        </div>
        {importedFunds.length === 0 && (
          <div className="watch-empty">
            Import a CSV snapshot from Robinhood, IBKR, Fidelity, or Schwab. Choose whether
            return is measured from the price at import or from the CSV cost basis.
          </div>
        )}
        {importedFunds.map(renderFundRow)}
      </aside>

      <main className="center">
        {err && <div className="err">{err}</div>}
        {!detail && <div className="watch-empty">Select or create a stock portfolio.</div>}
        {detail && (
          <>
            <div className="header">
              <div>
                <h1>{detail.name}</h1>
                <div className="name">
                  {importedDetail
                    ? `Imported snapshot · performance vs ${basisLabel || "cost basis"} · cash ${money(detail.cash)} · stock shares only, no options`
                    : `Started ${money(detail.initial_cash)} · cash ${money(detail.cash)} · stock shares only, no options`}
                  {mark ? ` · marked to Yahoo ${mark}` : ""}
                </div>
              </div>
              <div style={{ textAlign: "right" }}>
                <div className={`bigpx ${cls(detail.pnl)}`}>{money(detail.nav)}</div>
                <div className={cls(detail.pnl)}>
                  {money(detail.pnl)} ({pct(detail.return_pct)})
                </div>
              </div>
            </div>
            <div className="stats">
              <div className="stat">
                <div className="k">NAV</div>
                <div className="v">{money(detail.nav)}</div>
                {mark && <div className="muted">{mark}</div>}
              </div>
              <div className="stat">
                <div className="k">Cash</div>
                <div className="v">{money(detail.cash)}</div>
              </div>
              <div className="stat">
                <div className="k">P/L</div>
                <div className={`v ${cls(detail.pnl)}`}>{money(detail.pnl)}</div>
              </div>
              <div className="stat">
                <div className="k">Return</div>
                <div className={`v ${cls(detail.return_pct)}`}>{pct(detail.return_pct)}</div>
              </div>
              <div className="stat">
                <div className="k">Max DD</div>
                <div className="v">{pct(detail.max_drawdown_pct)}</div>
              </div>
              <div className="stat">
                <div className="k">Positions</div>
                <div className="v">{detail.holdings?.length ?? 0}</div>
              </div>
            </div>
            <div className="section-h">NAV over time</div>
            <div className="chart-wrap pf-chart">
              <NavChart snapshots={detail.snapshots || []} />
            </div>
            <VibePortfolioPanel
              portfolioId={detail.id}
              fundName={detail.name}
              onApply={(s) => {
                setTradeSym(s.symbol);
                if (s.action === "TRIM" || s.action === "EXIT") setTradeSide("sell");
                else if (s.action === "ADD") setTradeSide("buy");
              }}
            />
            <div className="section-h">Holdings</div>
            <table className="pf-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Shares</th>
                  <th>Avg cost</th>
                  <th>Last</th>
                  <th>Value</th>
                  <th>uP/L</th>
                </tr>
              </thead>
              <tbody>
                {(detail.holdings || []).length === 0 && (
                  <tr>
                    <td colSpan={6} className="muted">
                      No positions yet.
                    </td>
                  </tr>
                )}
                {(detail.holdings || []).map((h) => (
                  <tr key={h.symbol}>
                    <td>
                      <button type="button" className="linkish" onClick={() => onOpenSymbol(h.symbol)}>
                        {h.symbol}
                      </button>
                    </td>
                    <td>{h.shares}</td>
                    <td>{money(h.avg_cost)}</td>
                    <td>{money(h.last_price)}</td>
                    <td>{money(h.market_value)}</td>
                    <td className={cls(h.unrealized_pnl)}>{money(h.unrealized_pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </main>

      <aside className="col">
        <div className="pf-active">
          <div className="pf-active-k">Trading in</div>
          <div className="pf-active-name">{fundName || "No stock portfolio selected"}</div>
          {detail && detail.id === selectedId && (
            <div className="muted">
              Cash {money(detail.cash)} · NAV {money(detail.nav)}
              {mark ? ` · ${mark}` : ""}
              {detail.strategy?.kind && detail.strategy.kind !== "manual"
                ? ` · ${detail.strategy.kind}`
                : " · manual"}
            </div>
          )}
        </div>
        <div className="section-h">Simulate a stock trade</div>
        <div className="pf-form">
          <div className="pf-field">
            Ticker (stock / ETF)
            <SymbolSearch value={tradeSym} onChange={setTradeSym} onQuote={setTradeQuote} />
          </div>
          <div className="tabs">
            {(["buy", "sell"] as const).map((s) => (
              <button key={s} className={tradeSide === s ? "on" : ""} onClick={() => setTradeSide(s)}>
                {s}
              </button>
            ))}
          </div>
          <label>
            Shares
            <input
              value={tradeQty}
              placeholder="e.g. 10"
              onChange={(e) => setTradeQty(e.target.value)}
            />
          </label>
          <label>
            Or dollars
            <input
              value={tradeNotional}
              placeholder="e.g. 5000"
              onChange={(e) => setTradeNotional(e.target.value)}
            />
          </label>
          {ticket.px != null ? (
            <div className={`pf-preview${tradeSide === "buy" && ticket.cashOk === false ? " warn" : ""}`}>
              <div>
                <div className="k">Last</div>
                <div className="v">{money(ticket.px)}</div>
              </div>
              <div>
                <div className="k">Cash</div>
                <div className="v">{money(ticket.cash)}</div>
              </div>
              {ticket.shares != null && ticket.total != null ? (
                <>
                  <div>
                    <div className="k">{tradeSide === "buy" ? "Buy" : "Sell"}</div>
                    <div className="v">
                      {fmtShares(ticket.shares)} sh × {money(ticket.px)}
                    </div>
                  </div>
                  <div>
                    <div className="k">Total</div>
                    <div className="v">{money(ticket.total)}</div>
                  </div>
                  {tradeSide === "buy" ? (
                    <>
                      <div>
                        <div className="k">Cash after</div>
                        <div className={`v ${ticket.cashOk ? "" : "down"}`}>
                          {money(ticket.cashAfter)}
                        </div>
                      </div>
                      <div className={`pf-preview-msg ${ticket.cashOk ? "up" : "down"}`}>
                        {ticket.cashOk
                          ? "Cash is sufficient"
                          : `Not enough cash · need ${money(ticket.shortfall)} more`}
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <div className="k">Held / after</div>
                        <div className={`v ${ticket.sharesOk === false ? "down" : ""}`}>
                          {fmtShares(ticket.held)} → {fmtShares(Math.max(0, ticket.held - ticket.shares))}
                        </div>
                      </div>
                      <div className={`pf-preview-msg ${ticket.sharesOk === false ? "down" : "up"}`}>
                        {ticket.sharesOk === false
                          ? `Not enough shares · holding ${fmtShares(ticket.held)}`
                          : `Proceeds ${money(ticket.total)} · cash after ${money(ticket.cashAfter)}`}
                      </div>
                    </>
                  )}
                </>
              ) : (
                <div className="pf-preview-msg muted">
                  {ticket.maxBuy != null && ticket.maxBuy > 0
                    ? `Enter shares or dollars. Cash covers about ${fmtShares(ticket.maxBuy)} shares.`
                    : "Enter shares or a dollar amount."}
                </div>
              )}
            </div>
          ) : (
            <div className="muted pf-hint">Last price loads from the ticker to size the order.</div>
          )}
          <button
            type="button"
            className="llm-btn"
            onClick={submitTrade}
            disabled={
              busy ||
              !detail ||
              (tradeSide === "buy" && ticket.cashOk === false) ||
              (tradeSide === "sell" && ticket.sharesOk === false)
            }
          >
            {tradeSide === "buy" && ticket.total != null && ticket.sym
              ? `Buy ${ticket.sym} for ${money(ticket.total)}`
              : tradeSide === "sell" && ticket.total != null && ticket.sym
                ? `Sell ${ticket.sym} for ${money(ticket.total)}`
                : fundName
                  ? `Place stock order in ${fundName}`
                  : "Place stock order"}
          </button>
          <div className="muted pf-hint">US stocks and ETFs only. Options are not supported.</div>
        </div>

        <div className="section-h">Quant strategy</div>
        <div className="pf-form">
          <label>
            Strategy
            <select
              value={stratKind}
              onChange={(e) => setStratKind(e.target.value as PortfolioStrategyKind)}
            >
              {STRATEGY_OPTIONS.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          {hint && <div className="muted pf-hint">{hint}</div>}
          {usesSymbol && stratKind !== "manual" && (
            <div className="pf-field">
              {stratKind === "dual_momentum" ? "Risk-on ticker" : "Symbol"}
              <SymbolSearch value={stratSym} onChange={setStratSym} />
            </div>
          )}
          {stratKind !== "manual" && (
            <label className="pf-check">
              <input type="checkbox" checked={stratAuto} onChange={(e) => setStratAuto(e.target.checked)} />
              Run automatically every hour while the terminal is up
            </label>
          )}
          <button type="button" className="llm-btn" onClick={saveStrategy} disabled={busy || !detail}>
            {fundName ? `Save strategy for ${fundName}` : "Save strategy"}
          </button>
          {stratKind !== "manual" && (
            <button type="button" className="ghost-btn" onClick={runNow} disabled={busy || !detail}>
              Run one step now
            </button>
          )}
          {detail?.strategy?.note && <div className="muted pf-hint">Last: {detail.strategy.note}</div>}
          {detail?.strategy?.auto && detail.strategy.next_run_at ? (
            <div className="muted pf-hint">
              Next automatic run around {new Date(detail.strategy.next_run_at * 1000).toLocaleString()}
            </div>
          ) : null}
          {detail?.last_error && <div className="err">{detail.last_error}</div>}
        </div>

        <div className="section-h">Trade log</div>
        <div className="news">
          {(detail?.trades || [])
            .slice()
            .reverse()
            .slice(0, 40)
            .map((t, i) => (
              <div key={`${t.t}-${i}`} className="pf-trade">
                <div>
                  <b className={t.side === "buy" ? "up" : "down"}>{t.side.toUpperCase()}</b> {t.shares}{" "}
                  {t.symbol} @ {money(t.price)}
                </div>
                <div className="src">
                  {t.source} · {new Date(t.t * 1000).toLocaleString()}
                </div>
              </div>
            ))}
          {(!detail?.trades || detail.trades.length === 0) && (
            <div className="watch-empty">No trades yet.</div>
          )}
        </div>
      </aside>
    </div>
  );
}
