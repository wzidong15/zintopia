"""Portfolio Monte Carlo using monthly ETF/ticker history (Yahoo, Polygon fallback)."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Literal

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from portfolios import get_portfolio

_history: Callable[[str, str], dict[str, Any]] | None = None
router = APIRouter(prefix="/api/monte-carlo", tags=["monte-carlo"])

ASSET_CLASSES: list[dict[str, str]] = [
    {"id": "us_stock", "name": "US Stock Market", "symbol": "VTI"},
    {"id": "us_large", "name": "US Large Cap", "symbol": "VV"},
    {"id": "us_large_value", "name": "US Large Cap Value", "symbol": "VTV"},
    {"id": "us_large_growth", "name": "US Large Cap Growth", "symbol": "VUG"},
    {"id": "us_mid", "name": "US Mid Cap", "symbol": "VO"},
    {"id": "us_mid_value", "name": "US Mid Cap Value", "symbol": "IWS"},
    {"id": "us_mid_growth", "name": "US Mid Cap Growth", "symbol": "IWP"},
    {"id": "us_small", "name": "US Small Cap", "symbol": "VB"},
    {"id": "us_small_value", "name": "US Small Cap Value", "symbol": "VBR"},
    {"id": "us_small_growth", "name": "US Small Cap Growth", "symbol": "VBK"},
    {"id": "us_micro", "name": "US Micro Cap", "symbol": "IWC"},
    {"id": "global_ex_us", "name": "Global ex-US Stock Market", "symbol": "VXUS"},
    {"id": "intl_dev", "name": "Intl Developed ex-US Market", "symbol": "VEA"},
    {"id": "intl_small", "name": "International ex-US Small Cap", "symbol": "VSS"},
    {"id": "intl_value", "name": "International ex-US Value", "symbol": "EFV"},
    {"id": "europe", "name": "European Stocks", "symbol": "VGK"},
    {"id": "pacific", "name": "Pacific Stocks", "symbol": "VPL"},
    {"id": "em", "name": "Emerging Markets", "symbol": "VWO"},
    {"id": "cash", "name": "Cash", "symbol": "SHV"},
    {"id": "st_treas", "name": "Short Term Treasury", "symbol": "SHY"},
    {"id": "it_treas", "name": "Intermediate Term Treasury", "symbol": "IEF"},
    {"id": "ten_treas", "name": "10-year Treasury", "symbol": "IEF"},
    {"id": "lt_treas", "name": "Long Term Treasury", "symbol": "TLT"},
    {"id": "us_bond", "name": "Total US Bond Market", "symbol": "BND"},
    {"id": "tips", "name": "TIPS", "symbol": "TIP"},
    {"id": "global_bond", "name": "Global Bonds (Unhedged)", "symbol": "BNDX"},
    {"id": "global_bond_h", "name": "Global Bonds (USD Hedged)", "symbol": "BNDX"},
    {"id": "st_ig", "name": "Short-Term Investment Grade", "symbol": "VCSH"},
    {"id": "corp", "name": "Corporate Bonds", "symbol": "LQD"},
    {"id": "lt_corp", "name": "Long-Term Corporate Bonds", "symbol": "VCLT"},
    {"id": "hy", "name": "High Yield Corporate Bonds", "symbol": "HYG"},
    {"id": "st_muni", "name": "Short-Term Tax-Exempt", "symbol": "SUB"},
    {"id": "it_muni", "name": "Intermediate-Term Tax-Exempt", "symbol": "MUB"},
    {"id": "lt_muni", "name": "Long-Term Tax-Exempt", "symbol": "TFI"},
    {"id": "reit", "name": "REIT", "symbol": "VNQ"},
    {"id": "gold", "name": "Gold", "symbol": "GLD"},
    {"id": "pm", "name": "Precious Metals", "symbol": "SLV"},
    {"id": "comm", "name": "Commodities", "symbol": "DBC"},
]
_CLASS_BY_ID = {a["id"]: a for a in ASSET_CLASSES}

LAZY_PORTFOLIOS: list[dict[str, Any]] = [
    {"id": "60_40", "name": "Stocks/Bonds (60/40)", "weights": {"us_stock": 60, "us_bond": 40}},
    {
        "id": "all_seasons",
        "name": "Ray Dalio All Seasons",
        "weights": {"us_stock": 30, "lt_treas": 40, "it_treas": 15, "comm": 7.5, "gold": 7.5},
    },
    {
        "id": "core_four",
        "name": "Rick Ferri Core Four",
        "weights": {"us_stock": 48, "global_ex_us": 24, "us_bond": 20, "reit": 8},
    },
    {
        "id": "no_brainer",
        "name": "Bill Bernstein No Brainer",
        "weights": {"us_large": 25, "us_small": 25, "intl_dev": 25, "us_bond": 25},
    },
    {
        "id": "coffee_house",
        "name": "Bill Schultheis Coffee House",
        "weights": {
            "us_large": 10,
            "us_large_value": 10,
            "us_small": 10,
            "us_small_value": 10,
            "reit": 10,
            "intl_dev": 10,
            "intl_value": 10,
            "em": 10,
            "it_treas": 10,
            "st_treas": 10,
        },
    },
    {
        "id": "swensen_lazy",
        "name": "David Swensen Lazy Portfolio",
        "weights": {"us_stock": 30, "intl_dev": 15, "em": 5, "reit": 20, "lt_treas": 15, "tips": 15},
    },
    {
        "id": "swensen_yale",
        "name": "David Swensen Yale Endowment",
        "weights": {"us_stock": 30, "intl_dev": 15, "em": 5, "reit": 20, "lt_treas": 15, "tips": 15},
    },
    {"id": "couch", "name": "Scott Burns Couch Portfolio", "weights": {"us_stock": 50, "us_bond": 50}},
    {
        "id": "permanent",
        "name": "Harry Browne Permanent Portfolio",
        "weights": {"us_stock": 25, "lt_treas": 25, "gold": 25, "cash": 25},
    },
    {
        "id": "swedroe_simple",
        "name": "Larry Swedroe Simple Portfolio",
        "weights": {"us_stock": 30, "us_small_value": 10, "intl_dev": 20, "em": 10, "us_bond": 30},
    },
    {
        "id": "swedroe_fat",
        "name": "Larry Swedroe Minimize FatTails Portfolio",
        "weights": {"us_small_value": 15, "intl_value": 15, "em": 10, "tips": 30, "st_treas": 30},
    },
    {
        "id": "ivy",
        "name": "Mebane Faber Ivy Portfolio",
        "weights": {"us_stock": 20, "global_ex_us": 20, "reit": 20, "comm": 20, "us_bond": 20},
    },
    {
        "id": "marc_faber",
        "name": "Marc Faber Portfolio",
        "weights": {"us_stock": 25, "us_bond": 25, "gold": 25, "cash": 25},
    },
]

MAX_ASSETS = 10
MAX_SIMS = 5000
MIN_MONTHS = 24


def configure(*, history: Callable[[str, str], dict[str, Any]]) -> None:
    global _history
    _history = history


class McAsset(BaseModel):
    asset_id: str = ""
    symbol: str = ""
    weight: float = Field(default=0, ge=0, le=100)
    mean: float | None = None
    volatility: float | None = None


class McBody(BaseModel):
    portfolio_type: Literal["asset_classes", "tickers"] = "asset_classes"
    initial_amount: float = Field(default=100_000, gt=0, le=1e12)
    cashflows: Literal[
        "none",
        "contribute_fixed",
        "withdraw_fixed",
        "withdraw_pct",
        "rolling_avg",
        "geometric",
        "life_expectancy",
    ] = "none"
    cashflow_amount: float = Field(default=0, ge=0, le=1e12)
    inflation_adjusted: bool = True
    withdrawal_pct: float = Field(default=4, ge=0, le=100)
    rolling_periods: int = Field(default=3, ge=2, le=5)
    smoothing_rate: float = Field(default=80, ge=50, le=90)
    withdrawal_frequency: Literal["monthly", "quarterly", "annually"] = "annually"
    life_expectancy_model: Literal["single", "uniform"] = "single"
    current_age: int = Field(default=65, ge=30, le=95)
    years: int = Field(default=30, ge=5, le=75)
    tax_treatment: Literal["pretax", "aftertax"] = "pretax"
    investment_horizon: Literal["simulated", "perpetual"] = "simulated"
    federal_income_tax: float = Field(default=22, ge=0, le=50)
    cap_gains_tax: float = Field(default=15, ge=0, le=40)
    dividend_tax: float = Field(default=15, ge=0, le=40)
    aca_tax: float = Field(default=0, ge=0, le=10)
    state_income_tax: float = Field(default=0, ge=0, le=15)
    simulation_model: Literal["historical", "forecasted", "statistical", "parameterized"] = "historical"
    time_series: Literal["normal", "garch"] = "normal"
    risk_free_rate: float = Field(default=4.5, ge=0, le=20)
    use_historical_vol: bool = True
    use_historical_corr: bool = True
    use_full_history: bool = True
    start_year: int = Field(default=1972, ge=1970, le=2026)
    end_year: int = Field(default=2026, ge=1972, le=2026)
    bootstrap: Literal["month", "year", "block"] = "year"
    block_min_years: int = Field(default=2, ge=1, le=10)
    block_max_years: int = Field(default=5, ge=1, le=15)
    circular: bool = False
    distribution: Literal["normal", "student_t"] = "normal"
    degrees_of_freedom: int = Field(default=10, ge=5, le=50)
    expected_return: float | None = Field(default=None, ge=-20, le=40)
    volatility: float | None = Field(default=None, ge=0, le=80)
    sequence_risk: int = Field(default=0, ge=0, le=10)
    inflation_model: Literal["historical", "parameterized"] = "parameterized"
    inflation_mean: float = Field(default=2.5, ge=-5, le=20)
    inflation_vol: float = Field(default=1.5, ge=0, le=15)
    rebalancing: Literal["none", "annually", "semi", "quarterly", "monthly"] = "annually"
    n_sims: int = Field(default=1000, ge=200, le=MAX_SIMS)
    percentiles: list[int] = Field(default_factory=lambda: [10, 25, 50, 75, 90])
    assets: list[McAsset] = Field(default_factory=list)
    import_portfolio_id: str | None = None


def _sym(raw: str) -> str:
    return str(raw or "").strip().upper().split(":")[-1]


def _month_key(ts: int) -> tuple[int, int]:
    dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
    return dt.year, dt.month


def _monthly_closes(bars: list[dict[str, Any]], start_year: int, end_year: int) -> dict[tuple[int, int], float]:
    last: dict[tuple[int, int], float] = {}
    for b in bars:
        px = b.get("close")
        t = b.get("time")
        if not isinstance(px, (int, float)) or px <= 0 or not isinstance(t, (int, float)):
            continue
        y, m = _month_key(int(t))
        if y < start_year or y > end_year:
            continue
        last[(y, m)] = float(px)
    return last


def _returns_from_closes(closes: dict[tuple[int, int], float]) -> tuple[list[tuple[int, int]], np.ndarray]:
    keys = sorted(closes)
    if len(keys) < 3:
        return [], np.array([])
    px = np.array([closes[k] for k in keys], dtype=float)
    rets = px[1:] / px[:-1] - 1.0
    return keys[1:], rets


def _fetch_symbol(symbol: str) -> dict[str, Any]:
    if _history is None:
        raise HTTPException(500, "History not configured")
    last_err: Exception | None = None
    for rng in ("max", "5y"):
        try:
            return _history(symbol, rng)
        except Exception as e:
            last_err = e
    raise HTTPException(502, f"History failed for {symbol}: {last_err}") from last_err


def _life_expectancy(age: int, model: str) -> float:
    """IRS-like remaining years; Uniform Lifetime after 70, else a single-life approximation."""
    age = int(max(30, min(110, age)))
    if model == "uniform":
        # IRS Uniform Lifetime (account-holder table), clipped
        return max(1.1, 27.4 - 0.72 * max(0, age - 72))
    table = {
        30: 53.3,
        40: 43.6,
        50: 34.2,
        55: 29.6,
        60: 25.2,
        65: 21.0,
        70: 17.0,
        75: 13.4,
        80: 10.2,
        85: 7.6,
        90: 5.5,
        95: 4.0,
    }
    keys = sorted(table)
    if age <= keys[0]:
        return table[keys[0]]
    if age >= keys[-1]:
        return table[keys[-1]]
    for i, a in enumerate(keys[:-1]):
        b = keys[i + 1]
        if a <= age <= b:
            t = (age - a) / (b - a)
            return table[a] + t * (table[b] - table[a])
    return 20.0


def _rebalance_every(kind: str) -> int:
    return {"none": 0, "monthly": 1, "quarterly": 3, "semi": 6, "annually": 12}.get(kind, 12)


def _cf_every(kind: str) -> int:
    return {"monthly": 1, "quarterly": 3, "annually": 12}.get(kind, 12)


def _tax_haircut(federal: float, state: float, cg: float, div: float, aca: float) -> float:
    """Blend ordinary / gains / dividend into a simple return haircut on positive months."""
    blended = 0.4 * (federal + state) + 0.4 * cg + 0.2 * (div + aca)
    return max(0.0, min(0.6, blended / 100.0))


def _garch_vol(resid: np.ndarray) -> np.ndarray:
    """One-step GARCH(1,1) variances; omega/alpha/beta via variance targeting."""
    r = np.asarray(resid, dtype=float)
    if r.size < 12:
        v = float(np.var(r)) if r.size else 1.0
        return np.full(max(r.size, 1), max(v, 1e-8))
    var = float(np.var(r))
    alpha, beta = 0.06, 0.91
    omega = max(var * (1 - alpha - beta), 1e-10)
    out = np.empty(r.size)
    out[0] = var
    for t in range(1, r.size):
        out[t] = omega + alpha * r[t - 1] ** 2 + beta * out[t - 1]
    return np.maximum(out, 1e-10)


def _mv_draw(n_sims: int, n_steps: int, mu: np.ndarray, cov: np.ndarray, df: int | None, rng: np.random.Generator) -> np.ndarray:
    n_assets = mu.shape[0]
    z = rng.standard_normal((n_sims, n_steps, n_assets))
    try:
        chol = np.linalg.cholesky(cov + 1e-10 * np.eye(n_assets))
    except np.linalg.LinAlgError:
        chol = np.diag(np.sqrt(np.clip(np.diag(cov), 1e-10, None)))
    z = z @ chol.T
    if df is not None:
        chi = rng.chisquare(df, size=(n_sims, n_steps, 1))
        z = z * np.sqrt(df / np.maximum(chi, 1e-8))
    return z + mu.reshape(1, 1, -1)


def _bootstrap_idx(
    n_hist: int,
    years_hist: np.ndarray,
    n_sims: int,
    n_steps: int,
    mode: str,
    block_min: int,
    block_max: int,
    circular: bool,
    rng: np.random.Generator,
) -> np.ndarray:
    idx = np.empty((n_sims, n_steps), dtype=int)
    if mode == "month":
        draw = rng.integers(0, n_hist, size=(n_sims, n_steps))
        return draw
    # year / block of years
    uniq_years = np.unique(years_hist)
    year_to_idx = {int(y): np.where(years_hist == y)[0] for y in uniq_years}
    year_list = [int(y) for y in uniq_years]
    n_y = len(year_list)
    if n_y == 0:
        return rng.integers(0, n_hist, size=(n_sims, n_steps))
    for s in range(n_sims):
        pos = 0
        while pos < n_steps:
            if mode == "year":
                length = 1
                start = int(rng.integers(0, n_y))
            else:
                length = int(rng.integers(block_min, block_max + 1))
                if circular:
                    start = int(rng.integers(0, n_y))
                else:
                    start = int(rng.integers(0, max(1, n_y - length + 1)))
            for k in range(length):
                yi = (start + k) % n_y if circular else min(start + k, n_y - 1)
                months = year_to_idx[year_list[yi]]
                for m in months:
                    if pos >= n_steps:
                        break
                    idx[s, pos] = int(m)
                    pos += 1
                if pos >= n_steps:
                    break
    return idx


def _resolve_assets(body: McBody) -> tuple[list[dict[str, Any]], str | None]:
    note = None
    rows: list[dict[str, Any]] = []
    if body.import_portfolio_id:
        fund = get_portfolio(body.import_portfolio_id, live=True)
        nav = float(fund.get("nav") or 0)
        cash = float(fund.get("cash") or 0)
        if nav <= 0:
            raise HTTPException(400, "Imported fund has no NAV to allocate")
        holdings = list(fund.get("holdings") or [])
        parts: list[tuple[str, float]] = []
        for h in holdings:
            sym = _sym(str(h.get("symbol") or ""))
            mv = h.get("market_value")
            if not sym:
                continue
            if not isinstance(mv, (int, float)):
                sh = h.get("shares")
                px = h.get("last_price")
                mv = float(sh) * float(px) if isinstance(sh, (int, float)) and isinstance(px, (int, float)) else 0
            if float(mv) > 0:
                parts.append((sym, float(mv)))
        if cash > 0:
            parts.append(("SHV", cash))
        parts.sort(key=lambda x: x[1], reverse=True)
        if len(parts) > MAX_ASSETS:
            note = f"Using the top {MAX_ASSETS} holdings by value; remaining weight was dropped."
            parts = parts[:MAX_ASSETS]
        total = sum(v for _, v in parts) or nav
        for sym, mv in parts:
            rows.append({"label": sym, "symbol": sym, "weight": 100.0 * mv / total, "mean": None, "vol": None})
        if not rows:
            raise HTTPException(400, "Imported fund has no stock/ETF holdings")
        return rows, note

    for a in body.assets:
        w = float(a.weight or 0)
        if w <= 0:
            continue
        if body.portfolio_type == "asset_classes":
            cls = _CLASS_BY_ID.get(a.asset_id)
            if not cls:
                continue
            rows.append(
                {
                    "label": cls["name"],
                    "symbol": cls["symbol"],
                    "weight": w,
                    "mean": a.mean,
                    "vol": a.volatility,
                }
            )
        else:
            sym = _sym(a.symbol)
            if not sym:
                continue
            rows.append({"label": sym, "symbol": sym, "weight": w, "mean": a.mean, "vol": a.volatility})
        if len(rows) >= MAX_ASSETS:
            break
    if not rows:
        raise HTTPException(400, "Add at least one asset with weight > 0, or import a paper fund")
    s = sum(r["weight"] for r in rows)
    if s <= 0:
        raise HTTPException(400, "Weights must sum to more than 0")
    if abs(s - 100) > 1.5:
        note = f"Weights summed to {s:.1f}% and were normalized to 100%."
        for r in rows:
            r["weight"] = 100.0 * r["weight"] / s
    return rows, note


def _align_returns(symbols: list[str], start_year: int, end_year: int) -> tuple[np.ndarray, np.ndarray, list[str]]:
    fetched: dict[str, dict[tuple[int, int], float]] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=min(6, len(symbols) or 1)) as pool:
        futs = {pool.submit(_fetch_symbol, sym): sym for sym in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                payload = fut.result()
                fetched[sym] = _monthly_closes(payload.get("bars") or [], start_year, end_year)
            except Exception as e:
                errors.append(f"{sym}: {e}")
    if errors and len(fetched) < len(symbols):
        missing = [s for s in symbols if s not in fetched]
        if missing:
            raise HTTPException(502, "Could not load history for " + ", ".join(missing) + f" ({'; '.join(errors[:4])})")
    common: set[tuple[int, int]] | None = None
    for sym in symbols:
        keys = set(fetched.get(sym) or {})
        common = keys if common is None else common & keys
    if not common or len(common) < MIN_MONTHS + 1:
        raise HTTPException(
            400,
            f"Need at least {MIN_MONTHS} overlapping months of history in {start_year}–{end_year}.",
        )
    months = sorted(common)
    closes = np.column_stack([[fetched[s][m] for m in months] for s in symbols])
    rets = closes[1:] / closes[:-1] - 1.0
    month_keys = months[1:]
    years = np.array([y for y, _ in month_keys], dtype=int)
    return rets.astype(float), years, [f"{y}-{m:02d}" for y, m in month_keys]


@router.get("/meta")
def monte_carlo_meta() -> dict[str, Any]:
    return {
        "asset_classes": ASSET_CLASSES,
        "lazy_portfolios": LAZY_PORTFOLIOS,
        "max_assets": MAX_ASSETS,
        "max_sims": MAX_SIMS,
        "history": "Yahoo monthly (max); Polygon monthly if Yahoo 429s and a key is set.",
        "disclaimer": "Hypothetical. Not a forecast. Not financial advice.",
    }


@router.post("")
def run_monte_carlo(body: McBody) -> dict[str, Any]:
    rows, note = _resolve_assets(body)
    symbols = [r["symbol"] for r in rows]
    weights = np.array([r["weight"] / 100.0 for r in rows], dtype=float)
    start_y = 1972 if body.use_full_history else min(body.start_year, body.end_year)
    end_y = 2026 if body.use_full_history else max(body.start_year, body.end_year)
    hist, years, month_labels = _align_returns(symbols, start_y, end_y)
    n_hist, n_assets = hist.shape
    port_hist = hist @ weights
    mu_m = hist.mean(axis=0)
    vol_m = hist.std(axis=0, ddof=1)
    cov = np.cov(hist.T) if n_assets > 1 else np.array([[float(np.var(hist, ddof=1))]])
    corr = np.corrcoef(hist.T) if n_assets > 1 else np.array([[1.0]])
    if n_assets == 1:
        corr = np.array([[1.0]])

    # Parameter / forecast overrides (annual % → monthly)
    mu_use = mu_m.copy()
    vol_use = vol_m.copy()
    for i, r in enumerate(rows):
        if r.get("mean") is not None:
            mu_use[i] = (1 + float(r["mean"]) / 100.0) ** (1 / 12) - 1
        elif body.expected_return is not None and body.simulation_model in ("forecasted", "parameterized", "statistical"):
            mu_use[i] = (1 + float(body.expected_return) / 100.0) ** (1 / 12) - 1
        if r.get("vol") is not None:
            vol_use[i] = float(r["vol"]) / 100.0 / np.sqrt(12)
        elif body.volatility is not None and not body.use_historical_vol:
            vol_use[i] = float(body.volatility) / 100.0 / np.sqrt(12)
        elif not body.use_historical_vol and body.simulation_model in ("forecasted", "parameterized"):
            pass
    if not body.use_historical_corr:
        corr = np.eye(n_assets)
    vol_use = np.maximum(vol_use, 1e-6)
    cov_use = np.outer(vol_use, vol_use) * corr

    n_sims = int(body.n_sims)
    n_steps = int(body.years) * 12
    if body.investment_horizon == "perpetual":
        n_steps = min(75 * 12, n_steps * 2)
    rng = np.random.default_rng()
    df = body.degrees_of_freedom if body.distribution == "student_t" else None

    if body.sequence_risk:
        uniq = np.unique(years)
        scores = []
        for y in uniq:
            mask = years == y
            scores.append((float(np.prod(1.0 + port_hist[mask]) - 1.0), int(y)))
        scores.sort()
        worst = {y for _, y in scores[: body.sequence_risk]}
        order = [i for i, y in enumerate(years) if y in worst] + [i for i, y in enumerate(years) if y not in worst]
        hist_work = hist[order]
        years_work = years[order]
    else:
        hist_work = hist
        years_work = years
    n_hist = hist_work.shape[0]

    if body.simulation_model == "historical":
        idx = _bootstrap_idx(
            n_hist,
            years_work,
            n_sims,
            n_steps,
            body.bootstrap,
            body.block_min_years,
            max(body.block_min_years, body.block_max_years),
            body.circular,
            rng,
        )
        asset_rets = hist_work[idx]
    else:
        asset_rets = _mv_draw(n_sims, n_steps, mu_use, cov_use, df, rng)
        if body.time_series == "garch":
            # Scale each asset by a simulated GARCH path vs its sample vol
            for a in range(n_assets):
                v = _garch_vol(hist[:, a])
                last_v = float(v[-1])
                alpha, beta = 0.06, 0.91
                omega = max(float(np.var(hist[:, a])) * (1 - alpha - beta), 1e-10)
                sig = np.empty((n_sims, n_steps))
                sig[:, 0] = last_v
                z = (asset_rets[:, :, a] - mu_use[a]) / max(vol_use[a], 1e-8)
                for t in range(1, n_steps):
                    prev_r = z[:, t - 1] * np.sqrt(sig[:, t - 1])
                    sig[:, t] = omega + alpha * prev_r**2 + beta * sig[:, t - 1]
                asset_rets[:, :, a] = mu_use[a] + z * np.sqrt(np.maximum(sig, 1e-10))

    if body.tax_treatment == "aftertax":
        hair = _tax_haircut(
            body.federal_income_tax,
            body.state_income_tax,
            body.cap_gains_tax,
            body.dividend_tax,
            body.aca_tax,
        )
        pos = asset_rets > 0
        asset_rets = np.where(pos, asset_rets * (1 - hair), asset_rets)

    inf_mu = (1 + body.inflation_mean / 100.0) ** (1 / 12) - 1
    inf_sig = body.inflation_vol / 100.0 / np.sqrt(12)
    if body.inflation_model == "historical":
        # CPI proxy: TIP vs IEF is noisy; use parameterized mean with hist-like vol.
        inf_sig = max(inf_sig, 0.003)
    inflation = rng.normal(inf_mu, inf_sig, size=(n_sims, n_steps))

    w = weights.copy()
    wealth = np.zeros((n_sims, n_steps + 1), dtype=float)
    wealth[:, 0] = body.initial_amount
    holdings = wealth[:, 0:1] * w.reshape(1, -1)
    rb_every = _rebalance_every(body.rebalancing)
    cf_every = _cf_every(body.withdrawal_frequency)
    spend_hist: list[np.ndarray] = []
    base_annual = body.cashflow_amount
    if body.cashflows == "withdraw_pct":
        base_annual = body.initial_amount * (body.withdrawal_pct / 100.0)
    elif body.cashflows == "life_expectancy":
        e0 = _life_expectancy(body.current_age, body.life_expectancy_model)
        base_annual = body.initial_amount / max(e0, 1.0)

    failed = np.zeros(n_sims, dtype=bool)
    for t in range(n_steps):
        r = asset_rets[:, t, :]
        holdings = holdings * (1.0 + r)
        if rb_every and (t + 1) % rb_every == 0:
            total = holdings.sum(axis=1, keepdims=True)
            holdings = total * w.reshape(1, -1)
        total = holdings.sum(axis=1)
        flow = np.zeros(n_sims)
        if body.cashflows != "none" and (t + 1) % cf_every == 0:
            inf_factor = np.prod(1.0 + inflation[:, : t + 1], axis=1) if body.inflation_adjusted else np.ones(n_sims)
            if body.cashflows == "contribute_fixed":
                amt = (base_annual / (12 / cf_every)) * inf_factor
                flow = amt
            elif body.cashflows == "withdraw_fixed":
                flow = -(base_annual / (12 / cf_every)) * inf_factor
            elif body.cashflows == "withdraw_pct":
                flow = -total * (body.withdrawal_pct / 100.0) * (cf_every / 12.0)
            elif body.cashflows == "rolling_avg":
                target = (base_annual / (12 / cf_every)) * inf_factor
                spend_hist.append(target)
                window = spend_hist[-body.rolling_periods :]
                flow = -np.mean(np.stack(window, axis=0), axis=0)
            elif body.cashflows == "geometric":
                sm = body.smoothing_rate / 100.0
                target = total * (body.withdrawal_pct / 100.0) * (cf_every / 12.0)
                if not spend_hist:
                    prev = (base_annual / (12 / cf_every)) * inf_factor
                else:
                    prev = -spend_hist[-1]
                nxt = prev * (1.0 + inflation[:, t]) * sm + target * (1 - sm)
                flow = -nxt
                spend_hist.append(flow)
            elif body.cashflows == "life_expectancy":
                age = body.current_age + t / 12.0
                e = _life_expectancy(int(age), body.life_expectancy_model)
                flow = -total / max(e * (12 / cf_every), 1.0)
            if body.cashflows == "contribute_fixed":
                pass
            else:
                if body.cashflows != "geometric":
                    spend_hist.append(flow)
        total = total + flow
        broke = total <= 0
        failed = failed | broke
        total = np.maximum(total, 0.0)
        holdings = np.where(
            total[:, None] > 0,
            holdings * (total / np.maximum(holdings.sum(axis=1), 1e-12))[:, None],
            0.0,
        )
        if rb_every and (t + 1) % rb_every == 0:
            holdings = total[:, None] * w.reshape(1, -1)
        wealth[:, t + 1] = total

    pcts = sorted({int(p) for p in (body.percentiles or [10, 25, 50, 75, 90]) if 1 <= int(p) <= 99})
    if not pcts:
        pcts = [10, 25, 50, 75, 90]
    fan = {str(p): np.percentile(wealth, p, axis=0).tolist() for p in pcts}
    terminal = wealth[:, -1]
    years_axis = [i / 12 for i in range(n_steps + 1)]
    yearly = []
    for y in range(0, (n_steps // 12) + 1):
        col = wealth[:, min(y * 12, n_steps)]
        row = {"year": y, "mean": float(col.mean())}
        for p in pcts:
            row[f"p{p}"] = float(np.percentile(col, p))
        yearly.append(row)

    def _ann(series: np.ndarray) -> float:
        if series[-1] <= 0 or body.initial_amount <= 0:
            return float("nan")
        return float((series[-1] / body.initial_amount) ** (12 / n_steps) - 1)

    ann = np.array([_ann(wealth[i]) for i in range(n_sims)])
    dd = np.zeros(n_sims)
    for i in range(n_sims):
        peak = np.maximum.accumulate(wealth[i])
        dd[i] = float(np.min(np.where(peak > 0, wealth[i] / peak - 1.0, 0.0)))

    asset_stats = []
    for i, r in enumerate(rows):
        asset_stats.append(
            {
                "label": r["label"],
                "symbol": r["symbol"],
                "weight": round(float(r["weight"]), 4),
                "mean": round(float(((1 + mu_m[i]) ** 12 - 1) * 100), 2),
                "volatility": round(float(vol_m[i] * np.sqrt(12) * 100), 2),
            }
        )

    def _safe(n: float) -> float | None:
        if n is None or not np.isfinite(n):
            return None
        return float(n)

    return {
        "ok": True,
        "note": note,
        "disclaimer": "Hypothetical simulation from past monthly returns. Not a forecast. Not financial advice.",
        "n_sims": n_sims,
        "months": n_steps,
        "years": n_steps / 12,
        "history_months": n_hist,
        "history_start": month_labels[0] if month_labels else None,
        "history_end": month_labels[-1] if month_labels else None,
        "source": "Yahoo monthly history (Polygon fallback on 429)",
        "assets": asset_stats,
        "years_axis": years_axis,
        "percentiles": fan,
        "yearly": yearly,
        "success_rate": float((~failed).mean() * 100),
        "terminal": {
            "mean": _safe(float(terminal.mean())),
            "median": _safe(float(np.median(terminal))),
            "min": _safe(float(terminal.min())),
            "max": _safe(float(terminal.max())),
            **{f"p{p}": _safe(float(np.percentile(terminal, p))) for p in pcts},
        },
        "cagr": {
            "median": _safe(float(np.nanmedian(ann) * 100)),
            **{f"p{p}": _safe(float(np.nanpercentile(ann, p) * 100)) for p in pcts},
        },
        "max_drawdown": {
            "median": _safe(float(np.median(dd) * 100)),
            "p10": _safe(float(np.percentile(dd, 10) * 100)),
            "p90": _safe(float(np.percentile(dd, 90) * 100)),
        },
        "portfolio_sample": {
            "mean": round(float(((1 + port_hist.mean()) ** 12 - 1) * 100), 2),
            "volatility": round(float(port_hist.std(ddof=1) * np.sqrt(12) * 100), 2),
        },
    }
