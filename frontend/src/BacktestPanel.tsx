import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import BacktestChart, { type BtLine } from "./BacktestChart";
import {
  RANK_LABELS,
  defaultParamText,
  gridSize,
  paramTextFor,
  paramsFromText,
  parseSymbols,
  rangeLabel,
  searchSpaceFromText,
  type BtMeta,
  type BtResult,
  type BtRun,
  type BtStrategySpec,
} from "./backtest";
import type { PortfolioSummary } from "./portfolio";

const COLORS = ["#56d364", "#58a6ff", "#d2a8ff", "#f0883e", "#ff7b72", "#79c0ff"];

function money(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}
function pct(n?: number | null, d = 2) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(d)}%`;
}
function num(n?: number | null, d = 2) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toFixed(d);
}
function tone(n?: number | null) {
  if (n == null) return "";
  return n > 0 ? "up" : n < 0 ? "down" : "";
}
function signed(n?: number | null, d = 1) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n > 0 ? "+" : ""}${n.toFixed(d)}`;
}

type HistoryItem = { id: string; label: string; result: BtResult };
type ResultTab = "overview" | "ranking" | "walkforward" | "search" | "trades" | "notes";

/** One to three plain sentences that say what the numbers mean. */
function verdict(result: BtResult, selected: BtRun): string[] {
  const out: string[] = [];
  const b = result.benchmark;
  const wf = result.walk_forward;
  const ddSaved = b.max_drawdown != null && selected.max_drawdown != null ? selected.max_drawdown - b.max_drawdown : null;
  if (wf && wf.oos.cagr != null && wf.oos_benchmark.cagr != null) {
    const gap = wf.oos.cagr - wf.oos_benchmark.cagr;
    const dd =
      wf.oos.max_drawdown != null && wf.oos_benchmark.max_drawdown != null ? wf.oos.max_drawdown - wf.oos_benchmark.max_drawdown : null;
    out.push(
      `Out of sample (${wf.oos_start} → ${wf.oos_end}) the re-selected rule ${gap >= 0 ? "beat" : "trailed"} buy & hold by ${Math.abs(gap).toFixed(1)} points a year` +
        (dd != null
          ? `${dd > 0 ? " and cut the worst drawdown from" : " while the worst drawdown went from"} ${pct(wf.oos_benchmark.max_drawdown, 0)} to ${pct(wf.oos.max_drawdown, 0)}.`
          : "."),
    );
    if (wf.efficiency != null) {
      out.push(
        wf.efficiency >= 0.5
          ? `It kept ${(wf.efficiency * 100).toFixed(0)}% of its in-sample edge, a reasonable sign the rule is not just fitted noise.`
          : `It kept only ${(wf.efficiency * 100).toFixed(0)}% of its in-sample edge: most of the back-tested return did not survive, the usual signature of over-fitting.`,
      );
    }
  } else {
    out.push(
      `In sample, #${selected.rank} ${selected.label} ${selected.excess_cagr >= 0 ? "beat" : "trailed"} buy & hold by ${Math.abs(selected.excess_cagr).toFixed(1)} points a year` +
        (ddSaved != null
          ? `${ddSaved > 0 ? " and cut the worst drawdown from" : " while the worst drawdown went from"} ${pct(b.max_drawdown, 0)} to ${pct(selected.max_drawdown, 0)}.`
          : "."),
    );
    out.push("No walk-forward was run, so this is one in-sample path and the best cell is the most over-fit one.");
  }
  const top = result.search?.top[0];
  if (top && top.id === selected.id) {
    out.push(
      top.stable
        ? "The winner sits on a plateau: neighbouring parameter values score about as well."
        : "The winner is a spike: neighbouring parameter values score much worse, so prefer a stable row on the Search tab.",
    );
  }
  return out;
}

export default function BacktestPanel({ onOpenSymbol }: { onOpenSymbol?: (symbol: string) => void }) {
  const [meta, setMeta] = useState<BtMeta | null>(null);
  const [funds, setFunds] = useState<PortfolioSummary[]>([]);
  const [strategyId, setStrategyId] = useState("sma_cross");
  const [paramText, setParamText] = useState<Record<string, string>>({});
  const [symbolsText, setSymbolsText] = useState("SPY, QQQ");
  const [start, setStart] = useState("2017-01-01");
  const [end, setEnd] = useState("");
  const [cash, setCash] = useState("100000");
  const [fees, setFees] = useState("5");
  const [slip, setSlip] = useState("10");
  const [rankBy, setRankBy] = useState("sharpe");
  const [presetId, setPresetId] = useState("");
  const [fundId, setFundId] = useState("");
  const [busy, setBusy] = useState<"" | "grid" | "all" | "opt">("");
  const [budget, setBudget] = useState("200");
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<BtResult | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [logScale, setLogScale] = useState(false);
  const [advanced, setAdvanced] = useState(true);
  const [tab, setTab] = useState<ResultTab>("overview");
  const [wfOn, setWfOn] = useState(true);
  const [wfFolds, setWfFolds] = useState("4");
  const [wfTrain, setWfTrain] = useState("50");
  const [wfMode, setWfMode] = useState<"anchored" | "rolling">("anchored");

  useEffect(() => {
    let live = true;
    api
      .backtestMeta()
      .then((m) => {
        if (!live) return;
        setMeta(m);
        setStart(m.defaults.start);
        setCash(String(m.defaults.initial_cash));
        setFees(String(m.defaults.fees_bps));
        setSlip(String(m.defaults.slippage_bps));
        const first = m.strategies.find((s) => s.id === "sma_cross") || m.strategies[0];
        if (first) {
          setStrategyId(first.id);
          setParamText(defaultParamText(first));
        }
      })
      .catch((e: Error) => {
        if (live) setErr(`Backtester unavailable: ${e.message}`);
      });
    api
      .portfolios()
      .then((r) => {
        if (live) setFunds((r.items || []).filter((f) => f.strategy?.kind && f.strategy.kind !== "manual"));
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, []);

  const spec: BtStrategySpec | null = useMemo(
    () => meta?.strategies.find((s) => s.id === strategyId) || null,
    [meta, strategyId],
  );
  const symbols = useMemo(() => parseSymbols(symbolsText), [symbolsText]);
  const currentParams = useMemo(() => (spec ? paramsFromText(spec, paramText) : {}), [spec, paramText]);
  const combos = spec ? gridSize(spec, currentParams) : 0;

  function chooseStrategy(id: string) {
    const s = meta?.strategies.find((x) => x.id === id);
    setStrategyId(id);
    setPresetId("");
    setFundId("");
    if (s) setParamText(defaultParamText(s));
  }

  function applyPreset(id: string) {
    setPresetId(id);
    setFundId("");
    const p = meta?.presets.find((x) => x.id === id);
    const s = p && meta?.strategies.find((x) => x.id === p.strategy);
    if (!p || !s) return;
    setStrategyId(s.id);
    setSymbolsText(p.symbols.join(", "));
    setParamText(paramTextFor(s, p.params));
  }

  function applyFund(id: string) {
    setFundId(id);
    setPresetId("");
    const f = funds.find((x) => x.id === id);
    const kind = f?.strategy?.kind;
    const mapping = kind ? meta?.paper_kinds[kind] : undefined;
    const s = mapping && meta?.strategies.find((x) => x.id === mapping.strategy);
    if (!f || !mapping || !s) {
      if (f && kind) setErr(`The paper strategy "${kind}" depends on live movers and cannot be replayed.`);
      return;
    }
    const sym = (f.strategy?.symbol || "SPY").toUpperCase();
    setStrategyId(s.id);
    setSymbolsText(mapping.symbols.map((x) => x.replace("{symbol}", sym)).join(", "));
    setParamText(paramTextFor(s, mapping.params));
    setErr(null);
  }

  const wfBody = wfOn ? { folds: Number(wfFolds) || 4, train_pct: Number(wfTrain) || 50, mode: wfMode } : null;
  const common = {
    symbols,
    start,
    end: end || null,
    initial_cash: Number(cash) || 100000,
    fees_bps: Number(fees) || 0,
    slippage_bps: Number(slip) || 0,
    walk_forward: wfBody,
  };

  function remember(label: string, r: BtResult) {
    setResult(r);
    setSelectedId(r.best_id);
    setTab("overview");
    setHistory((h) => [{ id: `${Date.now()}`, label, result: r }, ...h].slice(0, 8));
  }

  async function run(mode: "grid" | "all") {
    if (!meta || !spec) return;
    if (!symbols.length) {
      setErr("Enter at least one symbol");
      return;
    }
    setBusy(mode);
    setErr(null);
    const strategies =
      mode === "grid"
        ? [{ kind: spec.id, params: currentParams }]
        : meta.strategies.map((s) => {
            const params: Record<string, unknown> = {};
            for (const p of s.params) params[p.key] = p.default;
            if (s.id === "momentum_rot") {
              params.top_n = symbols.length >= 2 ? [1, 2] : [1];
              params.defensive = "";
            }
            return { kind: s.id, params };
          });
    try {
      const r = await api.runBacktest({ ...common, strategies, rank_by: rankBy });
      remember(
        mode === "grid"
          ? `${spec.label} · ${symbols.join(" ")} · ${r.combination_count} runs`
          : `All rules · ${symbols.join(" ")} · ${r.combination_count} runs`,
        r,
      );
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  async function optimize() {
    if (!meta || !spec) return;
    if (!symbols.length) {
      setErr("Enter at least one symbol");
      return;
    }
    setBusy("opt");
    setErr(null);
    const { space, fixed } = searchSpaceFromText(spec, paramText);
    try {
      const r = await api.optimizeBacktest({
        ...common,
        strategy: spec.id,
        space,
        fixed,
        objective: rankBy,
        budget: Number(budget) || 200,
      });
      remember(`Search · ${spec.label} · ${symbols.join(" ")} · ${r.search?.evaluations ?? r.combination_count} evals`, r);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy("");
    }
  }

  const selected: BtRun | null = useMemo(() => {
    if (!result) return null;
    return result.runs.find((r) => r.id === selectedId) || result.runs[0] || null;
  }, [result, selectedId]);
  const best = result?.runs[0] || null;
  const wf = result?.walk_forward || null;
  const search = result?.search || null;

  const lines: BtLine[] = useMemo(() => {
    if (!result) return [];
    const out: BtLine[] = [];
    if (selected?.equity) {
      out.push({ id: selected.id, label: `#${selected.rank} ${selected.label}`, values: selected.equity, color: COLORS[0], width: 2 });
    }
    if (best && best.equity && selected && best.id !== selected.id) {
      out.push({ id: best.id, label: `#1 ${best.label}`, values: best.equity, color: COLORS[1], width: 1 });
    }
    if (result.walk_forward) {
      out.push({
        id: "wf",
        label: "Walk-forward (out of sample)",
        values: result.walk_forward.oos_equity.map((v) => (v == null ? Number.NaN : v)),
        color: COLORS[3],
        width: 2,
      });
    }
    out.push({ id: "bench", label: "Buy & hold", values: result.benchmark.equity, color: "#8b949e", width: 1, dashed: true });
    return out;
  }, [result, selected, best]);

  const hasEquity = (id: string) => !!result?.runs.find((r) => r.id === id)?.equity;
  const pick = (id: string) => {
    if (hasEquity(id)) setSelectedId(id);
  };

  const advancedSummary = `${RANK_LABELS[rankBy] || rankBy} · ${money(Number(cash) || 0)} · ${fees}+${slip} bps · ${
    wfOn ? `walk-forward ${wfFolds} folds, ${wfMode}, ${wfTrain}% train` : "no walk-forward"
  } · search budget ${budget}`;

  return (
    <div className="mc-layout bt-layout">
      {/* ------------------------------------------------------------ setup */}
      <div className="mc-form-col bt-setup">
        <div className="section-h">
          Strategy backtester
          <span className="muted">Yahoo daily closes · signals at the prior close, fills at the next · not financial advice</span>
        </div>
        {err && <div className="err bt-err">{err}</div>}

        <div className="bt-stack">
          {/* ---- 1 · Strategy */}
          <section className="bt-card">
            <div className="bt-card-h">
              <span className="bt-step">1</span> Strategy
              <span className="muted bt-card-hint">
                {spec ? (spec.group === "per_symbol" ? "one cash sleeve per symbol" : "whole portfolio") : ""}
                {spec && spec.params.length > 0 ? ` · ${combos} combination${combos === 1 ? "" : "s"}` : ""}
              </span>
            </div>
            <div className="bt-grid4">
              <label className="pf-field bt-span2">
                <span className="bt-label">Preset</span>
                <select value={presetId} onChange={(e) => applyPreset(e.target.value)}>
                  <option value="">— pick a tested setup, or build your own —</option>
                  {meta?.presets.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="pf-field">
                <span className="bt-label">Rule</span>
                <select value={strategyId} onChange={(e) => chooseStrategy(e.target.value)}>
                  {meta?.strategies.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="pf-field">
                <span className="bt-label">Paper fund</span>
                <select value={fundId} onChange={(e) => applyFund(e.target.value)} disabled={funds.length === 0}>
                  <option value="">{funds.length ? "— load its strategy —" : "no auto funds"}</option>
                  {funds.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name} · {f.strategy?.kind}
                      {f.strategy?.symbol ? ` ${f.strategy.symbol}` : ""}
                    </option>
                  ))}
                </select>
              </label>
              {spec && <p className="muted bt-desc bt-span4">{spec.description}</p>}
              {spec?.params.map((p) => (
                <label key={p.key} className="pf-field">
                  <span className="bt-label">
                    {p.label}
                    {p.help ? <span className="bt-label-hint">{p.help}</span> : null}
                  </span>
                  <input
                    value={paramText[p.key] ?? ""}
                    onChange={(e) => setParamText((t) => ({ ...t, [p.key]: e.target.value }))}
                    placeholder={p.type === "symbol" ? "e.g. SHY" : p.range ? rangeLabel(p) : "10, 20, 50"}
                  />
                  <span className="bt-under">{p.range ? `search ${rangeLabel(p)}` : "\u00a0"}</span>
                </label>
              ))}
              {spec && spec.params.length > 0 && (
                <p className="muted bt-desc bt-span4">
                  Several comma-separated values make a grid. For the optimizer: one value pins a parameter, several values set
                  its axis, a blank box uses the search range.
                </p>
              )}
            </div>
          </section>

          {/* ---- 2 · Data */}
          <section className="bt-card">
            <div className="bt-card-h">
              <span className="bt-step">2</span> Data
              <span className="muted bt-card-hint">Yahoo daily closes, about ten years · indicators warm up before the start date</span>
            </div>
            <div className="bt-grid4">
              <label className="pf-field bt-span2">
                <span className="bt-label">
                  Symbols
                  <span className="bt-label-hint">comma separated, max {meta?.limits.max_symbols ?? 12}</span>
                </span>
                <input value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)} placeholder="SPY, QQQ, IWM" />
              </label>
              <label className="pf-field">
                <span className="bt-label">Start</span>
                <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
              </label>
              <label className="pf-field">
                <span className="bt-label">
                  End
                  <span className="bt-label-hint">blank = today</span>
                </span>
                <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
              </label>
            </div>
          </section>

          {/* ---- 3 · Costs & validation */}
          <section className="bt-card">
            <button type="button" className="bt-card-h bt-card-toggle" onClick={() => setAdvanced((v) => !v)} aria-expanded={advanced}>
              <span className="bt-step">3</span> Costs &amp; validation
              <span className="muted bt-card-hint">{advanced ? "hide" : advancedSummary}</span>
            </button>
            {advanced && (
              <div className="bt-grid4">
                <label className="pf-field">
                  <span className="bt-label">Rank by</span>
                  <select value={rankBy} onChange={(e) => setRankBy(e.target.value)}>
                    {(meta?.rank_keys || ["sharpe"]).map((k) => (
                      <option key={k} value={k}>
                        {RANK_LABELS[k] || k}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="pf-field">
                  <span className="bt-label">Initial cash</span>
                  <input inputMode="decimal" value={cash} onChange={(e) => setCash(e.target.value)} />
                </label>
                <label className="pf-field">
                  <span className="bt-label">
                    Commission <span className="bt-label-hint">bps</span>
                  </span>
                  <input inputMode="decimal" value={fees} onChange={(e) => setFees(e.target.value)} />
                </label>
                <label className="pf-field">
                  <span className="bt-label">
                    Slippage <span className="bt-label-hint">bps</span>
                  </span>
                  <input inputMode="decimal" value={slip} onChange={(e) => setSlip(e.target.value)} />
                </label>

                <label className="pf-field">
                  <span className="bt-label">Walk-forward</span>
                  <select value={wfOn ? "on" : "off"} onChange={(e) => setWfOn(e.target.value === "on")}>
                    <option value="on">On: test out of sample</option>
                    <option value="off">Off: in-sample only</option>
                  </select>
                </label>
                <label className="pf-field">
                  <span className="bt-label">Test folds</span>
                  <select value={wfFolds} onChange={(e) => setWfFolds(e.target.value)} disabled={!wfOn}>
                    {[2, 3, 4, 5, 6, 8].map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="pf-field">
                  <span className="bt-label">
                    Training <span className="bt-label-hint">% of window</span>
                  </span>
                  <select value={wfTrain} onChange={(e) => setWfTrain(e.target.value)} disabled={!wfOn}>
                    {[30, 40, 50, 60, 70].map((n) => (
                      <option key={n} value={n}>
                        {n}%
                      </option>
                    ))}
                  </select>
                </label>
                <label className="pf-field">
                  <span className="bt-label">Training window</span>
                  <select value={wfMode} onChange={(e) => setWfMode(e.target.value as "anchored" | "rolling")} disabled={!wfOn}>
                    <option value="anchored">Anchored: all prior data</option>
                    <option value="rolling">Rolling: same-length slice</option>
                  </select>
                </label>

                <label className="pf-field">
                  <span className="bt-label">
                    Search budget <span className="bt-label-hint">evaluations</span>
                  </span>
                  <select value={budget} onChange={(e) => setBudget(e.target.value)}>
                    {[100, 200, 400, 800].map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="muted bt-desc bt-span3 bt-desc-mid">
                  Walk-forward re-picks the best parameters on each training slice and runs them on the next slice. The search
                  budget only applies to Optimize parameters.
                </p>
              </div>
            )}
          </section>
        </div>

        <div className="bt-actions">
          <button type="button" className="llm-btn" disabled={!!busy || !meta} onClick={() => void run("grid")}>
            {busy === "grid" ? "Running…" : `Run${combos > 1 ? ` grid (${combos})` : ""}`}
          </button>
          <button
            type="button"
            className="llm-btn bt-secondary"
            disabled={!!busy || !meta || !spec || spec.params.length === 0}
            onClick={() => void optimize()}
            title="Random sampling plus hill-climbing over the search ranges, with a plateau-versus-spike check"
          >
            {busy === "opt" ? "Searching…" : "Optimize parameters"}
          </button>
          <button type="button" className="llm-btn bt-secondary" disabled={!!busy || !meta} onClick={() => void run("all")}>
            {busy === "all" ? "Running…" : "Compare all rules"}
          </button>
          {history.length > 0 && (
            <div className="bt-chips">
              {history.map((h) => (
                <button
                  key={h.id}
                  type="button"
                  className={result === h.result ? "on" : ""}
                  onClick={() => {
                    setResult(h.result);
                    setSelectedId(h.result.best_id);
                    setTab("overview");
                  }}
                  title={`${h.result.start} → ${h.result.end} · ${h.result.elapsed_ms} ms`}
                >
                  {h.label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ------------------------------------------------------------ results */}
      <section className="mc-results bt-results">
        {!result && (
          <div className="muted mc-empty">
            Pick a preset or a rule and press Run. You get a verdict, the equity curve against buy &amp; hold, and the
            out-of-sample walk-forward read.
          </div>
        )}
        {result && selected && (
          <>
            <div className="bt-verdict">
              <div className="bt-verdict-title">
                #{selected.rank} {selected.strategy_label} <span className="muted">·</span> {selected.label}
              </div>
              <div className="muted bt-verdict-sub">
                {result.symbols.join(", ")} · {result.start} → {result.end} · {result.combination_count} run
                {result.combination_count === 1 ? "" : "s"} ranked by {RANK_LABELS[result.rank_by] || result.rank_by} · {result.elapsed_ms} ms
              </div>
              <div className="bt-kpis">
                <div className="bt-kpi">
                  <div className="k">In-sample CAGR</div>
                  <div className={`v ${tone(selected.cagr)}`}>{pct(selected.cagr, 1)}</div>
                  <div className="sub">
                    buy &amp; hold {pct(result.benchmark.cagr, 1)}{" "}
                    <span className={tone(selected.excess_cagr)}>({signed(selected.excess_cagr)})</span>
                  </div>
                </div>
                <div className="bt-kpi">
                  <div className="k">Out-of-sample CAGR</div>
                  <div className={`v ${wf ? tone(wf.oos.cagr) : "muted"}`}>{wf ? pct(wf.oos.cagr, 1) : "off"}</div>
                  <div className="sub">
                    {wf ? (
                      <>
                        buy &amp; hold {pct(wf.oos_benchmark.cagr, 1)}{" "}
                        <span className={tone((wf.oos.cagr ?? 0) - (wf.oos_benchmark.cagr ?? 0))}>
                          ({signed((wf.oos.cagr ?? 0) - (wf.oos_benchmark.cagr ?? 0))})
                        </span>
                      </>
                    ) : (
                      "enable walk-forward under Costs & validation"
                    )}
                  </div>
                </div>
                <div className="bt-kpi">
                  <div className="k">Max drawdown</div>
                  <div className="v down">{pct(selected.max_drawdown, 1)}</div>
                  <div className="sub">buy &amp; hold {pct(result.benchmark.max_drawdown, 1)}</div>
                </div>
                <div className="bt-kpi">
                  <div className="k">Sharpe</div>
                  <div className="v">{num(selected.sharpe)}</div>
                  <div className="sub">
                    buy &amp; hold {num(result.benchmark.sharpe)}
                    {wf ? ` · OOS ${num(wf.oos.sharpe)}` : ""}
                  </div>
                </div>
                <div className="bt-kpi">
                  <div className="k">Trades · win rate</div>
                  <div className="v">
                    {selected.trades} <span className="muted">·</span> {selected.win_rate == null ? "—" : pct(selected.win_rate, 0)}
                  </div>
                  <div className="sub">
                    invested {pct(selected.exposure, 0)} of the time
                    {selected.profit_factor != null ? ` · PF ${num(selected.profit_factor, 1)}` : ""}
                  </div>
                </div>
                {wf && (
                  <div className="bt-kpi">
                    <div className="k">Walk-forward efficiency</div>
                    <div className={`v ${wf.efficiency == null ? "" : wf.efficiency >= 0.5 ? "up" : "down"}`}>
                      {wf.efficiency == null ? "—" : num(wf.efficiency)}
                    </div>
                    <div className="sub">
                      beat buy &amp; hold in {wf.folds_beating_benchmark} of {wf.segments.length} folds
                    </div>
                  </div>
                )}
              </div>
              <ul className="bt-verdict-text">
                {verdict(result, selected).map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            </div>

            <div className="bt-legend-row">
              <div className="bt-legend">
                {lines.map((l) => (
                  <span key={l.id} style={{ color: l.color }}>
                    {l.dashed ? "┄ " : "— "}
                    {l.label}
                  </span>
                ))}
              </div>
              <label className="bt-log">
                <input type="checkbox" checked={logScale} onChange={(e) => setLogScale(e.target.checked)} /> log scale
              </label>
            </div>
            <BacktestChart dates={result.dates} lines={lines} logScale={logScale} />

            <nav className="bt-tabs" aria-label="Result sections">
              {(
                [
                  ["overview", "Overview"],
                  ["ranking", `Ranking (${result.combination_count})`],
                  wf ? ["walkforward", `Walk-forward (${wf.segments.length} folds)`] : null,
                  search ? ["search", `Search (${search.evaluations})`] : null,
                  selected.recent_trades?.length ? ["trades", "Last trades"] : null,
                  ["notes", `Notes (${result.warnings.length})`],
                ] as ([ResultTab, string] | null)[]
              )
                .filter((t): t is [ResultTab, string] => t != null)
                .map(([id, label]) => (
                  <button key={id} type="button" className={tab === id ? "on" : ""} onClick={() => setTab(id)}>
                    {label}
                  </button>
                ))}
            </nav>

            {tab === "overview" && (
              <div className="bt-tabpane">
                <div className="mc-stats bt-stats">
                  <div>
                    <div className="muted">Total return</div>
                    <div className={tone(selected.total_return)}>{pct(selected.total_return)}</div>
                  </div>
                  <div>
                    <div className="muted">Final value</div>
                    <div>{money(selected.final_value)}</div>
                  </div>
                  <div>
                    <div className="muted">Volatility</div>
                    <div>{pct(selected.volatility)}</div>
                  </div>
                  <div>
                    <div className="muted">Sortino · Calmar</div>
                    <div>
                      {num(selected.sortino)} · {num(selected.calmar)}
                    </div>
                  </div>
                  <div>
                    <div className="muted">Avg trade · best · worst</div>
                    <div>
                      {pct(selected.avg_trade_return)} · <span className="up">{pct(selected.best_trade, 1)}</span> ·{" "}
                      <span className="down">{pct(selected.worst_trade, 1)}</span>
                    </div>
                  </div>
                  <div>
                    <div className="muted">Costs</div>
                    <div>
                      {result.fees_bps} bps commission · {result.slippage_bps} bps slippage
                    </div>
                  </div>
                  <div>
                    <div className="muted">Data</div>
                    <div>
                      {Object.entries(result.sources)
                        .map(([s, src]) => `${s} ${src}`)
                        .join(", ")}{" "}
                      · from {result.data_start}
                    </div>
                  </div>
                  <div>
                    <div className="muted">Bars</div>
                    <div>
                      {result.bars} · {selected.years} years
                    </div>
                  </div>
                </div>
              </div>
            )}

            {tab === "ranking" && (
              <div className="bt-tabpane">
                <p className="muted bt-desc">In-sample. Click a row to chart it; the top {result.equity_runs} keep an equity curve.</p>
                <div className="bt-table-wrap">
                  <table className="pf-table bt-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Rule</th>
                        <th>Parameters</th>
                        <th>CAGR</th>
                        <th>Total</th>
                        <th>Sharpe</th>
                        <th>Sortino</th>
                        <th>Max DD</th>
                        <th>Calmar</th>
                        <th>Trades</th>
                        <th>Win</th>
                        <th>PF</th>
                        <th>Invested</th>
                      </tr>
                    </thead>
                    <tbody>
                      {result.runs.map((r) => (
                        <tr
                          key={r.id}
                          className={`bt-row${r.id === selected.id ? " on" : ""}${r.equity ? "" : " bt-row-noeq"}`}
                          onClick={() => pick(r.id)}
                        >
                          <td>{r.rank}</td>
                          <td>{r.strategy_label}</td>
                          <td className="bt-params-cell">{r.label}</td>
                          <td className={tone(r.cagr)}>{pct(r.cagr)}</td>
                          <td className={tone(r.total_return)}>{pct(r.total_return, 1)}</td>
                          <td>{num(r.sharpe)}</td>
                          <td>{num(r.sortino)}</td>
                          <td className="down">{pct(r.max_drawdown, 1)}</td>
                          <td>{num(r.calmar)}</td>
                          <td>{r.trades}</td>
                          <td>{r.win_rate == null ? "—" : pct(r.win_rate, 0)}</td>
                          <td>{r.profit_factor == null ? "—" : num(r.profit_factor, 1)}</td>
                          <td>{pct(r.exposure, 0)}</td>
                        </tr>
                      ))}
                      <tr className="bt-row bt-bench">
                        <td>—</td>
                        <td>Benchmark</td>
                        <td className="bt-params-cell">{result.benchmark.label}</td>
                        <td className={tone(result.benchmark.cagr)}>{pct(result.benchmark.cagr)}</td>
                        <td className={tone(result.benchmark.total_return)}>{pct(result.benchmark.total_return, 1)}</td>
                        <td>{num(result.benchmark.sharpe)}</td>
                        <td>{num(result.benchmark.sortino)}</td>
                        <td className="down">{pct(result.benchmark.max_drawdown, 1)}</td>
                        <td>{num(result.benchmark.calmar)}</td>
                        <td>0</td>
                        <td>—</td>
                        <td>—</td>
                        <td>100%</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {tab === "walkforward" && wf && (
              <div className="bt-tabpane">
                <p className="muted bt-desc">
                  {wf.mode} window · {wf.folds} folds · first {wf.train_pct}% ({wf.train_bars} bars) trains only · out of sample{" "}
                  {wf.oos_start} → {wf.oos_end} · selected by {RANK_LABELS[wf.rank_by] || wf.rank_by}. Each fold's parameters were
                  chosen on data before it, so the orange curve never sees its own test period.
                </p>
                <div className="mc-stats bt-stats">
                  <div>
                    <div className="muted">OOS CAGR · buy &amp; hold</div>
                    <div>
                      <span className={tone(wf.oos.cagr)}>{pct(wf.oos.cagr)}</span> · {pct(wf.oos_benchmark.cagr)}
                    </div>
                  </div>
                  <div>
                    <div className="muted">OOS Sharpe · buy &amp; hold</div>
                    <div>
                      {num(wf.oos.sharpe)} · {num(wf.oos_benchmark.sharpe)}
                    </div>
                  </div>
                  <div>
                    <div className="muted">OOS max drawdown · buy &amp; hold</div>
                    <div>
                      <span className="down">{pct(wf.oos.max_drawdown)}</span> · {pct(wf.oos_benchmark.max_drawdown)}
                    </div>
                  </div>
                  <div>
                    <div className="muted">Efficiency</div>
                    <div className={wf.efficiency == null ? "" : wf.efficiency >= 0.5 ? "up" : "down"}>
                      {wf.efficiency == null ? "—" : num(wf.efficiency)}
                      <span className="muted bt-sub">OOS CAGR ÷ avg in-sample CAGR {pct(wf.avg_is_cagr)}</span>
                    </div>
                  </div>
                  <div>
                    <div className="muted">Distinct selections</div>
                    <div>
                      {wf.distinct_selections} of {wf.segments.length} folds
                    </div>
                  </div>
                  {wf.is_best_full && (
                    <div>
                      <div className="muted">In-sample #1 on the same span</div>
                      <div>
                        <span className={tone(wf.is_best_full.cagr)}>{pct(wf.is_best_full.cagr)}</span> CAGR ·{" "}
                        {num(wf.is_best_full.sharpe)} Sharpe
                      </div>
                    </div>
                  )}
                </div>
                <div className="bt-table-wrap">
                  <table className="pf-table bt-table">
                    <thead>
                      <tr>
                        <th>Fold</th>
                        <th>Train</th>
                        <th>Test</th>
                        <th>Selected</th>
                        <th>IS rank</th>
                        <th>IS Sharpe</th>
                        <th>OOS return</th>
                        <th>OOS Sharpe</th>
                        <th>OOS DD</th>
                        <th>B&amp;H return</th>
                        <th>Trades</th>
                      </tr>
                    </thead>
                    <tbody>
                      {wf.segments.map((sg) => (
                        <tr
                          key={sg.fold}
                          className={`bt-row${hasEquity(sg.chosen_id) ? "" : " bt-row-noeq"}`}
                          onClick={() => pick(sg.chosen_id)}
                          title="Chart the selected combination's full in-sample path"
                        >
                          <td>{sg.fold}</td>
                          <td>
                            {sg.train_start} → {sg.train_end}
                          </td>
                          <td>
                            {sg.test_start} → {sg.test_end}
                          </td>
                          <td className="bt-params-cell">
                            {sg.chosen_strategy_label} · {sg.chosen_label}
                          </td>
                          <td>{sg.chosen_full_rank == null ? "—" : `#${sg.chosen_full_rank}`}</td>
                          <td>{num(sg.is.sharpe)}</td>
                          <td className={sg.beat_benchmark ? "up" : "down"}>{pct(sg.oos.total_return, 1)}</td>
                          <td>{num(sg.oos.sharpe)}</td>
                          <td className="down">{pct(sg.oos.max_drawdown, 1)}</td>
                          <td className={tone(sg.oos_benchmark.total_return)}>{pct(sg.oos_benchmark.total_return, 1)}</td>
                          <td>{sg.oos.trades ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <p className="muted bt-desc">
                  Test segments come from each combination's continuous path: positions carry across a boundary and switching
                  parameter sets at a boundary is assumed cost-free.
                </p>
              </div>
            )}

            {tab === "search" && search && (
              <div className="bt-tabpane">
                <p className="muted bt-desc">
                  {search.exhaustive ? "Exhaustive" : "Random sample + hill-climb"} · {search.evaluations} of{" "}
                  {search.grid_size.toLocaleString()} combinations · objective {RANK_LABELS[search.objective] || search.objective}
                  {Object.entries(search.fixed).filter(([, v]) => v !== "" && v != null).length
                    ? ` · pinned ${Object.entries(search.fixed)
                        .filter(([, v]) => v !== "" && v != null)
                        .map(([k, v]) => `${k}=${String(v)}`)
                        .join(" ")}`
                    : ""}
                  . A winner whose neighbours score about as well sits on a plateau; one whose neighbours collapse is a spike, the
                  usual signature of over-fitting.
                </p>
                <div className="bt-table-wrap">
                  <table className="pf-table bt-table">
                    <thead>
                      <tr>
                        <th>#</th>
                        <th>Parameters</th>
                        <th>{RANK_LABELS[search.objective] || search.objective}</th>
                        <th>Neighbours</th>
                        <th>Verdict</th>
                        <th>CAGR</th>
                        <th>Max DD</th>
                        <th>Trades</th>
                        <th>Win</th>
                      </tr>
                    </thead>
                    <tbody>
                      {search.top.map((t, i) => (
                        <tr
                          key={t.id}
                          className={`bt-row${t.id === selected.id ? " on" : ""}${hasEquity(t.id) ? "" : " bt-row-noeq"}`}
                          onClick={() => pick(t.id)}
                        >
                          <td>{i + 1}</td>
                          <td className="bt-params-cell">{t.label}</td>
                          <td>{num(t.objective)}</td>
                          <td>
                            {t.neighbours_mean == null ? "—" : num(t.neighbours_mean)}{" "}
                            <span className="muted">({t.neighbours})</span>
                          </td>
                          <td className={t.stable ? "up" : "down"}>{t.stable ? "plateau" : "spike"}</td>
                          <td className={tone(t.cagr)}>{pct(t.cagr)}</td>
                          <td className="down">{pct(t.max_drawdown, 1)}</td>
                          <td>{t.trades ?? "—"}</td>
                          <td>{t.win_rate == null ? "—" : pct(t.win_rate, 0)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {search.sensitivity.length > 0 && (
                  <>
                    <div className="bt-sub-h">
                      Sensitivity around the winner
                      <span className="muted">one parameter moves, the others stay at their best values · taller is better</span>
                    </div>
                    <div className="bt-sens">
                      {search.sensitivity.map((ax) => {
                        const vals = ax.points.map((p) => p.objective).filter((v): v is number => v != null);
                        const lo = vals.length ? Math.min(...vals) : 0;
                        const hi = vals.length ? Math.max(...vals) : 1;
                        return (
                          <div key={ax.key} className="bt-sens-axis">
                            <div className="bt-sens-h">
                              {ax.label} <span className="muted">best {ax.best}</span>
                            </div>
                            <div className="bt-sens-bars">
                              {ax.points.map((p) => {
                                const h = p.objective == null ? 0 : hi > lo ? ((p.objective - lo) / (hi - lo)) * 100 : 60;
                                const isBest = p.value === ax.best;
                                const clickable = !!p.id && hasEquity(p.id);
                                return (
                                  <div
                                    key={String(p.value)}
                                    className={`bt-sens-col${isBest ? " on" : ""}${clickable ? " clickable" : ""}`}
                                    title={`${ax.label} ${p.value}: ${p.objective == null ? "invalid" : num(p.objective)}${p.cagr != null ? ` · CAGR ${pct(p.cagr)}` : ""}${p.max_drawdown != null ? ` · DD ${pct(p.max_drawdown, 1)}` : ""}`}
                                    onClick={() => clickable && p.id && setSelectedId(p.id)}
                                  >
                                    <div className="bt-sens-bar" style={{ height: `${Math.max(4, h)}%` }} />
                                    <div className="bt-sens-v">{p.objective == null ? "×" : num(p.objective, 2)}</div>
                                    <div className="bt-sens-x">{p.value}</div>
                                  </div>
                                );
                              })}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </>
                )}
              </div>
            )}

            {tab === "trades" && selected.recent_trades && (
              <div className="bt-tabpane">
                <p className="muted bt-desc">
                  Most recent {selected.recent_trades.length} closed trades of #{selected.rank}. Click a symbol to open it in Research.
                </p>
                <div className="bt-table-wrap">
                  <table className="pf-table bt-table">
                    <thead>
                      <tr>
                        <th>Symbol</th>
                        <th>Entry</th>
                        <th>Exit</th>
                        <th>Return</th>
                        <th>Reason</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selected.recent_trades.map((t, i) => (
                        <tr key={i}>
                          <td>
                            {t.symbol && onOpenSymbol ? (
                              <button type="button" className="bt-link" onClick={() => onOpenSymbol(t.symbol!)}>
                                {t.symbol}
                              </button>
                            ) : (
                              t.symbol || "—"
                            )}
                          </td>
                          <td>{t.entry || "—"}</td>
                          <td>{t.exit || "—"}</td>
                          <td className={tone(t.return)}>{pct(t.return)}</td>
                          <td>{t.reason || "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {tab === "notes" && (
              <div className="bt-tabpane">
                <div className="bt-sub-h">Warnings</div>
                <ul className="bt-warnings muted">
                  {result.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
                <div className="bt-sub-h">Assumptions</div>
                <ul className="bt-warnings muted">
                  {Object.entries(result.assumptions).map(([k, v]) => (
                    <li key={k}>
                      <b>{k.replace(/_/g, " ")}</b>: {v}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
