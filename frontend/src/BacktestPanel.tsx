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

type HistoryItem = { id: string; label: string; result: BtResult };

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
  const [busy, setBusy] = useState<"" | "grid" | "all">("");
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<BtResult | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [logScale, setLogScale] = useState(false);
  const [showAssumptions, setShowAssumptions] = useState(false);
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
      const r = await api.runBacktest({
        symbols,
        start,
        end: end || null,
        strategies,
        initial_cash: Number(cash) || 100000,
        fees_bps: Number(fees) || 0,
        slippage_bps: Number(slip) || 0,
        rank_by: rankBy,
        walk_forward: wfOn ? { folds: Number(wfFolds) || 4, train_pct: Number(wfTrain) || 50, mode: wfMode } : null,
      });
      setResult(r);
      setSelectedId(r.best_id);
      const label =
        mode === "grid"
          ? `${spec.label} · ${symbols.join(" ")} · ${r.combination_count} runs`
          : `All strategies · ${symbols.join(" ")} · ${r.combination_count} runs`;
      setHistory((h) => [{ id: `${Date.now()}`, label, result: r }, ...h].slice(0, 8));
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
        label: `Walk-forward OOS (${result.walk_forward.folds} folds)`,
        values: result.walk_forward.oos_equity.map((v) => (v == null ? Number.NaN : v)),
        color: COLORS[3],
        width: 2,
      });
    }
    out.push({ id: "bench", label: "Buy & hold", values: result.benchmark.equity, color: "#8b949e", width: 1, dashed: true });
    return out;
  }, [result, selected, best]);
  const wf = result?.walk_forward || null;

  return (
    <div className="mc-layout bt-layout">
      <div className="mc-form-col">
        <div className="section-h">Strategy backtester</div>
        <p className="mc-lead muted">
          Replay a strategy over Yahoo daily closes with a parameter grid, or compare every strategy at its defaults on the
          same symbols. Signals at the prior close, fills at the next close, costs in basis points. Indicators warm up on
          history before the start date. In-sample research on one price path. Not financial advice.
        </p>
        {err && <div className="err bt-err">{err}</div>}
        <div className="mc-grid bt-grid">
          <label className="pf-field">
            Preset
            <select value={presetId} onChange={(e) => applyPreset(e.target.value)}>
              <option value="">— choose —</option>
              {meta?.presets.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label className="pf-field">
            Paper fund strategy
            <select value={fundId} onChange={(e) => applyFund(e.target.value)}>
              <option value="">— load from a fund —</option>
              {funds.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name} · {f.strategy?.kind}
                  {f.strategy?.symbol ? ` ${f.strategy.symbol}` : ""}
                </option>
              ))}
            </select>
          </label>
          <label className="pf-field">
            Strategy
            <select value={strategyId} onChange={(e) => chooseStrategy(e.target.value)}>
              {meta?.strategies.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.label}
                </option>
              ))}
            </select>
          </label>
          <label className="pf-field">
            Rank by
            <select value={rankBy} onChange={(e) => setRankBy(e.target.value)}>
              {(meta?.rank_keys || ["sharpe"]).map((k) => (
                <option key={k} value={k}>
                  {RANK_LABELS[k] || k}
                </option>
              ))}
            </select>
          </label>
          <label className="pf-field bt-symbols">
            Symbols (comma separated, max {meta?.limits.max_symbols ?? 12})
            <input value={symbolsText} onChange={(e) => setSymbolsText(e.target.value)} placeholder="SPY, QQQ, IWM" />
          </label>
          <label className="pf-field">
            Start
            <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="pf-field">
            End (blank = today)
            <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <label className="pf-field">
            Initial cash
            <input inputMode="decimal" value={cash} onChange={(e) => setCash(e.target.value)} />
          </label>
          <label className="pf-field">
            Commission (bps)
            <input inputMode="decimal" value={fees} onChange={(e) => setFees(e.target.value)} />
          </label>
          <label className="pf-field">
            Slippage (bps)
            <input inputMode="decimal" value={slip} onChange={(e) => setSlip(e.target.value)} />
          </label>
        </div>
        <div className="section-h">
          Walk-forward validation
          <span className="muted">re-select the best combination on each training slice, then run it forward</span>
        </div>
        <div className="mc-grid bt-grid">
          <label className="pf-field bt-check">
            Enabled
            <span className="bt-check-row">
              <input type="checkbox" checked={wfOn} onChange={(e) => setWfOn(e.target.checked)} />
              <span className="muted">out-of-sample folds on top of the in-sample grid</span>
            </span>
          </label>
          <label className="pf-field">
            Test folds
            <select value={wfFolds} onChange={(e) => setWfFolds(e.target.value)} disabled={!wfOn}>
              {[2, 3, 4, 5, 6, 8].map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label className="pf-field">
            Initial training %
            <select value={wfTrain} onChange={(e) => setWfTrain(e.target.value)} disabled={!wfOn}>
              {[30, 40, 50, 60, 70].map((n) => (
                <option key={n} value={n}>
                  {n}%
                </option>
              ))}
            </select>
          </label>
          <label className="pf-field">
            Training window
            <select value={wfMode} onChange={(e) => setWfMode(e.target.value as "anchored" | "rolling")} disabled={!wfOn}>
              <option value="anchored">Anchored (everything before the fold)</option>
              <option value="rolling">Rolling (same-length slice before the fold)</option>
            </select>
          </label>
        </div>
        {spec && (
          <>
            <div className="section-h">
              {spec.label}
              <span className="muted">
                {spec.group === "per_symbol" ? "one cash sleeve per symbol" : "whole portfolio"} · {combos} combination
                {combos === 1 ? "" : "s"}
              </span>
            </div>
            <p className="mc-lead muted">{spec.description}</p>
            {spec.params.length > 0 && (
              <div className="mc-grid bt-grid">
                {spec.params.map((p) => (
                  <label key={p.key} className="pf-field">
                    {p.label}
                    {p.help ? <span className="muted bt-help"> · {p.help}</span> : null}
                    <input
                      value={paramText[p.key] ?? ""}
                      onChange={(e) => setParamText((t) => ({ ...t, [p.key]: e.target.value }))}
                      placeholder={p.type === "symbol" ? "e.g. SHY" : "10, 20, 50"}
                    />
                  </label>
                ))}
              </div>
            )}
          </>
        )}
        <div className="bt-actions">
          <button type="button" className="llm-btn" disabled={!!busy || !meta} onClick={() => void run("grid")}>
            {busy === "grid" ? "Running…" : `Run grid (${combos})`}
          </button>
          <button type="button" className="llm-btn bt-secondary" disabled={!!busy || !meta} onClick={() => void run("all")}>
            {busy === "all" ? "Running…" : "Compare all strategies"}
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

      <section className="mc-results bt-results">
        {!result && (
          <div className="muted mc-empty">
            Run a grid to rank parameter combinations, or compare every strategy on the same symbols. The chart shows the
            selected run against equal-weight buy &amp; hold.
          </div>
        )}
        {result && selected && (
          <>
            <div className="section-h">
              #{selected.rank} {selected.strategy_label} · {selected.label}
              <span className="muted">
                {result.symbols.join(", ")} · {result.start} → {result.end} · {result.bars} bars · ranked by{" "}
                {RANK_LABELS[result.rank_by] || result.rank_by} · {result.elapsed_ms} ms
              </span>
            </div>
            <div className="mc-stats bt-stats">
              <div>
                <div className="muted">CAGR</div>
                <div className={tone(selected.cagr)}>{pct(selected.cagr)}</div>
              </div>
              <div>
                <div className="muted">Total return</div>
                <div className={tone(selected.total_return)}>{pct(selected.total_return)}</div>
              </div>
              <div>
                <div className="muted">vs buy &amp; hold CAGR</div>
                <div className={tone(selected.excess_cagr)}>
                  {selected.excess_cagr > 0 ? "+" : ""}
                  {pct(selected.excess_cagr)}
                </div>
              </div>
              <div>
                <div className="muted">Sharpe / Sortino</div>
                <div>
                  {num(selected.sharpe)} / {num(selected.sortino)}
                </div>
              </div>
              <div>
                <div className="muted">Max drawdown</div>
                <div className="down">{pct(selected.max_drawdown)}</div>
              </div>
              <div>
                <div className="muted">Calmar</div>
                <div>{num(selected.calmar)}</div>
              </div>
              <div>
                <div className="muted">Volatility</div>
                <div>{pct(selected.volatility)}</div>
              </div>
              <div>
                <div className="muted">Trades · win rate</div>
                <div>
                  {selected.trades} · {selected.win_rate == null ? "—" : pct(selected.win_rate, 1)}
                </div>
              </div>
              <div>
                <div className="muted">Profit factor · avg trade</div>
                <div>
                  {selected.profit_factor == null ? "—" : num(selected.profit_factor)} ·{" "}
                  {selected.avg_trade_return == null ? "—" : pct(selected.avg_trade_return)}
                </div>
              </div>
              <div>
                <div className="muted">Time invested</div>
                <div>{pct(selected.exposure, 1)}</div>
              </div>
              <div>
                <div className="muted">Final value</div>
                <div>{money(selected.final_value)}</div>
              </div>
              <div>
                <div className="muted">Buy &amp; hold</div>
                <div>
                  {pct(result.benchmark.cagr)} CAGR · {pct(result.benchmark.max_drawdown)} DD
                </div>
              </div>
            </div>
            <div className="mc-legend bt-legend">
              {lines.map((l) => (
                <span key={l.id} style={{ color: l.color }}>
                  {l.dashed ? "┄ " : "— "}
                  {l.label}
                </span>
              ))}
              <label className="bt-log">
                <input type="checkbox" checked={logScale} onChange={(e) => setLogScale(e.target.checked)} /> log scale
              </label>
            </div>
            <BacktestChart dates={result.dates} lines={lines} logScale={logScale} />
            {result.warnings.length > 0 && (
              <ul className="bt-warnings muted">
                {result.warnings.map((w) => (
                  <li key={w}>{w}</li>
                ))}
              </ul>
            )}

            {wf && (
              <>
                <div className="section-h">
                  Walk-forward · out of sample
                  <span className="muted">
                    {wf.mode} · {wf.folds} folds · train {wf.train_pct}% ({wf.train_bars} bars) · OOS {wf.oos_start} → {wf.oos_end} ·
                    selected by {RANK_LABELS[wf.rank_by] || wf.rank_by}
                  </span>
                </div>
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
                    <div className="muted">Walk-forward efficiency</div>
                    <div className={wf.efficiency == null ? "" : wf.efficiency >= 0.5 ? "up" : "down"}>
                      {wf.efficiency == null ? "—" : num(wf.efficiency)}
                      <span className="muted bt-sub"> OOS CAGR ÷ avg in-sample CAGR {pct(wf.avg_is_cagr)}</span>
                    </div>
                  </div>
                  <div>
                    <div className="muted">Folds beating buy &amp; hold</div>
                    <div>
                      {wf.folds_beating_benchmark} / {wf.segments.length}
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
                      <div className="muted">In-sample #1 on the same OOS span</div>
                      <div>
                        <span className={tone(wf.is_best_full.cagr)}>{pct(wf.is_best_full.cagr)}</span> CAGR ·{" "}
                        {num(wf.is_best_full.sharpe)} Sharpe
                        <span className="muted bt-sub"> {wf.is_best_full.label}</span>
                      </div>
                    </div>
                  )}
                  <div>
                    <div className="muted">OOS total return · final</div>
                    <div>
                      <span className={tone(wf.oos.total_return)}>{pct(wf.oos.total_return)}</span> ·{" "}
                      {money(wf.oos.final_value)}
                    </div>
                  </div>
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
                          className={`bt-row${result.runs.find((r) => r.id === sg.chosen_id)?.equity ? "" : " bt-row-noeq"}`}
                          onClick={() => result.runs.find((r) => r.id === sg.chosen_id)?.equity && setSelectedId(sg.chosen_id)}
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
                <p className="muted bt-foot">
                  Each fold's combination is the best on data before it, so the orange curve never sees its own test
                  period. Test segments come from each combination's continuous path: positions carry across a boundary
                  and switching parameter sets at a boundary is assumed cost-free. Efficiency below 0.5 means most of
                  the in-sample edge did not survive.
                </p>
              </>
            )}

            <div className="section-h">
              Ranking
              <span className="muted">
                {result.combination_count} runs · in-sample · click a row to chart it (top {result.equity_runs} keep an
                equity curve)
              </span>
            </div>
            <div className="bt-table-wrap">
              <table className="pf-table bt-table">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Strategy</th>
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
                      onClick={() => r.equity && setSelectedId(r.id)}
                      title={r.equity ? "Chart this run" : "Outside the top runs; no equity curve kept"}
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

            {selected.recent_trades && selected.recent_trades.length > 0 && (
              <>
                <div className="section-h">
                  Last trades · #{selected.rank}
                  <span className="muted">most recent {selected.recent_trades.length} closed trades</span>
                </div>
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
              </>
            )}

            <p className="muted bt-foot">
              {money(result.initial_cash)} start · {result.fees_bps} bps commission · {result.slippage_bps} bps slippage ·
              data {Object.entries(result.sources).map(([s, src]) => `${s} ${src}`).join(", ")} · history from{" "}
              {result.data_start} ·{" "}
              <button type="button" className="bt-link" onClick={() => setShowAssumptions((v) => !v)}>
                {showAssumptions ? "hide assumptions" : "show assumptions"}
              </button>
            </p>
            {showAssumptions && (
              <ul className="bt-warnings muted">
                {Object.entries(result.assumptions).map(([k, v]) => (
                  <li key={k}>
                    <b>{k}</b>: {v}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </section>
    </div>
  );
}
