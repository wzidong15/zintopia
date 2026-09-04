import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import MonteCarloChart from "./MonteCarloChart";
import {
  EMPTY_ROW,
  equalWeight,
  normalizeWeights,
  rowsFromHoldings,
  rowsFromLazy,
  type McAssetRow,
  type McMeta,
  type McResult,
} from "./monteCarlo";
import type { PortfolioSummary } from "./portfolio";

const YEARS = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75];
const AGES = Array.from({ length: 66 }, (_, i) => 30 + i);
const START_YEARS = Array.from({ length: 2026 - 1972 + 1 }, (_, i) => 1972 + i);

function money(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}
function pct(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(2)}%`;
}

function numOrUndef(s: string): number | undefined {
  const n = Number(s);
  return s.trim() && Number.isFinite(n) ? n : undefined;
}

export default function MonteCarloPanel() {
  const [meta, setMeta] = useState<McMeta | null>(null);
  const [funds, setFunds] = useState<PortfolioSummary[]>([]);
  const [allocMode, setAllocMode] = useState<"classes" | "tickers" | "import">("classes");
  const [lazyId, setLazyId] = useState("60_40");
  const [importId, setImportId] = useState("");
  const [rows, setRows] = useState<McAssetRow[]>(() => Array.from({ length: 10 }, () => EMPTY_ROW()));
  const [initial, setInitial] = useState("100000");
  const [cashflows, setCashflows] = useState("none");
  const [cfAmount, setCfAmount] = useState("0");
  const [inflationAdj, setInflationAdj] = useState(true);
  const [wdPct, setWdPct] = useState("4");
  const [rolling, setRolling] = useState("3");
  const [smoothing, setSmoothing] = useState("80");
  const [freq, setFreq] = useState("annually");
  const [lifeModel, setLifeModel] = useState("single");
  const [age, setAge] = useState("65");
  const [years, setYears] = useState("30");
  const [tax, setTax] = useState("pretax");
  const [horizon, setHorizon] = useState("simulated");
  const [fed, setFed] = useState("22");
  const [cg, setCg] = useState("15");
  const [div, setDiv] = useState("15");
  const [aca, setAca] = useState("0");
  const [stateTax, setStateTax] = useState("0");
  const [model, setModel] = useState("historical");
  const [tsModel, setTsModel] = useState("normal");
  const [rf, setRf] = useState("4.5");
  const [histVol, setHistVol] = useState(true);
  const [histCorr, setHistCorr] = useState(true);
  const [fullHist, setFullHist] = useState(true);
  const [startYear, setStartYear] = useState("1995");
  const [endYear, setEndYear] = useState("2025");
  const [bootstrap, setBootstrap] = useState("year");
  const [blockMin, setBlockMin] = useState("2");
  const [blockMax, setBlockMax] = useState("5");
  const [circular, setCircular] = useState(false);
  const [dist, setDist] = useState("normal");
  const [dof, setDof] = useState("10");
  const [expRet, setExpRet] = useState("");
  const [vol, setVol] = useState("");
  const [seqRisk, setSeqRisk] = useState("0");
  const [infModel, setInfModel] = useState("parameterized");
  const [infMean, setInfMean] = useState("2.5");
  const [infVol, setInfVol] = useState("1.5");
  const [rebalance, setRebalance] = useState("annually");
  const [nSims, setNSims] = useState("1000");
  const [pctCustom, setPctCustom] = useState("10,25,50,75,90");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [result, setResult] = useState<McResult | null>(null);

  useEffect(() => {
    api.monteCarloMeta().then(setMeta).catch(() => undefined);
    api.portfolios({ live: true }).then((r) => setFunds(r.items || [])).catch(() => undefined);
  }, []);

  useEffect(() => {
    const lazy = meta?.lazy_portfolios.find((p) => p.id === lazyId);
    if (lazy && allocMode === "classes") setRows(rowsFromLazy(lazy));
  }, [meta, lazyId, allocMode]);

  const filled = rows.filter((r) => (allocMode === "tickers" || allocMode === "import" ? r.symbol : r.asset_id) && Number(r.weight) > 0).length;
  const weightSum = rows.reduce((s, r) => s + (Number(r.weight) || 0), 0);

  const applyImport = async (id: string) => {
    setImportId(id);
    if (!id) return;
    try {
      const p = await api.portfolio(id, { live: true });
      setAllocMode("import");
      setRows(rowsFromHoldings(p.holdings || [], p.cash || 0));
      if (p.nav) setInitial(String(Math.round(p.nav)));
    } catch (e) {
      setErr(String((e as Error).message || e));
    }
  };

  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      const percentiles = pctCustom
        .split(/[,\s]+/)
        .map((x) => Number(x))
        .filter((n) => Number.isFinite(n) && n > 0 && n < 100);
      const body = {
        portfolio_type: allocMode === "classes" ? "asset_classes" : "tickers",
        initial_amount: Number(initial) || 100000,
        cashflows,
        cashflow_amount: Number(cfAmount) || 0,
        inflation_adjusted: inflationAdj,
        withdrawal_pct: Number(wdPct) || 4,
        rolling_periods: Number(rolling) || 3,
        smoothing_rate: Number(smoothing) || 80,
        withdrawal_frequency: freq,
        life_expectancy_model: lifeModel,
        current_age: Number(age) || 65,
        years: Number(years) || 30,
        tax_treatment: tax,
        investment_horizon: horizon,
        federal_income_tax: Number(fed) || 0,
        cap_gains_tax: Number(cg) || 0,
        dividend_tax: Number(div) || 0,
        aca_tax: Number(aca) || 0,
        state_income_tax: Number(stateTax) || 0,
        simulation_model: model,
        time_series: tsModel,
        risk_free_rate: Number(rf) || 0,
        use_historical_vol: histVol,
        use_historical_corr: histCorr,
        use_full_history: fullHist,
        start_year: Number(startYear) || 1972,
        end_year: Number(endYear) || 2026,
        bootstrap,
        block_min_years: Number(blockMin) || 2,
        block_max_years: Number(blockMax) || 5,
        circular,
        distribution: dist,
        degrees_of_freedom: Number(dof) || 10,
        expected_return: numOrUndef(expRet) ?? null,
        volatility: numOrUndef(vol) ?? null,
        sequence_risk: Number(seqRisk) || 0,
        inflation_model: infModel,
        inflation_mean: Number(infMean) || 2.5,
        inflation_vol: Number(infVol) || 1.5,
        rebalancing: rebalance,
        n_sims: Number(nSims) || 1000,
        percentiles: percentiles.length ? percentiles : [10, 25, 50, 75, 90],
        import_portfolio_id: allocMode === "import" ? importId || null : null,
        assets:
          allocMode === "import"
            ? []
            : rows
                .filter((r) => Number(r.weight) > 0)
                .map((r) => ({
                  asset_id: r.asset_id,
                  symbol: r.symbol,
                  weight: Number(r.weight) || 0,
                  mean: numOrUndef(r.mean) ?? null,
                  volatility: numOrUndef(r.volatility) ?? null,
                })),
      };
      setResult(await api.runMonteCarlo(body));
    } catch (e) {
      setResult(null);
      setErr(String((e as Error).message || e));
    } finally {
      setBusy(false);
    }
  };

  const classes = meta?.asset_classes || [];
  const showCfAmt = cashflows === "contribute_fixed" || cashflows === "withdraw_fixed" || cashflows === "rolling_avg";
  const showPct = cashflows === "withdraw_pct" || cashflows === "geometric";
  const showTax = tax === "aftertax";
  const showHistRange = !fullHist;
  const showBootstrap = model === "historical";
  const showStat = model !== "historical";
  const showBlock = bootstrap === "block";

  const legend = useMemo(() => Object.keys(result?.percentiles || {}).sort((a, b) => Number(a) - Number(b)), [result]);

  const patchRow = (i: number, patch: Partial<McAssetRow>) => {
    setRows((prev) => prev.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  };

  return (
    <div className="mc-layout">
      <div className="mc-form-col">
        <div className="section-h">Portfolio MC Simulation</div>
        <p className="mc-lead muted">
          Paths use monthly ETF/ticker history (Yahoo, Polygon fallback). Hypothetical. Not financial advice.
        </p>

        <div className="section-h">Simulation model</div>
        <div className="mc-grid">
          <label className="pf-field">
            Portfolio type
            <select
              value={allocMode}
              onChange={(e) => {
                const v = e.target.value as "classes" | "tickers" | "import";
                setAllocMode(v);
                if (v === "classes") {
                  const lazy = meta?.lazy_portfolios.find((p) => p.id === lazyId);
                  if (lazy) setRows(rowsFromLazy(lazy));
                }
              }}
            >
              <option value="classes">Asset classes</option>
              <option value="tickers">Tickers</option>
              <option value="import">Import portfolio</option>
            </select>
          </label>
          <label className="pf-field">
            Initial amount
            <input value={initial} onChange={(e) => setInitial(e.target.value)} inputMode="decimal" />
          </label>
          <label className="pf-field">
            Cashflows
            <select value={cashflows} onChange={(e) => setCashflows(e.target.value)}>
              <option value="none">No contributions or withdrawals</option>
              <option value="contribute_fixed">Contribute fixed amount periodically</option>
              <option value="withdraw_fixed">Withdraw fixed amount periodically</option>
              <option value="withdraw_pct">Withdraw fixed percentage periodically</option>
              <option value="rolling_avg">Rolling average spending rule</option>
              <option value="geometric">Geometric spending rule</option>
              <option value="life_expectancy">Withdraw based on life expectancy</option>
            </select>
          </label>
          {showCfAmt && (
            <label className="pf-field">
              Cashflow amount
              <input value={cfAmount} onChange={(e) => setCfAmount(e.target.value)} inputMode="decimal" />
            </label>
          )}
          {cashflows !== "none" && (
            <label className="pf-field">
              Inflation adjusted
              <select value={inflationAdj ? "yes" : "no"} onChange={(e) => setInflationAdj(e.target.value === "yes")}>
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </label>
          )}
          {showPct && (
            <label className="pf-field">
              Withdrawal percentage
              <input value={wdPct} onChange={(e) => setWdPct(e.target.value)} inputMode="decimal" />
            </label>
          )}
          {cashflows === "rolling_avg" && (
            <label className="pf-field">
              Rolling average periods
              <select value={rolling} onChange={(e) => setRolling(e.target.value)}>
                {[2, 3, 4, 5].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
          )}
          {cashflows === "geometric" && (
            <label className="pf-field">
              Smoothing rate
              <select value={smoothing} onChange={(e) => setSmoothing(e.target.value)}>
                {[50, 55, 60, 65, 70, 75, 80, 85, 90].map((n) => (
                  <option key={n} value={n}>
                    {n}%
                  </option>
                ))}
              </select>
            </label>
          )}
          {cashflows !== "none" && (
            <label className="pf-field">
              Withdrawal frequency
              <select value={freq} onChange={(e) => setFreq(e.target.value)}>
                <option value="monthly">Monthly</option>
                <option value="quarterly">Quarterly</option>
                <option value="annually">Annually</option>
              </select>
            </label>
          )}
          {cashflows === "life_expectancy" && (
            <>
              <label className="pf-field">
                Life expectancy model
                <select value={lifeModel} onChange={(e) => setLifeModel(e.target.value)}>
                  <option value="single">Single life expectancy</option>
                  <option value="uniform">Uniform life expectancy (70+)</option>
                </select>
              </label>
              <label className="pf-field">
                Current age
                <select value={age} onChange={(e) => setAge(e.target.value)}>
                  {AGES.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          <label className="pf-field">
            Simulation period in years
            <select value={years} onChange={(e) => setYears(e.target.value)}>
              {YEARS.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </label>
          <label className="pf-field">
            Tax treatment
            <select value={tax} onChange={(e) => setTax(e.target.value)}>
              <option value="pretax">Pre-tax returns</option>
              <option value="aftertax">After-tax returns</option>
            </select>
          </label>
          <label className="pf-field">
            Investment horizon
            <select value={horizon} onChange={(e) => setHorizon(e.target.value)}>
              <option value="simulated">Simulated period</option>
              <option value="perpetual">Perpetual</option>
            </select>
          </label>
          {showTax && (
            <>
              <label className="pf-field">
                Federal income tax %
                <input value={fed} onChange={(e) => setFed(e.target.value)} />
              </label>
              <label className="pf-field">
                Capital gains tax %
                <input value={cg} onChange={(e) => setCg(e.target.value)} />
              </label>
              <label className="pf-field">
                Dividend tax %
                <input value={div} onChange={(e) => setDiv(e.target.value)} />
              </label>
              <label className="pf-field">
                Affordable Care Act tax %
                <input value={aca} onChange={(e) => setAca(e.target.value)} />
              </label>
              <label className="pf-field">
                State income tax %
                <input value={stateTax} onChange={(e) => setStateTax(e.target.value)} />
              </label>
            </>
          )}
          <label className="pf-field">
            Simulation model
            <select value={model} onChange={(e) => setModel(e.target.value)}>
              <option value="historical">Historical returns</option>
              <option value="forecasted">Forecasted returns</option>
              <option value="statistical">Statistical returns</option>
              <option value="parameterized">Parameterized returns</option>
            </select>
          </label>
          {showStat && (
            <>
              <label className="pf-field">
                Time series model
                <select value={tsModel} onChange={(e) => setTsModel(e.target.value)}>
                  <option value="normal">Normal returns</option>
                  <option value="garch">GARCH model</option>
                </select>
              </label>
              <label className="pf-field">
                Distribution
                <select value={dist} onChange={(e) => setDist(e.target.value)}>
                  <option value="normal">Normal distribution</option>
                  <option value="student_t">Fat-tailed distribution</option>
                </select>
              </label>
              {dist === "student_t" && (
                <label className="pf-field">
                  Degrees of freedom
                  <input value={dof} onChange={(e) => setDof(e.target.value)} />
                </label>
              )}
              <label className="pf-field">
                Expected return %
                <input value={expRet} onChange={(e) => setExpRet(e.target.value)} placeholder="optional" />
              </label>
              <label className="pf-field">
                Volatility %
                <input value={vol} onChange={(e) => setVol(e.target.value)} placeholder="optional" />
              </label>
            </>
          )}
          <label className="pf-field">
            Risk-free rate %
            <input value={rf} onChange={(e) => setRf(e.target.value)} />
          </label>
          <label className="pf-field">
            Use historical volatility
            <select value={histVol ? "yes" : "no"} onChange={(e) => setHistVol(e.target.value === "yes")}>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </label>
          <label className="pf-field">
            Use historical correlations
            <select value={histCorr ? "yes" : "no"} onChange={(e) => setHistCorr(e.target.value === "yes")}>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </label>
          <label className="pf-field">
            Use full history
            <select value={fullHist ? "yes" : "no"} onChange={(e) => setFullHist(e.target.value === "yes")}>
              <option value="yes">Yes</option>
              <option value="no">No</option>
            </select>
          </label>
          {showHistRange && (
            <>
              <label className="pf-field">
                Start year
                <select value={startYear} onChange={(e) => setStartYear(e.target.value)}>
                  {START_YEARS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
              <label className="pf-field">
                End year
                <select value={endYear} onChange={(e) => setEndYear(e.target.value)}>
                  {START_YEARS.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}
          {showBootstrap && (
            <>
              <label className="pf-field">
                Bootstrap model
                <select value={bootstrap} onChange={(e) => setBootstrap(e.target.value)}>
                  <option value="month">Single month</option>
                  <option value="year">Single year</option>
                  <option value="block">Block of years</option>
                </select>
              </label>
              {showBlock && (
                <>
                  <label className="pf-field">
                    Block min. years
                    <input value={blockMin} onChange={(e) => setBlockMin(e.target.value)} />
                  </label>
                  <label className="pf-field">
                    Block max. years
                    <input value={blockMax} onChange={(e) => setBlockMax(e.target.value)} />
                  </label>
                </>
              )}
              <label className="pf-field">
                Circular bootstrapping
                <select value={circular ? "yes" : "no"} onChange={(e) => setCircular(e.target.value === "yes")}>
                  <option value="no">No</option>
                  <option value="yes">Yes</option>
                </select>
              </label>
            </>
          )}
          <label className="pf-field">
            Sequence of returns risk
            <select value={seqRisk} onChange={(e) => setSeqRisk(e.target.value)}>
              <option value="0">No adjustments</option>
              {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>
                  Worst {n} year{n > 1 ? "s" : ""} first
                </option>
              ))}
            </select>
          </label>
          <label className="pf-field">
            Inflation model
            <select value={infModel} onChange={(e) => setInfModel(e.target.value)}>
              <option value="parameterized">Parameterized inflation</option>
              <option value="historical">Historical inflation (parameterized vol)</option>
            </select>
          </label>
          <label className="pf-field">
            Inflation mean %
            <input value={infMean} onChange={(e) => setInfMean(e.target.value)} />
          </label>
          <label className="pf-field">
            Inflation volatility %
            <input value={infVol} onChange={(e) => setInfVol(e.target.value)} />
          </label>
          <label className="pf-field">
            Rebalancing
            <select value={rebalance} onChange={(e) => setRebalance(e.target.value)}>
              <option value="none">No rebalancing</option>
              <option value="annually">Rebalance annually</option>
              <option value="semi">Rebalance semi-annually</option>
              <option value="quarterly">Rebalance quarterly</option>
              <option value="monthly">Rebalance monthly</option>
            </select>
          </label>
          <label className="pf-field">
            Simulations
            <input value={nSims} onChange={(e) => setNSims(e.target.value)} />
          </label>
          <label className="pf-field">
            Percentile intervals
            <input value={pctCustom} onChange={(e) => setPctCustom(e.target.value)} />
          </label>
        </div>

        <div className="section-h">Asset allocation</div>
        <div className="mc-alloc-tools">
          {allocMode === "classes" && (
            <label className="pf-field">
              Lazy portfolios
              <select
                value={lazyId}
                onChange={(e) => {
                  setLazyId(e.target.value);
                  const lazy = meta?.lazy_portfolios.find((p) => p.id === e.target.value);
                  if (lazy) setRows(rowsFromLazy(lazy));
                }}
              >
                <option value="">Custom</option>
                {(meta?.lazy_portfolios || []).map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          {allocMode === "import" && (
            <label className="pf-field">
              Import paper fund
              <select value={importId} onChange={(e) => void applyImport(e.target.value)}>
                <option value="">Select a fund…</option>
                {funds.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="mc-alloc-btns">
            <button
              type="button"
              onClick={() =>
                setRows(equalWeight(rows, Math.max(1, filled)))
              }
            >
              Equal weight
            </button>
            <button type="button" onClick={() => setRows(normalizeWeights(rows))}>
              Normalize
            </button>
            <button
              type="button"
              onClick={() => setRows(Array.from({ length: 10 }, () => EMPTY_ROW()))}
            >
              Clear
            </button>
          </div>
        </div>
        <div className="mc-alloc-table">
          <div className="mc-alloc-head">
            <span>Asset</span>
            <span>Weight %</span>
            <span>Mean %</span>
            <span>Vol %</span>
          </div>
          {rows.map((r, i) => (
            <div className="mc-alloc-row" key={i}>
              {allocMode === "classes" ? (
                <select value={r.asset_id} onChange={(e) => patchRow(i, { asset_id: e.target.value })}>
                  <option value="">Select asset class…</option>
                  {classes.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} ({c.symbol})
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  value={r.symbol}
                  placeholder="Ticker"
                  onChange={(e) => patchRow(i, { symbol: e.target.value.toUpperCase() })}
                  readOnly={allocMode === "import"}
                />
              )}
              <input value={r.weight} onChange={(e) => patchRow(i, { weight: e.target.value })} />
              <input value={r.mean} onChange={(e) => patchRow(i, { mean: e.target.value })} placeholder="hist" />
              <input
                value={r.volatility}
                onChange={(e) => patchRow(i, { volatility: e.target.value })}
                placeholder="hist"
              />
            </div>
          ))}
          <div className="mc-alloc-total muted">Total {weightSum.toFixed(1)}%</div>
        </div>
        {err && <div className="err">{err}</div>}
        <button type="button" className="llm-btn mc-run" disabled={busy} onClick={() => void run()}>
          {busy ? "Running…" : "Run simulation"}
        </button>
      </div>

      <section className="mc-results">
        {!result && <div className="muted mc-empty">Run a simulation to see percentile paths, terminal value, and success rate.</div>}
        {result && (
          <>
            <div className="section-h">Simulated portfolio value</div>
            <div className="mc-legend">
              {legend.map((k) => (
                <span key={k}>P{k}</span>
              ))}
            </div>
            <MonteCarloChart yearsAxis={result.years_axis} percentiles={result.percentiles} />
            <div className="mc-stats">
              <div>
                <div className="muted">Success rate</div>
                <div>{pct(result.success_rate)}</div>
              </div>
              <div>
                <div className="muted">Terminal median</div>
                <div>{money(result.terminal.median)}</div>
              </div>
              <div>
                <div className="muted">Terminal P10 / P90</div>
                <div>
                  {money(result.terminal.p10)} / {money(result.terminal.p90)}
                </div>
              </div>
              <div>
                <div className="muted">CAGR median</div>
                <div>{pct(result.cagr.median)}</div>
              </div>
              <div>
                <div className="muted">Max DD median</div>
                <div>{pct(result.max_drawdown.median)}</div>
              </div>
              <div>
                <div className="muted">Sample mean / vol</div>
                <div>
                  {pct(result.portfolio_sample?.mean)} / {pct(result.portfolio_sample?.volatility)}
                </div>
              </div>
            </div>
            {result.note && <p className="muted">{result.note}</p>}
            <p className="muted">
              {result.n_sims} paths · {result.history_months} months of history ({result.history_start} to{" "}
              {result.history_end}) · {result.source}
            </p>
            <div className="section-h">Allocation used</div>
            <table className="pf-table">
              <thead>
                <tr>
                  <th>Asset</th>
                  <th>Ticker</th>
                  <th>Weight</th>
                  <th>Mean</th>
                  <th>Vol</th>
                </tr>
              </thead>
              <tbody>
                {result.assets.map((a) => (
                  <tr key={a.symbol + a.label}>
                    <td>{a.label}</td>
                    <td>{a.symbol}</td>
                    <td>{a.weight.toFixed(1)}%</td>
                    <td>{pct(a.mean)}</td>
                    <td>{pct(a.volatility)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="section-h">Yearly percentiles</div>
            <div className="mc-year-wrap">
              <table className="pf-table">
                <thead>
                  <tr>
                    <th>Year</th>
                    <th>Mean</th>
                    {legend.map((k) => (
                      <th key={k}>P{k}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.yearly.map((y) => (
                    <tr key={y.year}>
                      <td>{y.year}</td>
                      <td>{money(y.mean)}</td>
                      {legend.map((k) => (
                        <td key={k}>{money(y[`p${k}`] as number)}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted">{result.disclaimer}</p>
          </>
        )}
      </section>
    </div>
  );
}
