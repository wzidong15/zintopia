import type { DeepAnalysis } from "./deep";
import type { PeerList } from "./fundamentals";
import { cls, fmt, fmtInt, pct } from "./format";

function money(n?: number | null) {
  if (n == null || Number.isNaN(n)) return "—";
  const abs = Math.abs(n);
  const sign = n < 0 ? "-" : "";
  if (abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(2)}B`;
  if (abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(2)}M`;
  if (abs >= 1e3) return `${sign}$${(abs / 1e3).toFixed(1)}K`;
  return `${sign}$${abs.toFixed(0)}`;
}
function num(n?: number | null, d = 2) {
  if (n == null || Number.isNaN(n)) return "—";
  return n.toLocaleString(undefined, { maximumFractionDigits: d });
}
function actionClass(action: string) {
  if (action === "ACCUMULATE" || action === "LEAN LONG") return "badge buy";
  if (action === "REDUCE" || action === "AVOID") return "badge sell";
  return "badge neutral";
}
function scoreTone(action: string) {
  if (action === "ACCUMULATE" || action === "LEAN LONG") return "buy";
  if (action === "REDUCE" || action === "AVOID") return "sell";
  return "neutral";
}

export default function DeepPanel({
  data,
  loading,
  error,
  peers,
  onPickPeer,
}: {
  data: DeepAnalysis | null;
  loading: boolean;
  error?: string | null;
  peers?: PeerList | null;
  onPickPeer?: (symbol: string) => void;
}) {
  if (loading) {
    return (
      <section className="deep" id="deep-analysis">
        <div className="section-h">Deep analysis</div>
        <div className="summary">Loading insider, options, Congress, and forecast…</div>
      </section>
    );
  }
  if (error) {
    return (
      <section className="deep" id="deep-analysis">
        <div className="section-h">Deep analysis</div>
        <div className="err">{error}</div>
      </section>
    );
  }
  if (!data) return null;
  const s = data.suggestion;
  const an = data.options.analysis || null;
  const ladder = an?.oi_by_strike || [];
  const oiMax = Math.max(1, ...ladder.map((r) => Math.max(r.call_oi, r.put_oi)));
  return (
    <section className="deep" id="deep-analysis">
      <div className="section-h">Deep analysis · {data.symbol}</div>
      <div className="decision">
        <div>
          <div className="k">Investment suggestion</div>
          <div className="decision-row">
            <span className={actionClass(s.action)}>{s.action}</span>
            <span className="px">score {s.score}/100</span>
          </div>
          <div className="score-track" aria-hidden>
            <div className={`score-fill ${scoreTone(s.action)}`} style={{ width: `${s.score}%` }} />
          </div>
        </div>
        <ul>
          {s.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
        <div className="muted">{s.disclaimer}</div>
      </div>

      <div className="deep-grid">
        <article>
          <div className="section-h">
            Forecast
            <span className="muted">
              {data.forecast.recommendation || "—"}
              {data.forecast.analysts ? ` · ${data.forecast.analysts} analysts` : ""}
            </span>
          </div>
          <div className="stats" style={{ gridTemplateColumns: "1fr 1fr 1fr", padding: "0 12px 8px" }}>
            <div className="stat">
              <div className="k">Mean target</div>
              <div className="v">{num(data.forecast.target_mean)}</div>
            </div>
            <div className="stat">
              <div className="k">Range</div>
              <div className="v">
                {num(data.forecast.target_low)}–{num(data.forecast.target_high)}
              </div>
            </div>
            <div className="stat">
              <div className="k">Implied upside</div>
              <div className={`v ${(data.forecast.upside_pct ?? 0) >= 0 ? "up" : "down"}`}>
                {data.forecast.upside_pct == null ? "—" : `${data.forecast.upside_pct.toFixed(1)}%`}
              </div>
            </div>
          </div>
        </article>

        <article className="opt-card">
          <div className="section-h">
            Options
            <span className="muted">
              {an?.expiry ? `${an.expiry}${an.dte != null ? ` · ${an.dte}d` : ""}` : "next 3 expiries"}
              {" · vol P/C "}
              {data.options.put_call == null ? "—" : data.options.put_call.toFixed(2)}
            </span>
          </div>
          <div className="stats opt-stats">
            <div className="stat">
              <div className="k">ATM IV</div>
              <div className="v">{an?.atm_iv == null ? "—" : `${(an.atm_iv * 100).toFixed(1)}%`}</div>
              <div className="sub">{an?.atm_strike != null ? `strike ${num(an.atm_strike, 1)}` : "nearest monthly"}</div>
            </div>
            <div className="stat">
              <div className="k">IV rank</div>
              <div className="v">
                {an?.iv_rank == null
                  ? "collecting"
                  : `${an.iv_rank.toFixed(0)}`}
              </div>
              <div className="sub">
                {an?.iv_rank == null
                  ? `${an?.iv_samples ?? 0}/${an?.iv_samples_needed ?? 20} daily samples`
                  : `pctile ${an.iv_percentile == null ? "—" : an.iv_percentile.toFixed(0)} · ${an.iv_samples ?? 0}d`}
              </div>
            </div>
            <div className="stat">
              <div className="k">Expected move</div>
              <div className="v">
                {an?.expected_move_pct == null ? "—" : `±${an.expected_move_pct.toFixed(1)}%`}
              </div>
              <div className="sub">
                {an?.expected_low != null && an?.expected_high != null
                  ? `${num(an.expected_low, 2)} – ${num(an.expected_high, 2)}`
                  : an?.expected_move_basis || "—"}
              </div>
            </div>
            <div className="stat">
              <div className="k">Max pain</div>
              <div className="v">{an?.max_pain == null ? "—" : num(an.max_pain, 1)}</div>
              <div className="sub">
                {an?.max_pain != null && an?.spot
                  ? `${(((an.max_pain - an.spot) / an.spot) * 100).toFixed(1)}% vs spot`
                  : "—"}
              </div>
            </div>
          </div>
          {ladder.length > 0 && (
            <div className="oi-ladder">
              <div className="oi-head">
                <span className="down">Put OI</span>
                <span>Strike</span>
                <span className="up">Call OI</span>
              </div>
              {ladder.map((r) => (
                <div
                  key={r.strike}
                  className={`oi-row${r.strike === an?.atm_strike ? " atm" : ""}${r.strike === an?.max_pain ? " pain" : ""}`}
                  title={`${num(r.strike, 1)} · ${fmtInt(r.put_oi)} puts · ${fmtInt(r.call_oi)} calls`}
                >
                  <div className="oi-side put">
                    <span className="oi-n">{fmtInt(r.put_oi)}</span>
                    <span className="oi-bar down" style={{ width: `${(r.put_oi / oiMax) * 100}%` }} />
                  </div>
                  <div className="oi-k">{num(r.strike, 1)}</div>
                  <div className="oi-side call">
                    <span className="oi-bar up" style={{ width: `${(r.call_oi / oiMax) * 100}%` }} />
                    <span className="oi-n">{fmtInt(r.call_oi)}</span>
                  </div>
                </div>
              ))}
              <div className="oi-foot muted">
                OI put/call {an?.oi_put_call == null ? "—" : an.oi_put_call.toFixed(2)} · {fmtInt(an?.call_oi)} calls ·{" "}
                {fmtInt(an?.put_oi)} puts · spot row highlighted
                {an?.max_pain != null && ladder.some((r) => r.strike === an.max_pain)
                  ? " · max pain underlined"
                  : an?.max_pain != null
                    ? ` · max pain ${num(an.max_pain, 1)} is outside this ladder`
                    : ""}
              </div>
            </div>
          )}
          <div className="opt-sub-h">
            Unusual volume
            <span className="muted">
              next 3 expiries · {num(data.options.call_volume, 0)} calls · {num(data.options.put_volume, 0)} puts
            </span>
          </div>
          <table>
            <thead>
              <tr>
                <th>Side</th>
                <th>Exp</th>
                <th>Strike</th>
                <th>Vol</th>
                <th>OI</th>
                <th>Vol/OI</th>
              </tr>
            </thead>
            <tbody>
              {data.options.items.slice(0, 6).map((o, i) => (
                <tr key={i}>
                  <td className={o.side === "call" ? "up" : "down"}>{o.side}</td>
                  <td>{(o.expiry || data.options.expiry || "—").slice(5)}</td>
                  <td>{num(o.strike, 1)}</td>
                  <td>{num(o.volume, 0)}</td>
                  <td>{num(o.open_interest, 0)}</td>
                  <td>{num(o.vol_oi, 1)}</td>
                </tr>
              ))}
              {data.options.items.length === 0 && (
                <tr>
                  <td colSpan={6} className="muted">
                    {data.options.error
                      ? `Options request failed: ${data.options.error}`
                      : data.options.expiry
                        ? "No unusual volume on the nearest chains"
                        : "Yahoo returned no option chain for this ticker"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </article>

        <article>
          <div className="section-h">
            Insider trades
            <span className="muted">net {money(data.insiders.net_value)}</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Insider</th>
                <th>Text</th>
                <th>Value</th>
              </tr>
            </thead>
            <tbody>
              {data.insiders.items.slice(0, 6).map((r, i) => (
                <tr key={i}>
                  <td>{r.date || "—"}</td>
                  <td>{r.insider || "—"}</td>
                  <td>{(r.text || "").slice(0, 42)}</td>
                  <td>{money(r.value)}</td>
                </tr>
              ))}
              {data.insiders.items.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    No recent Form 4 rows
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </article>

        <article>
          <div className="section-h">
            Senate / House PTR trades
            <span className="muted">
              {data.congress.buy_count ?? 0} buy · {data.congress.sell_count ?? 0} sell
              {data.congress.status === "refreshing"
                ? " · updating"
                : data.congress.filed_through
                  ? ` · filed through ${String(data.congress.filed_through).slice(0, 10)}`
                  : ""}
            </span>
          </div>
          <div className="summary">
            Periodic transaction reports from the House Clerk and Senate eFD, not live holdings.
            {data.congress.source ? ` ${data.congress.source}.` : ""}
            {data.congress.note ? ` ${data.congress.note}` : ""}
          </div>
          <table>
            <thead>
              <tr>
                <th>Date</th>
                <th>Member</th>
                <th>Type</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
              {data.congress.items.slice(0, 6).map((r, i) => (
                <tr key={i}>
                  <td>{r.date || "—"}</td>
                  <td>
                    {r.link ? (
                      <a href={r.link} target="_blank" rel="noreferrer">
                        {r.person || "—"}
                      </a>
                    ) : (
                      r.person || "—"
                    )}
                    {r.chamber ? ` (${r.chamber})` : ""}
                  </td>
                  <td>{r.type || "—"}</td>
                  <td>{r.amount || "—"}</td>
                </tr>
              ))}
              {data.congress.items.length === 0 && (
                <tr>
                  <td colSpan={4} className="muted">
                    {data.congress.status === "refreshing"
                      ? "Updating official periodic transaction reports…"
                      : "No matching periodic transaction reports"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </article>
      </div>

      {peers && (
        <article className="peer-block">
          <div className="section-h">
            Peers
            <span className="muted">{peers.sector || "same sector"} · P/E · cap · 1M</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>P/E</th>
                <th>Cap</th>
                <th>P/S</th>
                <th>1M</th>
              </tr>
            </thead>
            <tbody>
              {peers.items.map((p) => (
                <tr
                  key={p.ticker}
                  className={p.symbol === data.symbol ? "peer-self" : "peer-row"}
                  onClick={() => p.symbol !== data.symbol && onPickPeer?.(p.symbol)}
                >
                  <td className="sym">{p.symbol}</td>
                  <td>{fmt(p.pe, 1)}</td>
                  <td>{fmtInt(p.market_cap)}</td>
                  <td>{fmt(p.ps, 1)}</td>
                  <td className={cls(p.perf_1m)}>{pct(p.perf_1m)}</td>
                </tr>
              ))}
              {peers.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="muted">
                    No same-sector peers from TradingView
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </article>
      )}
    </section>
  );
}
