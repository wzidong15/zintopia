"""Options analytics from the Yahoo chain we already fetch, plus a market-wide options strip.

Per ticker (deep panel): ATM implied volatility, IV rank / percentile from locally
recorded daily samples, expected move from the ATM straddle, max pain, and put/call
open interest by strike for one expiry.

Market-wide: VIX term structure (VIX9D / VIX / VIX3M / VIX6M), the SKEW index, and the
CBOE daily put/call ratios (published after each session, free, no key).

Free sources only. No options flow (that needs OPRA tick data). Not financial advice.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

IV_SAMPLES_MIN = 20
IV_SAMPLES_MAX = 260  # ~52 trading weeks
OI_STRIKES_EACH_SIDE = 7
EXPIRY_MIN_DAYS = 7
EXPIRY_MONTHLY_MAX_DAYS = 45

CBOE_DAILY_URL = "https://cdn.cboe.com/data/us/options/market_statistics/daily/{date}_daily_options"
CBOE_DELAYED_QUOTE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/quotes/{symbol}.json"

VIX_TERM = [
    ("VIX9D", "^VIX9D", "9d"),
    ("VIX", "^VIX", "30d"),
    ("VIX3M", "^VIX3M", "3m"),
    ("VIX6M", "^VIX6M", "6m"),
]
CBOE_RATIO_KEYS = {
    "TOTAL PUT/CALL RATIO": "total",
    "INDEX PUT/CALL RATIO": "index",
    "EQUITY PUT/CALL RATIO": "equity",
    "EXCHANGE TRADED PRODUCTS PUT/CALL RATIO": "etp",
    "SPX + SPXW PUT/CALL RATIO": "spx",
    "CBOE VOLATILITY INDEX (VIX) PUT/CALL RATIO": "vix",
}

_iv_lock = threading.Lock()


# --------------------------------------------------------------------------- helpers


def _num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _data_dir() -> Path:
    override = (
        os.environ.get("ZINTOPIA_DATA_DIR")
        or os.environ.get("FINTOPIA_DATA_DIR")
        or os.environ.get("UTOPIA_DATA_DIR")
        or ""
    ).strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".zintopia"


def _iv_path() -> Path:
    return _data_dir() / "iv_history.json"


def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    # weekday(): Monday=0 ... Friday=4
    offset = (4 - first.weekday()) % 7
    return first + timedelta(days=offset + 14)


def _is_monthly(expiry: date) -> bool:
    tf = _third_friday(expiry.year, expiry.month)
    # Yahoo sometimes lists the Thursday when Friday is a holiday.
    return abs((expiry - tf).days) <= 1


def _parse_expiry(s: str) -> date | None:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def pick_analysis_expiry(expiries: list[str], today: date | None = None) -> str | None:
    """Prefer the nearest standard monthly expiry within ~45 days that is at least a week
    out; otherwise the nearest expiry at least a week out; otherwise the first one."""
    today = today or date.today()
    parsed = [(e, d) for e in expiries if (d := _parse_expiry(e))]
    if not parsed:
        return expiries[0] if expiries else None
    far_enough = [(e, d) for e, d in parsed if (d - today).days >= EXPIRY_MIN_DAYS]
    for e, d in far_enough:
        if (d - today).days <= EXPIRY_MONTHLY_MAX_DAYS and _is_monthly(d):
            return e
    if far_enough:
        return far_enough[0][0]
    return parsed[-1][0]


def _mid(row: pd.Series) -> float | None:
    bid = _num(row.get("bid"))
    ask = _num(row.get("ask"))
    if bid is not None and ask is not None and ask > 0 and bid > 0 and ask >= bid:
        return (bid + ask) / 2
    last = _num(row.get("lastPrice"))
    return last if last is not None and last > 0 else None


def _valid_iv(v: Any) -> float | None:
    iv = _num(v)
    # Yahoo fills deep ITM / stale contracts with 1e-5; anything under 1% is junk.
    if iv is None or iv < 0.01 or iv > 5:
        return None
    return iv


# --------------------------------------------------------------------------- chain analytics


def analyze_chain(
    expiry: str,
    calls: pd.DataFrame | None,
    puts: pd.DataFrame | None,
    spot: float | None,
    today: date | None = None,
) -> dict[str, Any]:
    """Compute ATM IV, expected move, max pain, and OI by strike for a single expiry."""
    today = today or date.today()
    exp_date = _parse_expiry(expiry)
    dte = max(1, (exp_date - today).days) if exp_date else None
    out: dict[str, Any] = {
        "expiry": expiry,
        "dte": dte,
        "spot": spot,
        "atm_strike": None,
        "atm_iv": None,
        "expected_move": None,
        "expected_move_pct": None,
        "expected_low": None,
        "expected_high": None,
        "expected_move_basis": None,
        "max_pain": None,
        "call_oi": None,
        "put_oi": None,
        "oi_put_call": None,
        "oi_by_strike": [],
    }
    if calls is None or puts is None or getattr(calls, "empty", True) or getattr(puts, "empty", True):
        return out

    c = calls.copy()
    p = puts.copy()
    for df in (c, p):
        df["strike"] = pd.to_numeric(df.get("strike"), errors="coerce")
        df["openInterest"] = pd.to_numeric(df.get("openInterest"), errors="coerce").fillna(0)
    c = c.dropna(subset=["strike"]).sort_values("strike")
    p = p.dropna(subset=["strike"]).sort_values("strike")
    if c.empty or p.empty:
        return out

    call_oi_total = float(c["openInterest"].sum())
    put_oi_total = float(p["openInterest"].sum())
    out["call_oi"] = int(call_oi_total)
    out["put_oi"] = int(put_oi_total)
    out["oi_put_call"] = round(put_oi_total / call_oi_total, 2) if call_oi_total > 0 else None

    # ---- strikes present on both sides (needed for a straddle and for a clean OI ladder)
    strikes = sorted(set(c["strike"].tolist()) | set(p["strike"].tolist()))
    if not strikes:
        return out

    ref = spot
    if ref is None:
        # Fall back to the strike where call and put OI are closest to balanced.
        ref = float(strikes[len(strikes) // 2])
    atm_strike = min(strikes, key=lambda k: abs(k - ref))
    out["atm_strike"] = atm_strike

    c_by = {float(r["strike"]): r for _, r in c.iterrows()}
    p_by = {float(r["strike"]): r for _, r in p.iterrows()}

    # ---- ATM IV: mean of call/put IV at the ATM strike, else median of valid IVs near spot
    ivs: list[float] = []
    for side in (c_by, p_by):
        row = side.get(atm_strike)
        if row is not None:
            iv = _valid_iv(row.get("impliedVolatility"))
            if iv is not None:
                ivs.append(iv)
    if not ivs and ref:
        near: list[float] = []
        for side in (c_by, p_by):
            for k, row in side.items():
                if abs(k - ref) / ref <= 0.05:
                    iv = _valid_iv(row.get("impliedVolatility"))
                    if iv is not None:
                        near.append(iv)
        if near:
            near.sort()
            ivs = [near[len(near) // 2]]
    atm_iv = sum(ivs) / len(ivs) if ivs else None
    out["atm_iv"] = round(atm_iv, 4) if atm_iv is not None else None

    # ---- expected move: ATM straddle mid; fall back to spot * iv * sqrt(dte / 365)
    move: float | None = None
    basis: str | None = None
    c_row = c_by.get(atm_strike)
    p_row = p_by.get(atm_strike)
    if c_row is not None and p_row is not None:
        cm = _mid(c_row)
        pm = _mid(p_row)
        if cm is not None and pm is not None:
            move = cm + pm
            basis = "ATM straddle"
    if move is None and atm_iv is not None and spot and dte:
        move = spot * atm_iv * math.sqrt(dte / 365.0)
        basis = "IV × √t"
    if move is not None and spot:
        out["expected_move"] = round(move, 2)
        out["expected_move_pct"] = round(move / spot * 100, 2)
        out["expected_low"] = round(spot - move, 2)
        out["expected_high"] = round(spot + move, 2)
        out["expected_move_basis"] = basis

    # ---- max pain: strike minimising total intrinsic value paid to option holders
    best_k: float | None = None
    best_pain: float | None = None
    for k in strikes:
        pain = 0.0
        for ck, row in c_by.items():
            if k > ck:
                pain += float(row["openInterest"]) * (k - ck)
        for pk, row in p_by.items():
            if pk > k:
                pain += float(row["openInterest"]) * (pk - k)
        if best_pain is None or pain < best_pain:
            best_pain = pain
            best_k = k
    out["max_pain"] = best_k

    # ---- OI ladder around the ATM strike
    idx = strikes.index(atm_strike)
    lo = max(0, idx - OI_STRIKES_EACH_SIDE)
    hi = min(len(strikes), idx + OI_STRIKES_EACH_SIDE + 1)
    ladder = []
    for k in strikes[lo:hi]:
        cr = c_by.get(k)
        pr = p_by.get(k)
        ladder.append(
            {
                "strike": k,
                "call_oi": int(cr["openInterest"]) if cr is not None else 0,
                "put_oi": int(pr["openInterest"]) if pr is not None else 0,
            }
        )
    out["oi_by_strike"] = ladder
    return out


# --------------------------------------------------------------------------- IV history


def _read_iv_store() -> dict[str, Any]:
    path = _iv_path()
    try:
        if path.exists():
            data = json.loads(path.read_text("utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def _write_iv_store(store: dict[str, Any]) -> None:
    path = _iv_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(store, separators=(",", ":")), "utf-8")
        tmp.replace(path)
    except OSError:
        pass


def record_iv_and_rank(symbol: str, iv: float | None, today: date | None = None) -> dict[str, Any]:
    """Append today's ATM IV sample for the symbol (one per day) and return rank stats
    over the stored window. Yahoo has no IV history, so rank starts as 'collecting'."""
    sym = symbol.strip().upper()
    today = today or date.today()
    key = today.isoformat()
    with _iv_lock:
        store = _read_iv_store()
        rows: list[list[Any]] = list(store.get(sym) or [])
        if iv is not None:
            rows = [r for r in rows if not (isinstance(r, list) and len(r) == 2 and r[0] == key)]
            rows.append([key, round(float(iv), 4)])
            rows = rows[-IV_SAMPLES_MAX:]
            store[sym] = rows
            _write_iv_store(store)
    samples = [_num(r[1]) for r in rows if isinstance(r, list) and len(r) == 2]
    samples = [s for s in samples if s is not None]
    n = len(samples)
    out: dict[str, Any] = {
        "iv_samples": n,
        "iv_samples_needed": IV_SAMPLES_MIN,
        "iv_rank": None,
        "iv_percentile": None,
        "iv_low": None,
        "iv_high": None,
        "iv_first_sample": rows[0][0] if rows else None,
    }
    if n == 0 or iv is None:
        return out
    lo = min(samples)
    hi = max(samples)
    out["iv_low"] = round(lo, 4)
    out["iv_high"] = round(hi, 4)
    if n < IV_SAMPLES_MIN:
        return out
    if hi > lo:
        out["iv_rank"] = round((iv - lo) / (hi - lo) * 100, 1)
    else:
        out["iv_rank"] = 50.0
    below = sum(1 for s in samples if s < iv)
    out["iv_percentile"] = round(below / n * 100, 1)
    return out


# --------------------------------------------------------------------------- market strip


def _cboe_delayed_quote(http_json: Callable[..., Any], symbol: str) -> dict[str, Any] | None:
    try:
        payload = http_json("GET", CBOE_DELAYED_QUOTE_URL.format(symbol=symbol), timeout=10.0)
    except Exception:
        return None
    data = (payload or {}).get("data") or {}
    price = _num(data.get("current_price"))
    if price is None:
        return None
    return {
        "price": price,
        "change": _num(data.get("price_change")),
        "change_pct": _num(data.get("price_change_percent")),
        "source": "cboe-delayed",
    }


def vix_term_structure(
    quote_fn: Callable[[str], dict[str, Any]],
    http_json: Callable[..., Any],
) -> dict[str, Any]:
    """VIX9D / VIX / VIX3M / VIX6M and SKEW. Yahoo first, CBOE delayed quotes as fallback."""
    points: list[dict[str, Any]] = []
    for label, yahoo_sym, tenor in VIX_TERM:
        q: dict[str, Any] | None = None
        try:
            raw = quote_fn(yahoo_sym)
            if raw and _num(raw.get("price")) is not None:
                q = {
                    "price": _num(raw.get("price")),
                    "change": _num(raw.get("change")),
                    "change_pct": _num(raw.get("change_pct")),
                    "source": raw.get("source") or "yfinance",
                }
        except Exception:
            q = None
        if q is None:
            q = _cboe_delayed_quote(http_json, "_" + label)
        points.append({"symbol": label, "tenor": tenor, **(q or {"price": None, "change": None, "change_pct": None, "source": None})})

    by = {p["symbol"]: p for p in points}
    vix = _num(by.get("VIX", {}).get("price"))
    vix3m = _num(by.get("VIX3M", {}).get("price"))
    vix9d = _num(by.get("VIX9D", {}).get("price"))
    ratio = round(vix / vix3m, 3) if vix and vix3m else None
    structure: str | None = None
    if ratio is not None:
        if ratio > 1.0:
            structure = "backwardation"
        elif ratio > 0.95:
            structure = "flat"
        else:
            structure = "contango"
    near_stress = bool(vix9d and vix and vix9d > vix)

    skew = None
    try:
        raw = quote_fn("^SKEW")
        if raw and _num(raw.get("price")) is not None:
            skew = {"price": _num(raw.get("price")), "change": _num(raw.get("change")), "source": raw.get("source") or "yfinance"}
    except Exception:
        skew = None
    if skew is None:
        cq = _cboe_delayed_quote(http_json, "_SKEW")
        if cq:
            skew = {"price": cq["price"], "change": cq["change"], "source": cq["source"]}

    return {
        "points": points,
        "vix_vix3m": ratio,
        "structure": structure,
        "near_term_stress": near_stress,
        "skew": skew,
    }


def cboe_put_call(http_json: Callable[..., Any], today: date | None = None, lookback_days: int = 7) -> dict[str, Any]:
    """Most recent CBOE daily put/call ratios. The file for a session appears after the close,
    so walk back a few days until one loads."""
    today = today or date.today()
    errors: list[str] = []
    for back in range(lookback_days):
        d = today - timedelta(days=back)
        if d.weekday() >= 5:
            continue
        url = CBOE_DAILY_URL.format(date=d.isoformat())
        try:
            payload = http_json("GET", url, timeout=10.0)
        except Exception as e:
            errors.append(f"{d.isoformat()}: {str(e)[:80]}")
            continue
        ratios_raw = (payload or {}).get("ratios") or []
        ratios: dict[str, float | None] = {}
        for row in ratios_raw:
            name = str(row.get("name") or "").strip().upper()
            key = CBOE_RATIO_KEYS.get(name)
            if key:
                ratios[key] = _num(row.get("value"))
        if not ratios:
            errors.append(f"{d.isoformat()}: no ratios")
            continue
        totals = (payload or {}).get("SUM OF ALL PRODUCTS") or []
        volume: dict[str, Any] = {}
        open_interest: dict[str, Any] = {}
        for row in totals:
            name = str(row.get("name") or "").upper()
            block = {"call": _num(row.get("call")), "put": _num(row.get("put")), "total": _num(row.get("total"))}
            if name == "VOLUME":
                volume = block
            elif name == "OPEN INTEREST":
                open_interest = block
        return {
            "date": d.isoformat(),
            "ratios": ratios,
            "volume": volume,
            "open_interest": open_interest,
            "source": "cboe",
            "url": url,
        }
    return {"date": None, "ratios": {}, "volume": {}, "open_interest": {}, "source": "cboe", "error": "; ".join(errors[-3:]) or "unavailable"}


def market_options(
    quote_fn: Callable[[str], dict[str, Any]],
    http_json: Callable[..., Any],
) -> dict[str, Any]:
    term = vix_term_structure(quote_fn, http_json)
    pc = cboe_put_call(http_json)
    return {
        "vix": term,
        "put_call": pc,
        "as_of": int(time.time()),
        "note": "VIX term structure and SKEW from Yahoo (CBOE delayed fallback); put/call ratios from the CBOE daily statistics file, published after each session. Not financial advice.",
    }
