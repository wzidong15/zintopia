"""Strategy backtester on daily closes.

Pure numpy / pandas, no vectorbt. Modeled on the QuantLab conventions:

- Signals are decided at the previous close and filled at the next close.
- Stops are observed on the close and filled at the following close (not intraday
  stop orders); the realised loss can exceed the threshold.
- Commission is charged on notional; slippage lifts buys and lowers sells.
- Signal strategies (SMA cross, trend filter, RSI) run one independent cash sleeve
  per symbol: initial cash split equally, each sleeve goes all-in on entry, no
  rebalancing between sleeves.
- Rotation strategies rebalance the whole portfolio to equal target weights on the
  first trading day of each month using scores from the prior close; sells first,
  then buys sized to the cash left after costs.
- Indicators warm up on history *before* the requested start date when Yahoo has it,
  so the strategy is live from day one of the window.
- Trade statistics use closed trades only; open positions are marked to market.
- Returns and volatility use 252 trading days; Sharpe assumes a zero risk-free rate.

Everything here is in-sample research on one price path. Not financial advice.
"""

from __future__ import annotations

import math
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from itertools import product
from typing import Any, Callable, Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

TRADING_DAYS = 252
MAX_SYMBOLS = 12
MAX_RUNS = 300
MAX_STRATEGIES = 8
EQUITY_RUNS = 12
MIN_BARS = 60
HISTORY_RANGE = "10y"
ENGINE = "zintopia-numpy"
_ET = ZoneInfo("America/New_York")

SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLY", "XLP", "XLE", "XLI", "XLB", "XLU", "XLRE", "XLC"]

_history: Callable[[str, str], dict[str, Any]] | None = None
_closes_cache: dict[str, tuple[float, pd.Series, str]] = {}
_CLOSES_TTL_SEC = 60 * 60


def configure(*, history: Callable[[str, str], dict[str, Any]]) -> None:
    global _history
    _history = history


# --------------------------------------------------------------------------- strategy specs

RankKey = Literal["sharpe", "cagr", "total_return", "calmar", "sortino", "max_drawdown"]

STRATEGIES: list[dict[str, Any]] = [
    {
        "id": "buy_hold",
        "label": "Buy & hold",
        "group": "portfolio",
        "description": "Equal-weight the symbols on the first bar and hold. Costs on the initial buy only. Same as the benchmark line minus costs.",
        "params": [],
    },
    {
        "id": "sma_cross",
        "label": "SMA crossover",
        "group": "per_symbol",
        "description": "Long while the fast SMA is above the slow SMA; exit on the cross down or a close-observed stop. After a stop, wait for a fresh cross up. One cash sleeve per symbol.",
        "params": [
            {"key": "fast", "label": "Fast SMA", "type": "int_list", "default": [10, 20, 50]},
            {"key": "slow", "label": "Slow SMA", "type": "int_list", "default": [50, 100, 200]},
            {"key": "stop_loss", "label": "Stop loss %", "type": "float_list", "default": [0, 8], "help": "0 = none"},
        ],
    },
    {
        "id": "trend_sma",
        "label": "Trend filter (price vs SMA)",
        "group": "per_symbol",
        "description": "Faber-style: long while the close is above the SMA, cash otherwise. The paper fund's 200-day trend strategy is window 200.",
        "params": [
            {"key": "window", "label": "SMA window", "type": "int_list", "default": [100, 150, 200]},
            {"key": "stop_loss", "label": "Stop loss %", "type": "float_list", "default": [0], "help": "0 = none"},
        ],
    },
    {
        "id": "rsi_reversion",
        "label": "RSI mean reversion",
        "group": "per_symbol",
        "description": "Buy when Wilder RSI closes below the entry level; sell above the exit level, on a stop, or after max hold days. No trend filter.",
        "params": [
            {"key": "rsi_window", "label": "RSI window", "type": "int_list", "default": [2, 14]},
            {"key": "entry", "label": "Entry RSI <", "type": "float_list", "default": [10, 30]},
            {"key": "exit", "label": "Exit RSI >", "type": "float_list", "default": [70]},
            {"key": "max_hold", "label": "Max hold days", "type": "int_list", "default": [0, 5], "help": "0 = none"},
            {"key": "stop_loss", "label": "Stop loss %", "type": "float_list", "default": [0, 8], "help": "0 = none"},
        ],
    },
    {
        "id": "rsi_trend",
        "label": "RSI + trend filter",
        "group": "per_symbol",
        "description": "Mean-revert only in an uptrend: buy when RSI is below the entry level and the close is above the trend SMA; sell when RSI is above the exit level, the close drops under the SMA, a stop hits, or max hold days pass.",
        "params": [
            {"key": "rsi_window", "label": "RSI window", "type": "int_list", "default": [2, 14]},
            {"key": "entry", "label": "Entry RSI <", "type": "float_list", "default": [10, 30]},
            {"key": "exit", "label": "Exit RSI >", "type": "float_list", "default": [70]},
            {"key": "trend_window", "label": "Trend SMA", "type": "int_list", "default": [200]},
            {"key": "max_hold", "label": "Max hold days", "type": "int_list", "default": [0, 5], "help": "0 = none"},
            {"key": "stop_loss", "label": "Stop loss %", "type": "float_list", "default": [0, 8], "help": "0 = none"},
        ],
    },
    {
        "id": "momentum_rot",
        "label": "Momentum rotation",
        "group": "portfolio",
        "description": "On the first trading day of each month rank the symbols by trailing return at the prior close and hold the top N with positive momentum at 1/N each. Empty slots go to the defensive symbol, or cash when blank. Lookback 0 = accelerated momentum (average of 1, 3, and 6-month returns). SPY + EFA, top 1, defensive SHY is dual momentum; the 11 sector ETFs, top 3, is sector rotation.",
        "params": [
            {"key": "lookback", "label": "Lookback days", "type": "int_list", "default": [63, 126, 252], "help": "0 = 1/3/6m average"},
            {"key": "top_n", "label": "Hold top N", "type": "int_list", "default": [1, 2]},
            {"key": "defensive", "label": "Defensive symbol", "type": "symbol", "default": "", "help": "blank = cash"},
        ],
    },
]
_SPEC_BY_ID = {s["id"]: s for s in STRATEGIES}

PRESETS: list[dict[str, Any]] = [
    {
        "id": "faber_spy",
        "label": "Faber trend on SPY (SMA 100–250)",
        "symbols": ["SPY"],
        "strategy": "trend_sma",
        "params": {"window": [100, 150, 200, 250], "stop_loss": [0]},
    },
    {
        "id": "gem",
        "label": "Dual momentum: SPY / EFA, SHY fallback",
        "symbols": ["SPY", "EFA"],
        "strategy": "momentum_rot",
        "params": {"lookback": [0, 126, 252], "top_n": [1], "defensive": "SHY"},
    },
    {
        "id": "sector_rot",
        "label": "Sector rotation: top 2–4 of 11 sector ETFs",
        "symbols": list(SECTOR_ETFS),
        "strategy": "momentum_rot",
        "params": {"lookback": [126], "top_n": [2, 3, 4], "defensive": ""},
    },
    {
        "id": "rsi2_trend",
        "label": "RSI(2) pullbacks in an uptrend: SPY, QQQ",
        "symbols": ["SPY", "QQQ"],
        "strategy": "rsi_trend",
        "params": {"rsi_window": [2], "entry": [5, 10, 15], "exit": [70], "trend_window": [200], "max_hold": [5], "stop_loss": [5, 8]},
    },
    {
        "id": "sma_grid",
        "label": "SMA crossover grid: SPY, QQQ, IWM",
        "symbols": ["SPY", "QQQ", "IWM"],
        "strategy": "sma_cross",
        "params": {"fast": [10, 20, 50], "slow": [50, 100, 200], "stop_loss": [0, 8]},
    },
]

# Paper-fund strategy kinds (portfolios.py) -> backtest equivalents. "momentum" (day-gainers)
# depends on a live movers board and cannot be replayed.
PAPER_KINDS: dict[str, dict[str, Any]] = {
    "buy_hold": {"strategy": "buy_hold", "symbols": ["{symbol}"], "params": {}},
    "sma_cross": {"strategy": "sma_cross", "symbols": ["{symbol}"], "params": {"fast": [20], "slow": [50], "stop_loss": [0]}},
    "trend_200": {"strategy": "trend_sma", "symbols": ["{symbol}"], "params": {"window": [200], "stop_loss": [0]}},
    "rsi_reversion": {"strategy": "rsi_reversion", "symbols": ["{symbol}"], "params": {"rsi_window": [14], "entry": [30], "exit": [70], "max_hold": [0], "stop_loss": [0]}},
    "rsi_trend": {"strategy": "rsi_trend", "symbols": ["{symbol}"], "params": {"rsi_window": [14], "entry": [30], "exit": [70], "trend_window": [200], "max_hold": [0], "stop_loss": [0]}},
    "dual_momentum": {"strategy": "momentum_rot", "symbols": ["{symbol}", "EFA"], "params": {"lookback": [0], "top_n": [1], "defensive": "SHY"}},
    "sector_rot": {"strategy": "momentum_rot", "symbols": list(SECTOR_ETFS), "params": {"lookback": [126], "top_n": [3], "defensive": ""}},
}


# --------------------------------------------------------------------------- request models


class BtStrategy(BaseModel):
    kind: str = Field(min_length=1, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)


class BtBody(BaseModel):
    symbols: list[str] = Field(default_factory=lambda: ["SPY"], min_length=1, max_length=MAX_SYMBOLS)
    start: str = "2017-01-01"
    end: str | None = None
    strategies: list[BtStrategy] = Field(min_length=1, max_length=MAX_STRATEGIES)
    initial_cash: float = Field(default=100_000, gt=0, le=1e12)
    fees_bps: float = Field(default=5, ge=0, le=200)
    slippage_bps: float = Field(default=10, ge=0, le=200)
    rank_by: RankKey = "sharpe"
    max_runs: int = Field(default=MAX_RUNS, ge=1, le=MAX_RUNS)


# --------------------------------------------------------------------------- helpers


def _sym(raw: str) -> str:
    return str(raw or "").strip().upper().split(":")[-1]


def _parse_date(s: str | None, fallback: date) -> date:
    if not s or not str(s).strip():
        return fallback
    try:
        return datetime.strptime(str(s).strip()[:10], "%Y-%m-%d").date()
    except ValueError as e:
        raise HTTPException(422, f"Bad date {s!r}; use YYYY-MM-DD") from e


def _unix_to_date(ts: int) -> date:
    return datetime.fromtimestamp(int(ts), tz=_ET).date()


def _as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, str):
        return [p.strip() for p in v.replace(";", ",").split(",") if p.strip()]
    return [v]


def _coerce_list(values: list[Any], kind: str, key: str) -> list[float]:
    out: list[float] = []
    for v in values:
        try:
            f = float(v)
        except (TypeError, ValueError) as e:
            raise HTTPException(422, f"{key}: {v!r} is not a number") from e
        if not math.isfinite(f):
            raise HTTPException(422, f"{key}: {v!r} is not finite")
        if kind == "int_list":
            if abs(f - round(f)) > 1e-9:
                raise HTTPException(422, f"{key}: {v!r} must be a whole number")
            f = float(int(round(f)))
        out.append(f)
    # de-dupe, keep order
    seen: set[float] = set()
    uniq = [x for x in out if not (x in seen or seen.add(x))]
    return uniq


def _expand_grid(spec: dict[str, Any], params: dict[str, Any], n_symbols: int) -> list[dict[str, Any]]:
    """Cartesian product of every list-valued parameter, validated per strategy."""
    axes: list[tuple[str, list[Any]]] = []
    scalars: dict[str, Any] = {}
    for p in spec["params"]:
        key = p["key"]
        raw = params.get(key, p["default"])
        if p["type"] == "symbol":
            scalars[key] = _sym(str(raw or ""))
            continue
        vals = _coerce_list(_as_list(raw) or list(p["default"]), p["type"], key)
        if not vals:
            raise HTTPException(422, f"{p['label']} needs at least one value")
        axes.append((key, vals))
    combos: list[dict[str, Any]] = []
    for values in product(*(vals for _, vals in axes)) if axes else [()]:
        combo = dict(scalars)
        for (key, _), v in zip(axes, values):
            combo[key] = int(v) if key in ("fast", "slow", "window", "rsi_window", "trend_window", "max_hold", "lookback", "top_n") else v
        combos.append(combo)

    sid = spec["id"]
    valid: list[dict[str, Any]] = []
    for c in combos:
        for key in ("fast", "slow", "window", "rsi_window", "trend_window"):
            if key in c and not (1 <= c[key] <= 1000):
                raise HTTPException(422, f"{key} must be between 1 and 1000")
        if "stop_loss" in c and not (0 <= c["stop_loss"] < 100):
            raise HTTPException(422, "stop_loss must be a percent between 0 and 99")
        if "max_hold" in c and not (0 <= c["max_hold"] <= 500):
            raise HTTPException(422, "max_hold must be between 0 and 500")
        if "entry" in c and "exit" in c and not (0 < c["entry"] < c["exit"] < 100):
            raise HTTPException(422, "entry RSI must be above 0 and below the exit RSI, which must be below 100")
        if sid == "sma_cross" and c["fast"] >= c["slow"]:
            continue  # skip rather than fail: grids naturally include fast >= slow pairs
        if sid == "momentum_rot":
            if not (0 <= c["lookback"] <= 1260):
                raise HTTPException(422, "lookback must be between 0 and 1260 days")
            if not (1 <= c["top_n"] <= n_symbols):
                raise HTTPException(422, f"top_n must be between 1 and the number of symbols ({n_symbols})")
        valid.append(c)
    if not valid:
        raise HTTPException(422, f"{spec['label']}: no valid parameter combination (fast must be below slow)")
    return valid


def _param_label(sid: str, c: dict[str, Any]) -> str:
    stop = f" · stop {c['stop_loss']:g}%" if c.get("stop_loss") else ""
    hold = f" · hold ≤{c['max_hold']}d" if c.get("max_hold") else ""
    if sid == "buy_hold":
        return "equal weight, hold"
    if sid == "sma_cross":
        return f"SMA {c['fast']}/{c['slow']}{stop}"
    if sid == "trend_sma":
        return f"close vs SMA {c['window']}{stop}"
    if sid == "rsi_reversion":
        return f"RSI({c['rsi_window']}) <{c['entry']:g} / >{c['exit']:g}{hold}{stop}"
    if sid == "rsi_trend":
        return f"RSI({c['rsi_window']}) <{c['entry']:g} / >{c['exit']:g} · SMA {c['trend_window']}{hold}{stop}"
    if sid == "momentum_rot":
        lb = "1/3/6m" if c["lookback"] == 0 else f"{c['lookback']}d"
        d = f" · {c['defensive']} fallback" if c.get("defensive") else " · cash fallback"
        return f"{lb} momentum · top {c['top_n']}{d}"
    return ", ".join(f"{k}={v}" for k, v in c.items())


# --------------------------------------------------------------------------- indicators


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(int(n)).mean().to_numpy()


def wilder_rsi(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder RSI seeded with the arithmetic mean of the first n changes (same as portfolios._rsi)."""
    n = int(n)
    out = np.full(len(x), np.nan)
    if n < 1 or len(x) <= n:
        return out
    delta = np.diff(x)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)

    def rsi(g: float, l: float) -> float:
        total = g + l
        return 50.0 if total <= 0 else 100.0 * g / total

    avg_gain = float(gains[:n].mean())
    avg_loss = float(losses[:n].mean())
    out[n] = rsi(avg_gain, avg_loss)
    for k in range(n, len(delta)):
        avg_gain = (avg_gain * (n - 1) + gains[k]) / n
        avg_loss = (avg_loss * (n - 1) + losses[k]) / n
        out[k + 1] = rsi(avg_gain, avg_loss)
    return out


def trailing_return(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if n <= 0 or len(x) <= n:
        return out
    out[n:] = x[n:] / x[:-n] - 1.0
    return out


def accel_momentum(x: np.ndarray) -> np.ndarray:
    parts = [trailing_return(x, n) for n in (21, 63, 126)]
    stack = np.vstack(parts)
    with np.errstate(invalid="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmean(stack, axis=0)


def _cross_up(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    above = a > b
    prev = np.roll(above, 1)
    prev[0] = False
    prev_valid = np.roll(~np.isnan(a) & ~np.isnan(b), 1)
    prev_valid[0] = False
    return above & ~prev & prev_valid


# --------------------------------------------------------------------------- simulators


def simulate_sleeve(
    close: np.ndarray,
    entry: np.ndarray,
    exit_: np.ndarray,
    *,
    cash0: float,
    fees: float,
    slip: float,
    stop_loss: float = 0.0,
    max_hold: int = 0,
    entry_after_stop: np.ndarray | None = None,
) -> tuple[np.ndarray, list[dict[str, Any]], np.ndarray]:
    """One all-in long-only sleeve. Signals at i-1 fill at close[i].

    Returns equity, closed trades, and a per-bar boolean "held" array."""
    n = len(close)
    equity = np.empty(n)
    held = np.zeros(n, dtype=bool)
    cash = float(cash0)
    shares = 0.0
    entry_px = 0.0
    entry_i = -1
    need_cross = False
    trades: list[dict[str, Any]] = []
    for i in range(n):
        px = float(close[i])
        if i > 0 and px > 0:
            if shares > 0:
                prev = float(close[i - 1])
                stopped = stop_loss > 0 and prev <= entry_px * (1.0 - stop_loss)
                expired = max_hold > 0 and (i - entry_i) >= max_hold
                if stopped or expired or bool(exit_[i - 1]):
                    fill = px * (1.0 - slip)
                    proceeds = shares * fill * (1.0 - fees)
                    cost = shares * entry_px * (1.0 + fees)
                    trades.append(
                        {
                            "entry": entry_i,
                            "exit": i,
                            "return": proceeds / cost - 1.0 if cost > 0 else 0.0,
                            "pnl": proceeds - cost,
                            "reason": "stop" if stopped else ("max_hold" if expired else "signal"),
                        }
                    )
                    cash += proceeds
                    shares = 0.0
                    need_cross = bool(stopped) and entry_after_stop is not None
            else:
                sig = bool(entry_after_stop[i - 1]) if need_cross and entry_after_stop is not None else bool(entry[i - 1])
                if sig and not bool(exit_[i - 1]) and cash > 0:
                    fill = px * (1.0 + slip)
                    shares = cash / (fill * (1.0 + fees))
                    cash = 0.0
                    entry_px = fill
                    entry_i = i
                    need_cross = False
        held[i] = shares > 0
        equity[i] = cash + shares * px
    return equity, trades, held


def simulate_weights(
    closes: np.ndarray,
    schedule: dict[int, np.ndarray],
    *,
    cash0: float,
    fees: float,
    slip: float,
) -> tuple[np.ndarray, list[dict[str, Any]], float]:
    """Rebalance to target weights on scheduled bars (sells first, buys sized to remaining cash)."""
    n, m = closes.shape
    equity = np.empty(n)
    shares = np.zeros(m)
    cost_basis = np.zeros(m)  # total cost of the open position per symbol
    cash = float(cash0)
    trades: list[dict[str, Any]] = []
    exposure_sum = 0.0
    for i in range(n):
        px = closes[i]
        w = schedule.get(i)
        if w is not None:
            nav = cash + float(np.dot(shares, px))
            target = np.where(px > 0, w * nav / np.where(px > 0, px, 1.0), 0.0)
            delta = target - shares
            delta[np.abs(delta) * px < 1e-6] = 0.0
            # sells
            for j in np.where(delta < 0)[0]:
                qty = -delta[j]
                proceeds = qty * px[j] * (1.0 - slip) * (1.0 - fees)
                cash += proceeds
                if shares[j] > 0:
                    basis_part = cost_basis[j] * (qty / shares[j])
                    cost_basis[j] -= basis_part
                    if target[j] <= 1e-12:
                        trades.append(
                            {"entry": -1, "exit": i, "symbol": j, "return": proceeds / basis_part - 1.0 if basis_part > 0 else 0.0, "pnl": proceeds - basis_part, "reason": "rebalance"}
                        )
                shares[j] -= qty
                if shares[j] < 1e-12:
                    shares[j] = 0.0
                    cost_basis[j] = 0.0
            # buys
            buy_idx = np.where(delta > 0)[0]
            need = float(sum(delta[j] * px[j] * (1.0 + slip) * (1.0 + fees) for j in buy_idx))
            k = 1.0 if need <= cash or need <= 0 else max(0.0, cash / need)
            for j in buy_idx:
                qty = delta[j] * k
                cost = qty * px[j] * (1.0 + slip) * (1.0 + fees)
                if cost <= 0:
                    continue
                cash -= cost
                shares[j] += qty
                cost_basis[j] += cost
            if cash < 0 and cash > -1e-6:
                cash = 0.0
        pos_val = float(np.dot(shares, px))
        equity[i] = cash + pos_val
        exposure_sum += pos_val / equity[i] if equity[i] > 0 else 0.0
    return equity, trades, exposure_sum / n if n else 0.0


# --------------------------------------------------------------------------- metrics


def metrics(equity: np.ndarray, dates: list[date], trades: list[dict[str, Any]], exposure: float, cash0: float) -> dict[str, Any]:
    eq = np.asarray(equity, dtype=float)
    n = len(eq)
    if n < 2 or cash0 <= 0:
        return {}
    total = eq[-1] / cash0 - 1.0
    years = max((dates[-1] - dates[0]).days / 365.25, 1.0 / 365.25)
    cagr = (eq[-1] / cash0) ** (1.0 / years) - 1.0 if eq[-1] > 0 else -1.0
    rets = np.diff(eq) / eq[:-1]
    rets = rets[np.isfinite(rets)]
    std = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    vol = std * math.sqrt(TRADING_DAYS)
    sharpe = float(rets.mean() / std * math.sqrt(TRADING_DAYS)) if std > 0 else 0.0
    downside = rets[rets < 0]
    dstd = float(np.sqrt(np.mean(np.square(downside)))) if len(downside) else 0.0
    sortino = float(rets.mean() / dstd * math.sqrt(TRADING_DAYS)) if dstd > 0 else 0.0
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1.0
    max_dd = float(dd.min())
    calmar = cagr / abs(max_dd) if max_dd < 0 else None
    closed = [t for t in trades if t.get("exit", -1) >= 0]
    n_tr = len(closed)
    wins = [t for t in closed if t["pnl"] > 0]
    gross_profit = float(sum(t["pnl"] for t in wins))
    gross_loss = float(-sum(t["pnl"] for t in closed if t["pnl"] < 0))
    return {
        "total_return": round(total * 100, 2),
        "cagr": round(cagr * 100, 2),
        "volatility": round(vol * 100, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "max_drawdown": round(max_dd * 100, 2),
        "calmar": round(calmar, 2) if calmar is not None else None,
        "trades": n_tr,
        "win_rate": round(len(wins) / n_tr * 100, 1) if n_tr else None,
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "avg_trade_return": round(float(np.mean([t["return"] for t in closed])) * 100, 2) if n_tr else None,
        "best_trade": round(max(t["return"] for t in closed) * 100, 2) if n_tr else None,
        "worst_trade": round(min(t["return"] for t in closed) * 100, 2) if n_tr else None,
        "exposure": round(exposure * 100, 1),
        "final_value": round(float(eq[-1]), 2),
        "years": round(years, 2),
    }


# --------------------------------------------------------------------------- data


def _closes_for(symbol: str) -> tuple[pd.Series, str]:
    if _history is None:
        raise HTTPException(500, "History not configured")
    key = _sym(symbol)
    now = time.time()
    hit = _closes_cache.get(key)
    if hit and now - hit[0] < _CLOSES_TTL_SEC:
        return hit[1], hit[2]
    last_err: Exception | None = None
    payload: dict[str, Any] | None = None
    for rng in (HISTORY_RANGE, "5y", "2y"):
        try:
            payload = _history(key, rng)
            break
        except Exception as e:  # noqa: BLE001
            last_err = e
    if payload is None:
        raise HTTPException(502, f"History failed for {key}: {last_err}") from last_err
    rows: dict[date, float] = {}
    for b in payload.get("bars") or []:
        c = b.get("close")
        t = b.get("time")
        if c is None or not isinstance(t, (int, float)) or float(c) <= 0:
            continue
        rows[_unix_to_date(int(t))] = float(c)
    if len(rows) < MIN_BARS:
        raise HTTPException(422, f"{key}: only {len(rows)} daily bars available")
    series = pd.Series(rows).sort_index()
    interval = str(payload.get("interval") or "1d")
    if interval != "1d":
        raise HTTPException(422, f"{key}: history came back as {interval} bars, need daily")
    src = str(payload.get("source") or "unknown")
    _closes_cache[key] = (now, series, src)
    return series, src


def load_closes(symbols: list[str]) -> tuple[pd.DataFrame, dict[str, str]]:
    uniq = list(dict.fromkeys(_sym(s) for s in symbols if _sym(s)))
    sources: dict[str, str] = {}
    frames: dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(uniq)))) as pool:
        for sym, (series, src) in zip(uniq, pool.map(_closes_for, uniq)):
            frames[sym] = series
            sources[sym] = src
    df = pd.DataFrame(frames).sort_index().ffill()
    df = df.dropna(how="any")
    if df.empty:
        raise HTTPException(422, "No overlapping daily history across the symbols")
    return df, sources


# --------------------------------------------------------------------------- one candidate


def run_candidate(
    close_full: pd.DataFrame,
    start_idx: int,
    sid: str,
    combo: dict[str, Any],
    *,
    cash0: float,
    fees: float,
    slip: float,
    trade_symbols: list[str],
) -> tuple[np.ndarray, list[dict[str, Any]], float]:
    spec = _SPEC_BY_ID[sid]
    stop = float(combo.get("stop_loss", 0) or 0) / 100.0
    if spec["group"] == "per_symbol":
        n_sleeves = len(trade_symbols)
        sleeve_cash = cash0 / n_sleeves
        equity_sum: np.ndarray | None = None
        trades: list[dict[str, Any]] = []
        exposure = 0.0
        for j, sym in enumerate(trade_symbols):
            x = close_full[sym].to_numpy(dtype=float)
            entry_after_stop = None
            if sid == "sma_cross":
                fast = sma(x, combo["fast"])
                slow = sma(x, combo["slow"])
                entry = fast > slow
                exit_ = fast < slow
                entry_after_stop = _cross_up(fast, slow)
            elif sid == "trend_sma":
                s = sma(x, combo["window"])
                entry = x > s
                exit_ = x < s
                entry_after_stop = _cross_up(x, s)
            elif sid == "rsi_reversion":
                r = wilder_rsi(x, combo["rsi_window"])
                entry = r < combo["entry"]
                exit_ = r > combo["exit"]
            elif sid == "rsi_trend":
                r = wilder_rsi(x, combo["rsi_window"])
                s = sma(x, combo["trend_window"])
                entry = (r < combo["entry"]) & (x > s)
                exit_ = (r > combo["exit"]) | (x < s)
            else:
                raise HTTPException(422, f"Unknown strategy {sid}")
            entry = np.nan_to_num(entry.astype(float), nan=0.0).astype(bool)
            exit_ = np.nan_to_num(exit_.astype(float), nan=0.0).astype(bool)
            # slice so bar start_idx is index 0; keep one prior bar so the first fill can use it
            s0 = max(0, start_idx - 1)
            eq, tr, held = simulate_sleeve(
                x[s0:],
                entry[s0:],
                exit_[s0:],
                cash0=sleeve_cash,
                fees=fees,
                slip=slip,
                stop_loss=stop,
                max_hold=int(combo.get("max_hold", 0) or 0),
                entry_after_stop=entry_after_stop[s0:] if entry_after_stop is not None else None,
            )
            drop = start_idx - s0
            eq = eq[drop:]
            ex = float(held[drop:].mean()) if len(held) > drop else 0.0
            # the prior bar is only a signal source; no fill can happen on it (i == 0 in the sleeve)
            for t in tr:
                t["symbol"] = sym
                t["entry"] -= drop
                t["exit"] -= drop
            trades.extend(tr)
            exposure += ex / n_sleeves
            equity_sum = eq if equity_sum is None else equity_sum + eq
        return equity_sum, trades, exposure

    # portfolio strategies
    closes = close_full[trade_symbols].to_numpy(dtype=float)
    m = len(trade_symbols)
    if sid == "buy_hold":
        sched = {start_idx: np.full(m, 1.0 / m)}
    elif sid == "momentum_rot":
        lb = int(combo["lookback"])
        top_n = int(combo["top_n"])
        defensive = _sym(str(combo.get("defensive") or ""))
        d_idx = trade_symbols.index(defensive) if defensive and defensive in trade_symbols else None
        rank_idx = [j for j in range(m) if j != d_idx]
        if not rank_idx:
            raise HTTPException(422, "Momentum rotation needs at least one non-defensive symbol")
        top_n = min(top_n, len(rank_idx))
        scores = np.column_stack(
            [accel_momentum(closes[:, j]) if lb == 0 else trailing_return(closes[:, j], lb) for j in range(m)]
        )
        idx = close_full.index
        months = [(d.year, d.month) for d in idx]
        sched = {}
        for i in range(max(1, start_idx), len(idx)):
            if months[i] == months[i - 1] and i != start_idx:
                continue
            sc = scores[i - 1]
            if np.all(np.isnan(sc[rank_idx])):
                continue
            eligible = [j for j in rank_idx if np.isfinite(sc[j]) and sc[j] > 0]
            winners = sorted(eligible, key=lambda j: (-sc[j], trade_symbols[j]))[:top_n]
            w = np.zeros(m)
            for j in winners:
                w[j] = 1.0 / top_n
            leftover = 1.0 - len(winners) / top_n
            if d_idx is not None and leftover > 0:
                w[d_idx] = leftover
            sched[i] = w
    else:
        raise HTTPException(422, f"Unknown strategy {sid}")
    sched_local = {i - start_idx: w for i, w in sched.items() if i >= start_idx}
    eq, tr, ex = simulate_weights(closes[start_idx:], sched_local, cash0=cash0, fees=fees, slip=slip)
    for t in tr:
        t["symbol"] = trade_symbols[int(t["symbol"])]
    return eq, tr, ex


# --------------------------------------------------------------------------- routes


@router.get("/meta")
def backtest_meta() -> dict[str, Any]:
    return {
        "engine": ENGINE,
        "strategies": STRATEGIES,
        "presets": PRESETS,
        "paper_kinds": PAPER_KINDS,
        "sector_etfs": SECTOR_ETFS,
        "rank_keys": ["sharpe", "cagr", "total_return", "calmar", "sortino", "max_drawdown"],
        "limits": {"max_symbols": MAX_SYMBOLS, "max_runs": MAX_RUNS, "max_strategies": MAX_STRATEGIES, "equity_runs": EQUITY_RUNS, "history_range": HISTORY_RANGE},
        "defaults": {"start": "2017-01-01", "initial_cash": 100_000, "fees_bps": 5, "slippage_bps": 10},
    }


@router.post("")
def run_backtest(body: BtBody) -> dict[str, Any]:
    t0 = time.time()
    symbols = list(dict.fromkeys(_sym(s) for s in body.symbols if _sym(s)))
    if not symbols:
        raise HTTPException(422, "At least one symbol is required")
    if len(symbols) > MAX_SYMBOLS:
        raise HTTPException(422, f"At most {MAX_SYMBOLS} symbols")
    today = datetime.now(_ET).date()
    start = _parse_date(body.start, date(2017, 1, 1))
    end = _parse_date(body.end, today)
    if start >= end:
        raise HTTPException(422, "start must be before end")

    # expand grids first so bad params fail before any download
    plan: list[tuple[dict[str, Any], dict[str, Any], list[str]]] = []
    fetch_set: list[str] = list(symbols)
    for st in body.strategies:
        spec = _SPEC_BY_ID.get(st.kind)
        if not spec:
            raise HTTPException(422, f"Unknown strategy {st.kind!r}; valid: {', '.join(_SPEC_BY_ID)}")
        combos = _expand_grid(spec, st.params or {}, len(symbols))
        for c in combos:
            trade_syms = list(symbols)
            d = _sym(str(c.get("defensive") or "")) if spec["id"] == "momentum_rot" else ""
            if d:
                if d not in trade_syms:
                    trade_syms.append(d)
                if d not in fetch_set:
                    fetch_set.append(d)
            plan.append((spec, c, trade_syms))
    if len(plan) > body.max_runs:
        raise HTTPException(422, f"{len(plan)} parameter combinations exceed the limit of {body.max_runs}; narrow the grid")

    close_full, sources = load_closes(fetch_set)
    idx = list(close_full.index)
    data_start = idx[0]
    start_idx = next((i for i, d in enumerate(idx) if d >= start), None)
    end_idx = max((i for i, d in enumerate(idx) if d <= end), default=None)
    if start_idx is None or end_idx is None or end_idx - start_idx + 1 < MIN_BARS:
        raise HTTPException(422, f"Fewer than {MIN_BARS} daily bars between {start} and {end} (data begins {data_start})")
    close_win = close_full.iloc[: end_idx + 1]
    dates = idx[start_idx : end_idx + 1]
    dates_unix = [int(datetime(d.year, d.month, d.day, 16, 0, tzinfo=_ET).timestamp()) for d in dates]

    fees = body.fees_bps / 10_000.0
    slip = body.slippage_bps / 10_000.0
    cash0 = float(body.initial_cash)

    runs: list[dict[str, Any]] = []
    for k, (spec, combo, trade_syms) in enumerate(plan):
        try:
            eq, trades, exposure = run_candidate(
                close_win, start_idx, spec["id"], combo, cash0=cash0, fees=fees, slip=slip, trade_symbols=trade_syms
            )
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"{spec['label']} {combo}: {e}") from e
        m = metrics(eq, dates, trades, exposure, cash0)
        runs.append(
            {
                "id": f"{spec['id']}:{k}",
                "strategy": spec["id"],
                "strategy_label": spec["label"],
                "params": combo,
                "label": _param_label(spec["id"], combo),
                "symbols": trade_syms,
                **m,
                "_equity": eq,
                "_trades": trades,
            }
        )

    # benchmark: equal-weight buy & hold of the requested symbols, no costs
    bench_px = close_win[symbols].to_numpy(dtype=float)[start_idx:]
    bench_eq = cash0 * np.mean(bench_px / bench_px[0], axis=1)
    bench_m = metrics(bench_eq, dates, [], 1.0, cash0)

    key = body.rank_by
    reverse = key != "max_drawdown"

    def rank_val(r: dict[str, Any]) -> float:
        v = r.get(key)
        if v is None:
            return -1e18 if reverse else 1e18
        return float(v)

    runs.sort(key=rank_val, reverse=reverse)
    for r in runs:
        r["excess_cagr"] = round(float(r.get("cagr", 0) or 0) - float(bench_m.get("cagr", 0) or 0), 2)
    out_runs: list[dict[str, Any]] = []
    for pos, r in enumerate(runs):
        item = {k: v for k, v in r.items() if not k.startswith("_")}
        item["rank"] = pos + 1
        if pos < EQUITY_RUNS:
            item["equity"] = [round(float(v), 2) for v in r["_equity"]]
            item["recent_trades"] = [
                {
                    "symbol": t.get("symbol"),
                    "entry": dates[t["entry"]].isoformat() if 0 <= t.get("entry", -1) < len(dates) else None,
                    "exit": dates[t["exit"]].isoformat() if 0 <= t.get("exit", -1) < len(dates) else None,
                    "return": round(t["return"] * 100, 2),
                    "reason": t.get("reason"),
                }
                for t in r["_trades"][-12:]
            ]
        out_runs.append(item)

    best = out_runs[0] if out_runs else None
    warnings = [
        "In-sample parameter ranking on one price path. No walk-forward or out-of-sample test; the best cell is the most over-fit cell.",
    ]
    if start_idx == 0 and data_start > start:
        warnings.append(f"History starts {data_start}, later than the requested {start}; indicators warmed up inside the window.")
    if best and (best.get("trades") or 0) < 30:
        warnings.append("Best run closed fewer than 30 trades; win rate and profit factor are thin samples.")
    if any(s["group"] == "portfolio" and s["id"] == "momentum_rot" for s, _, _ in plan):
        warnings.append("Rotation trade counts are full exits of a symbol at a rebalance; partial trims are not counted.")
    if any(v == "polygon" for v in sources.values()):
        warnings.append("Some history came from Polygon (Yahoo was rate limited); free plans return about two years, which shortens the window.")

    return {
        "engine": ENGINE,
        "symbols": symbols,
        "fetched": fetch_set,
        "sources": sources,
        "start": dates[0].isoformat(),
        "end": dates[-1].isoformat(),
        "requested_start": start.isoformat(),
        "data_start": data_start.isoformat(),
        "bars": len(dates),
        "dates": dates_unix,
        "initial_cash": cash0,
        "fees_bps": body.fees_bps,
        "slippage_bps": body.slippage_bps,
        "rank_by": key,
        "combination_count": len(out_runs),
        "equity_runs": min(EQUITY_RUNS, len(out_runs)),
        "best_id": best["id"] if best else None,
        "runs": out_runs,
        "benchmark": {"label": "Equal-weight buy & hold, no costs", "equity": [round(float(v), 2) for v in bench_eq], **bench_m},
        "warnings": warnings,
        "assumptions": {
            "execution": "signal at previous close, fill at next close",
            "stops": "observed on the close, filled at the following close; loss can exceed the threshold",
            "sleeves": "per-symbol strategies split cash equally and go all-in per sleeve; no cross-sleeve rebalancing",
            "rotation": "monthly, first trading day, scores from the prior close; sells first, buys sized to remaining cash after costs",
            "warmup": "indicators use history before the start date when available",
            "benchmark": "equal-weight buy & hold of the requested symbols from the start date, no costs",
            "trade_stats": "closed trades only; open positions marked to market",
            "returns": "252 trading days per year; Sharpe and Sortino assume a zero risk-free rate",
            "dividends": "Yahoo auto-adjusted closes (dividends and splits folded into price)",
        },
        "elapsed_ms": int((time.time() - t0) * 1000),
        "note": "Hypothetical, in-sample research. Not financial advice.",
    }
