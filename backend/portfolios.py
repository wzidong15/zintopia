"""Stock portfolios: paper funds that buy/sell shares (no options), run simple strategies, track NAV."""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from broker_import import MAX_BYTES as IMPORT_MAX_BYTES
from broker_import import parse_broker_csv

DATA_DIR = Path(__file__).resolve().parent / "data"
LEGACY_DATA_FILE = DATA_DIR / "portfolios.json"
SECTOR_ETFS = (
    "XLK",
    "XLF",
    "XLE",
    "XLV",
    "XLI",
    "XLY",
    "XLP",
    "XLU",
    "XLB",
    "XLRE",
    "XLC",
)
DUAL_DEFENSIVE = "SHY"
DUAL_INTL = "EFA"
MAX_PORTFOLIOS = 20
MAX_TRADES = 250
MAX_SNAPSHOTS = 600
_stop = threading.Event()
_scheduler: threading.Thread | None = None

Kind = Literal[
    "manual",
    "buy_hold",
    "sma_cross",
    "momentum",
    "rsi_reversion",
    "trend_200",
    "dual_momentum",
    "sector_rot",
    "rsi_trend",
]

router = APIRouter(prefix="/api/portfolios", tags=["portfolios"])
_lock = threading.Lock()

_quote: Callable[[str], dict[str, Any]] | None = None
_quotes: Callable[[list[str]], list[dict[str, Any]]] | None = None
_history: Callable[[str, str], dict[str, Any]] | None = None
_movers: Callable[[str, int], list[dict[str, Any]]] | None = None
_session: Callable[[], str] | None = None
_extended_marks: Callable[[list[str]], dict[str, float]] | None = None


def configure(
    *,
    quote: Callable[[str], dict[str, Any]],
    quotes: Callable[[list[str]], list[dict[str, Any]]],
    history: Callable[[str, str], dict[str, Any]],
    movers: Callable[[str, int], list[dict[str, Any]]],
    session: Callable[[], str] | None = None,
    extended_marks: Callable[[list[str]], dict[str, float]] | None = None,
) -> None:
    global _quote, _quotes, _history, _movers, _session, _extended_marks
    _quote = quote
    _quotes = quotes
    _history = history
    _movers = movers
    _session = session
    _extended_marks = extended_marks
    start_scheduler()


def _strategy_interval_sec() -> float:
    raw = (
        os.environ.get("ZINTOPIA_STRATEGY_INTERVAL_SEC")
        or os.environ.get("FINTOPIA_STRATEGY_INTERVAL_SEC")
        or os.environ.get("UTOPIA_STRATEGY_INTERVAL_SEC")
        or "3600"
    ).strip()
    try:
        sec = float(raw)
    except ValueError:
        sec = 3600.0
    return max(60.0, min(sec, 24 * 3600.0))


def start_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.is_alive():
        return
    _stop.clear()
    _scheduler = threading.Thread(target=_scheduler_loop, name="zintopia-auto-strategy", daemon=True)
    _scheduler.start()


def stop_scheduler() -> None:
    _stop.set()


def _scheduler_loop() -> None:
    # Let the API finish booting / outbound network settle, then honor the hourly cadence.
    if _stop.wait(15):
        return
    while not _stop.is_set():
        try:
            run_due_auto_strategies()
        except Exception:
            pass
        if _stop.wait(30):
            return


class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    amount: float = Field(gt=0, le=1e12)


class OrderBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    side: Literal["buy", "sell"]
    shares: float | None = Field(default=None, gt=0)
    notional: float | None = Field(default=None, gt=0)


class StrategyBody(BaseModel):
    kind: Kind
    auto: bool = False
    symbol: str = "SPY"


def _data_dir() -> Path:
    override = (
        os.environ.get("ZINTOPIA_DATA_DIR")
        or os.environ.get("FINTOPIA_DATA_DIR")
        or os.environ.get("UTOPIA_DATA_DIR")
        or ""
    ).strip()
    if override:
        return Path(override).expanduser().resolve()
    new = Path.home() / ".zintopia"
    old = Path.home() / ".fintopia"
    if not new.exists() and old.exists():
        try:
            old.rename(new)
        except OSError:
            return old
    return new


def _data_file() -> Path:
    return _data_dir() / "portfolios.json"


def _empty_store() -> dict[str, Any]:
    return {"portfolios": []}


def _migrate_legacy_if_needed(dest: Path) -> None:
    if dest.is_file() or not LEGACY_DATA_FILE.is_file():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(LEGACY_DATA_FILE.read_text())


def _load() -> dict[str, Any]:
    path = _data_file()
    _migrate_legacy_if_needed(path)
    if not path.is_file():
        return _empty_store()
    try:
        return json.loads(path.read_text())
    except Exception:
        return _empty_store()


def _save(store: dict[str, Any]) -> None:
    path = _data_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2, default=str))
    tmp.replace(path)


def _now() -> int:
    return int(time.time())


def _money(n: float) -> float:
    return round(float(n), 2)


def _shares(n: float) -> float:
    return round(float(n), 6)


def _find(store: dict[str, Any], pid: str) -> dict[str, Any]:
    for p in store.get("portfolios") or []:
        if p.get("id") == pid:
            return p
    raise HTTPException(404, "Portfolio not found")


def _mark_session() -> str:
    if _session is None:
        return "rth"
    try:
        return _session()
    except Exception:
        return "rth"


def _price_map(symbols: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    uniq = [s.upper() for s in dict.fromkeys(symbols) if s]
    if not uniq:
        return out
    sess = _mark_session()
    if sess != "rth" and _extended_marks is not None:
        try:
            for sym, px in (_extended_marks(uniq) or {}).items():
                key = str(sym).upper()
                if key and isinstance(px, (int, float)) and px > 0:
                    out[key] = float(px)
        except Exception:
            pass
    missing = [s for s in uniq if s not in out]
    if not missing:
        return out
    if _quotes is not None:
        try:
            rows = _quotes(missing)
        except Exception:
            rows = []
        for row in rows:
            sym = str(row.get("symbol") or "").upper()
            px = row.get("price")
            if sym and isinstance(px, (int, float)) and px > 0:
                out[sym] = float(px)
    still = [s for s in uniq if s not in out]
    if still and _quote is not None:
        for s in still:
            try:
                q = _quote(s)
                px = q.get("price")
                if isinstance(px, (int, float)) and px > 0:
                    out[s] = float(px)
            except Exception:
                continue
    return out


def _nav(p: dict[str, Any], prices: dict[str, float]) -> float:
    total = float(p.get("cash") or 0)
    for sym, h in (p.get("holdings") or {}).items():
        shares = float(h.get("shares") or 0)
        px = prices.get(sym) or float(h.get("last_price") or 0)
        total += shares * px
        if px:
            h["last_price"] = _money(px)
            h["market_value"] = _money(shares * px)
    return _money(total)


def _snapshot(p: dict[str, Any], nav: float, *, force: bool = False) -> None:
    snaps = p.setdefault("snapshots", [])
    now = _now()
    if not force and snaps:
        last = snaps[-1]
        if now - int(last.get("t") or 0) < 20:
            last["t"] = now
            last["nav"] = nav
            last["cash"] = _money(p.get("cash") or 0)
            return
    snaps.append({"t": now, "nav": nav, "cash": _money(p.get("cash") or 0)})
    if len(snaps) > MAX_SNAPSHOTS:
        p["snapshots"] = snaps[-MAX_SNAPSHOTS:]


def _record_trade(p: dict[str, Any], **trade: Any) -> None:
    trades = p.setdefault("trades", [])
    trades.append({"t": _now(), **trade})
    if len(trades) > MAX_TRADES:
        p["trades"] = trades[-MAX_TRADES:]


def _fill_order(p: dict[str, Any], symbol: str, side: str, shares: float, price: float, source: str) -> None:
    symbol = symbol.upper()
    holdings = p.setdefault("holdings", {})
    cash = float(p.get("cash") or 0)
    notional = shares * price
    if side == "buy":
        if notional > cash + 0.01:
            raise HTTPException(400, f"Insufficient cash (${cash:.2f}) for ${notional:.2f} buy")
        h = holdings.get(symbol) or {"shares": 0.0, "avg_cost": 0.0}
        prev_sh = float(h["shares"])
        prev_cost = float(h["avg_cost"])
        new_sh = prev_sh + shares
        h["avg_cost"] = _money((prev_sh * prev_cost + notional) / new_sh) if new_sh else 0.0
        h["shares"] = _shares(new_sh)
        h["last_price"] = _money(price)
        holdings[symbol] = h
        p["cash"] = _money(cash - notional)
    else:
        h = holdings.get(symbol)
        if not h or float(h.get("shares") or 0) + 1e-9 < shares:
            have = float((h or {}).get("shares") or 0)
            raise HTTPException(400, f"Insufficient shares of {symbol} ({have})")
        h["shares"] = _shares(float(h["shares"]) - shares)
        if h["shares"] <= 1e-8:
            holdings.pop(symbol, None)
        else:
            holdings[symbol] = h
        p["cash"] = _money(cash + notional)
    p["updated_at"] = _now()
    _record_trade(
        p,
        symbol=symbol,
        side=side,
        shares=_shares(shares),
        price=_money(price),
        notional=_money(notional),
        source=source,
    )


def _sma(closes: list[float], n: int) -> float | None:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


def _rsi(closes: list[float], n: int = 14) -> float | None:
    """Wilder RSI. Needs n+1 closes."""
    if len(closes) < n + 1:
        return None
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = sum(gains[:n]) / n
    avg_loss = sum(losses[:n]) / n
    for g, loss in zip(gains[n:], losses[n:]):
        avg_gain = (avg_gain * (n - 1) + g) / n
        avg_loss = (avg_loss * (n - 1) + loss) / n
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


_closes_cache: dict[tuple[str, str], tuple[float, list[float]]] = {}
_CLOSES_TTL_SEC = 15 * 60


def _daily_closes(symbol: str, rng: str = "6mo") -> list[float]:
    key = (str(symbol).upper(), rng)
    now = time.time()
    hit = _closes_cache.get(key)
    if hit and now - hit[0] < _CLOSES_TTL_SEC:
        return list(hit[1])
    if _history is None:
        return []
    try:
        hist = _history(symbol, rng)
    except Exception:
        return []
    closes = [float(b["close"]) for b in (hist.get("bars") or []) if b.get("close") is not None]
    if closes:
        _closes_cache[key] = (now, closes)
    return closes


def _roc(closes: list[float], n: int) -> float | None:
    if len(closes) <= n:
        return None
    base = closes[-1 - n]
    if not base:
        return None
    return closes[-1] / base - 1.0


def _accel_mom(closes: list[float]) -> float | None:
    """Average of 1m / 3m / 6m returns (accelerated dual momentum)."""
    parts = [x for x in (_roc(closes, 21), _roc(closes, 63), _roc(closes, 126)) if x is not None]
    if not parts:
        return None
    return sum(parts) / len(parts)


def _long_symbols(p: dict[str, Any]) -> list[str]:
    return [
        str(s).upper()
        for s, h in (p.get("holdings") or {}).items()
        if float((h or {}).get("shares") or 0) > 1e-8
    ]


def _ensure_px(symbol: str, prices: dict[str, float]) -> float | None:
    px = prices.get(symbol)
    if isinstance(px, (int, float)) and px > 0:
        return float(px)
    if _quote is not None:
        try:
            q = _quote(symbol) or {}
            cand = q.get("price")
            if isinstance(cand, (int, float)) and cand > 0:
                prices[symbol] = float(cand)
                return float(cand)
        except Exception:
            pass
    closes = _daily_closes(symbol, "3mo")
    if closes:
        prices[symbol] = closes[-1]
        return closes[-1]
    return None


def _rotate_to(p: dict[str, Any], prices: dict[str, float], picks: list[str], source: str) -> str:
    """Hold exactly `picks` (equal cash into names not already held). Skip if the set matches."""
    picks = [s.upper() for s in dict.fromkeys(picks) if s]
    held = _long_symbols(p)
    if set(held) == set(picks):
        return "holding " + ", ".join(picks) if picks else "cash"
    sold: list[str] = []
    for sym in held:
        if sym in picks:
            continue
        h = (p.get("holdings") or {}).get(sym) or {}
        sh = float(h.get("shares") or 0)
        px = _ensure_px(sym, prices) or float(h.get("last_price") or 0)
        if px > 0 and sh > 0:
            _fill_order(p, sym, "sell", sh, px, source)
            sold.append(sym)
    if not picks:
        return "sold to cash" + (f" ({', '.join(sold)})" if sold else "")
    missing = [s for s in picks if s not in _long_symbols(p)]
    if not missing:
        return "holding " + ", ".join(picks)
    cash = float(p.get("cash") or 0)
    if cash < 10:
        return f"want {', '.join(picks)}; little cash"
    slice_amt = cash / len(missing)
    bought: list[str] = []
    for sym in missing:
        px = _ensure_px(sym, prices)
        if not px:
            continue
        shares = _shares((slice_amt * 0.99) / px)
        if shares <= 0:
            continue
        _fill_order(p, sym, "buy", shares, px, source)
        bought.append(sym)
    bits: list[str] = []
    if sold:
        bits.append("sold " + ", ".join(sold))
    if bought:
        bits.append("bought " + ", ".join(bought))
    return ("; ".join(bits) if bits else "no trades") + " → " + ", ".join(picks)


def _run_strategy(p: dict[str, Any], prices: dict[str, float], *, force: bool = False) -> str:
    st = p.get("strategy") or {}
    kind = st.get("kind") or "manual"
    if kind == "manual":
        return "manual"
    now = _now()
    last = int(st.get("last_run_at") or 0)
    if not force and last and now - last < _strategy_interval_sec():
        return "cooldown"
    note = "no action"
    try:
        if kind == "buy_hold":
            note = _strat_buy_hold(p, prices, str(st.get("symbol") or "SPY").upper())
        elif kind == "sma_cross":
            note = _strat_sma_cross(p, prices, str(st.get("symbol") or "SPY").upper())
        elif kind == "momentum":
            note = _strat_momentum(p, prices)
        elif kind == "rsi_reversion":
            note = _strat_rsi(p, prices, str(st.get("symbol") or "SPY").upper())
        elif kind == "trend_200":
            note = _strat_trend_200(p, prices, str(st.get("symbol") or "SPY").upper())
        elif kind == "dual_momentum":
            note = _strat_dual_momentum(p, prices, str(st.get("symbol") or "SPY").upper())
        elif kind == "sector_rot":
            note = _strat_sector_rot(p, prices)
        elif kind == "rsi_trend":
            note = _strat_rsi_trend(p, prices, str(st.get("symbol") or "SPY").upper())
    except HTTPException as e:
        note = str(e.detail)
        p["last_error"] = note
    except Exception as e:
        note = str(e)
        p["last_error"] = note
    else:
        p["last_error"] = None
    st["last_run_at"] = now
    st["note"] = note
    p["strategy"] = st
    return note


def _strat_buy_hold(p: dict[str, Any], prices: dict[str, float], symbol: str) -> str:
    cash = float(p.get("cash") or 0)
    held = (p.get("holdings") or {}).get(symbol)
    if held and float(held.get("shares") or 0) > 0:
        return f"holding {symbol}"
    px = prices.get(symbol)
    if not px and _quote is not None:
        q = _quote(symbol)
        px = float(q["price"]) if q.get("price") else None
        if px:
            prices[symbol] = px
    if not px:
        return f"no price for {symbol}"
    if cash < px:
        return "cash too small to buy 1 share"
    shares = _shares((cash * 0.99) / px)
    if shares <= 0:
        return "no shares"
    _fill_order(p, symbol, "buy", shares, px, "buy_hold")
    return f"bought {shares} {symbol}"


def _strat_sma_cross(p: dict[str, Any], prices: dict[str, float], symbol: str) -> str:
    if _history is None:
        return "history unavailable"
    hist = _history(symbol, "6mo")
    closes = [float(b["close"]) for b in (hist.get("bars") or []) if b.get("close") is not None]
    fast = _sma(closes, 20)
    slow = _sma(closes, 50)
    if fast is None or slow is None:
        return "not enough bars for SMA"
    px = prices.get(symbol)
    if not px:
        px = closes[-1]
        prices[symbol] = px
    held = float(((p.get("holdings") or {}).get(symbol) or {}).get("shares") or 0)
    if fast > slow and held <= 0:
        cash = float(p.get("cash") or 0)
        shares = _shares((cash * 0.99) / px) if px else 0
        if shares <= 0:
            return "SMA golden cross, no cash"
        _fill_order(p, symbol, "buy", shares, px, "sma_cross")
        return f"golden cross: bought {shares} {symbol}"
    if fast < slow and held > 0:
        _fill_order(p, symbol, "sell", held, px, "sma_cross")
        return f"death cross: sold {held} {symbol}"
    return f"SMA20={fast:.2f} SMA50={slow:.2f}, hold"


def _strat_momentum(p: dict[str, Any], prices: dict[str, float]) -> str:
    if _movers is None:
        return "movers unavailable"
    gainers = _movers("gainers", 8)
    picks: list[str] = []
    for g in gainers:
        sym = str(g.get("symbol") or "").upper()
        px = g.get("price")
        if not sym or not isinstance(px, (int, float)) or px < 5:
            continue
        prices[sym] = float(px)
        picks.append(sym)
        if len(picks) >= 3:
            break
    if not picks:
        return "no gainers"
    holdings = dict(p.get("holdings") or {})
    for sym, h in holdings.items():
        if sym not in picks and float(h.get("shares") or 0) > 0:
            px = prices.get(sym) or float(h.get("last_price") or 0)
            if px:
                _fill_order(p, sym, "sell", float(h["shares"]), px, "momentum")
    cash = float(p.get("cash") or 0)
    if cash < 10:
        return f"rotated into {', '.join(picks)}; little cash left"
    slice_amt = cash / len(picks)
    bought = []
    for sym in picks:
        px = prices.get(sym)
        if not px:
            continue
        shares = _shares((slice_amt * 0.99) / px)
        if shares <= 0:
            continue
        _fill_order(p, sym, "buy", shares, px, "momentum")
        bought.append(sym)
    return "momentum: " + (", ".join(bought) if bought else "no buys")


def _strat_rsi(p: dict[str, Any], prices: dict[str, float], symbol: str) -> str:
    q: dict[str, Any] = {}
    if _quote is not None:
        try:
            q = _quote(symbol) or {}
        except Exception:
            q = {}
    px = prices.get(symbol)
    cand = q.get("price")
    if isinstance(cand, (int, float)) and cand > 0:
        px = float(cand)
        prices[symbol] = px
    rsi = q.get("rsi")
    if not isinstance(rsi, (int, float)):
        closes = _daily_closes(symbol)
        rsi = _rsi(closes)
        if (px is None or px <= 0) and closes:
            px = closes[-1]
            prices[symbol] = px
    if not isinstance(px, (int, float)) or px <= 0:
        return f"no price for {symbol}"
    if not isinstance(rsi, (int, float)):
        return "not enough bars for RSI"
    held = float(((p.get("holdings") or {}).get(symbol) or {}).get("shares") or 0)
    if rsi < 30 and held <= 0:
        cash = float(p.get("cash") or 0)
        shares = _shares((cash * 0.25) / float(px))
        if shares <= 0:
            return f"RSI {rsi:.1f} oversold, no cash"
        _fill_order(p, symbol, "buy", shares, float(px), "rsi_reversion")
        return f"RSI {rsi:.1f}: bought {shares} {symbol}"
    if rsi > 70 and held > 0:
        _fill_order(p, symbol, "sell", held, float(px), "rsi_reversion")
        return f"RSI {rsi:.1f}: sold {held} {symbol}"
    return f"RSI {rsi:.1f}, hold"


def _strat_trend_200(p: dict[str, Any], prices: dict[str, float], symbol: str) -> str:
    """Faber-style: long the ticker when price > SMA200, else cash."""
    closes = _daily_closes(symbol, "2y")
    sma = _sma(closes, 200)
    if sma is None:
        return "not enough bars for SMA200"
    px = _ensure_px(symbol, prices) or closes[-1]
    prices[symbol] = px
    if px >= sma:
        return _rotate_to(p, prices, [symbol], "trend_200") + f" (px {px:.2f} ≥ SMA200 {sma:.2f})"
    return _rotate_to(p, prices, [], "trend_200") + f" (px {px:.2f} < SMA200 {sma:.2f})"


def _strat_dual_momentum(p: dict[str, Any], prices: dict[str, float], risk_on: str) -> str:
    """Antonacci / accelerated dual momentum: best of risk-on vs EFA if score beats SHY and 0."""
    risk_on = risk_on or "SPY"
    scores: dict[str, float] = {}
    for s in dict.fromkeys([risk_on, DUAL_INTL, DUAL_DEFENSIVE]):
        sc = _accel_mom(_daily_closes(s, "1y"))
        if sc is None:
            continue
        scores[s] = sc
        _ensure_px(s, prices)
    if not scores:
        return "not enough history for dual momentum"
    equities = [(s, scores[s]) for s in (risk_on, DUAL_INTL) if s in scores]
    equities.sort(key=lambda x: -x[1])
    shy = scores.get(DUAL_DEFENSIVE)
    pick: str | None = None
    if equities and equities[0][1] > 0 and (shy is None or equities[0][1] > shy):
        pick = equities[0][0]
    elif DUAL_DEFENSIVE in scores:
        pick = DUAL_DEFENSIVE
    if pick is None:
        return "not enough history for dual momentum"
    body = _rotate_to(p, prices, [pick], "dual_momentum")
    detail = ", ".join(f"{s} {v * 100:.1f}%" for s, v in scores.items())
    return f"{body} (1/3/6m {detail})" if detail else body


def _strat_sector_rot(p: dict[str, Any], prices: dict[str, float]) -> str:
    """Hold the top 3 US sector ETFs by 6-month return if that return is positive."""
    ranked: list[tuple[str, float]] = []
    for s in SECTOR_ETFS:
        r = _roc(_daily_closes(s, "1y"), 126)
        if r is None:
            continue
        _ensure_px(s, prices)
        ranked.append((s, r))
    ranked.sort(key=lambda x: -x[1])
    if not ranked:
        return "not enough history for sector rotation"
    picks = [s for s, r in ranked if r > 0][:3]
    if not picks:
        body = _rotate_to(p, prices, [], "sector_rot")
        return f"{body} (no sector with +6m return)"
    body = _rotate_to(p, prices, picks, "sector_rot")
    top = ", ".join(f"{s} {r * 100:.1f}%" for s, r in ranked[:3])
    return f"{body} | 6m {top}"


def _strat_rsi_trend(p: dict[str, Any], prices: dict[str, float], symbol: str) -> str:
    """Mean-revert only in an uptrend: buy RSI<30 if px>SMA200; sell if RSI>70 or trend fails."""
    closes = _daily_closes(symbol, "2y")
    rsi = _rsi(closes)
    sma = _sma(closes, 200)
    px = _ensure_px(symbol, prices)
    if px is None and closes:
        px = closes[-1]
        prices[symbol] = px
    if rsi is None or sma is None or not isinstance(px, (int, float)) or px <= 0:
        return "not enough bars for RSI+trend"
    prices[symbol] = px
    held = float(((p.get("holdings") or {}).get(symbol) or {}).get("shares") or 0)
    uptrend = px >= sma
    if rsi < 30 and uptrend and held <= 0:
        cash = float(p.get("cash") or 0)
        shares = _shares((cash * 0.25) / float(px))
        if shares <= 0:
            return f"RSI {rsi:.1f} oversold in uptrend, no cash"
        _fill_order(p, symbol, "buy", shares, float(px), "rsi_trend")
        return f"RSI {rsi:.1f} + uptrend: bought {shares} {symbol}"
    if held > 0 and (rsi > 70 or not uptrend):
        _fill_order(p, symbol, "sell", held, float(px), "rsi_trend")
        why = "overbought" if rsi > 70 else "lost SMA200"
        return f"RSI {rsi:.1f} {why}: sold {held} {symbol}"
    side = "uptrend" if uptrend else "downtrend"
    return f"RSI {rsi:.1f} {side} SMA200 {sma:.2f}, hold"


def _symbols(p: dict[str, Any]) -> list[str]:
    symbols = list((p.get("holdings") or {}).keys())
    st = p.get("strategy") or {}
    kind = st.get("kind")
    st_sym = str(st.get("symbol") or "").upper()
    if st_sym:
        symbols.append(st_sym)
    if kind == "sector_rot":
        symbols.extend(SECTOR_ETFS)
    elif kind == "dual_momentum":
        symbols.extend([st_sym or "SPY", DUAL_INTL, DUAL_DEFENSIVE])
    return symbols


def _holding_prices(p: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for sym, h in (p.get("holdings") or {}).items():
        px = h.get("last_price")
        if isinstance(px, (int, float)) and px > 0:
            out[str(sym).upper()] = float(px)
    return out


def _origin(p: dict[str, Any]) -> str:
    raw = str(p.get("origin") or "").strip().lower()
    if raw in ("import", "paper"):
        return raw
    for t in p.get("trades") or []:
        if str(t.get("source") or "") == "broker-csv":
            return "import"
    return "paper"


def _cost_basis_mode(p: dict[str, Any]) -> str | None:
    raw = str(p.get("cost_basis") or "").strip().lower()
    if raw in ("mark", "csv"):
        return raw
    if _origin(p) == "import":
        return "csv"
    return None


def _enrich(p: dict[str, Any], prices: dict[str, float] | None = None) -> dict[str, Any]:
    if prices is None:
        prices = {**_holding_prices(p), **_price_map(_symbols(p))}
    nav = _nav(p, prices)
    initial = float(p.get("initial_cash") or 0) or 1.0
    pnl = _money(nav - initial)
    ret = (nav - initial) / initial * 100
    snaps = p.get("snapshots") or []
    peak = initial
    max_dd = 0.0
    for s in snaps:
        n = float(s.get("nav") or 0)
        peak = max(peak, n)
        if peak > 0:
            max_dd = min(max_dd, (n - peak) / peak)
    holdings_out = []
    for sym, h in (p.get("holdings") or {}).items():
        shares = float(h.get("shares") or 0)
        last = prices.get(sym) or float(h.get("last_price") or 0)
        avg = float(h.get("avg_cost") or 0)
        mv = shares * last
        holdings_out.append(
            {
                "symbol": sym,
                "shares": shares,
                "avg_cost": avg,
                "last_price": _money(last) if last else None,
                "market_value": _money(mv),
                "unrealized_pnl": _money(mv - shares * avg) if last else None,
            }
        )
    holdings_out.sort(key=lambda x: -(x.get("market_value") or 0))
    st = dict(p.get("strategy") or {})
    last = int(st.get("last_run_at") or 0)
    interval = int(_strategy_interval_sec())
    st["interval_sec"] = interval
    if st.get("auto") and st.get("kind") not in (None, "manual"):
        st["next_run_at"] = (last + interval) if last else _now()
    return {
        **{k: v for k, v in p.items() if k not in ("holdings", "strategy")},
        "origin": _origin(p),
        "cost_basis": _cost_basis_mode(p),
        "strategy": st,
        "holdings": holdings_out,
        "nav": nav,
        "pnl": pnl,
        "return_pct": round(ret, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "prices": prices,
        "mark_session": _mark_session(),
    }


def _summary(p: dict[str, Any], prices: dict[str, float] | None = None) -> dict[str, Any]:
    e = _enrich(p, prices)
    return {
        "id": e["id"],
        "name": e["name"],
        "initial_cash": e["initial_cash"],
        "cash": e["cash"],
        "nav": e["nav"],
        "pnl": e["pnl"],
        "return_pct": e["return_pct"],
        "strategy": e.get("strategy"),
        "updated_at": e.get("updated_at"),
        "created_at": e.get("created_at"),
        "holdings_count": len(e.get("holdings") or []),
        "last_error": e.get("last_error"),
        "origin": e.get("origin") or "paper",
        "cost_basis": e.get("cost_basis"),
    }


@router.get("")
def list_portfolios(live: bool = False):
    with _lock:
        copies = [copy.deepcopy(p) for p in (_load().get("portfolios") or [])]
    priced: list[tuple[dict[str, Any], dict[str, float], dict[str, Any]]] = []
    for p in copies:
        prices = _price_map(_symbols(p)) if live else _holding_prices(p)
        e = _summary(p, prices)
        priced.append((p, prices, e))
    if live:
        with _lock:
            store = _load()
            for _copy, prices, e in priced:
                try:
                    cur = _find(store, e["id"])
                except HTTPException:
                    continue
                _snapshot(cur, e["nav"])
                holdings = cur.get("holdings") or {}
                for sym, px in prices.items():
                    h = holdings.get(sym)
                    if h:
                        h["last_price"] = _money(px)
            _save(store)
    items = [e for _p, _pr, e in priced]
    items.sort(key=lambda x: -(x.get("updated_at") or 0))
    return {"items": items}


@router.post("")
def create_portfolio(body: CreateBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "Name required")
    with _lock:
        store = _load()
        if len(store.get("portfolios") or []) >= MAX_PORTFOLIOS:
            raise HTTPException(400, f"At most {MAX_PORTFOLIOS} portfolios")
        now = _now()
        p = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "initial_cash": _money(body.amount),
            "cash": _money(body.amount),
            "created_at": now,
            "updated_at": now,
            "holdings": {},
            "trades": [],
            "snapshots": [{"t": now, "nav": _money(body.amount), "cash": _money(body.amount)}],
            "strategy": {"kind": "manual", "auto": False, "symbol": "SPY", "last_run_at": 0, "note": ""},
            "last_error": None,
            "origin": "paper",
        }
        store.setdefault("portfolios", []).append(p)
        _save(store)
        return _enrich(p)


@router.post("/import")
async def import_portfolio(
    name: str = Form(""),
    cash: str = Form(""),
    csv_text: str = Form(""),
    cost_basis: str = Form(""),
    file: UploadFile | None = File(None),
):
    """Create an imported snapshot from a read-only broker CSV/TSV. No login."""
    text = (csv_text or "").strip()
    if not text and file is not None and (file.filename or "").strip():
        raw = await file.read(IMPORT_MAX_BYTES + 1)
        if len(raw) > IMPORT_MAX_BYTES:
            raise HTTPException(400, "File too large (1 MB max)")
        text = raw.decode("utf-8-sig", errors="replace")
    if not text.strip():
        raise HTTPException(400, "Upload a CSV or paste positions (symbol, shares, average cost)")
    mode = str(cost_basis or "").strip().lower()
    if mode not in ("mark", "csv"):
        raise HTTPException(400, "Choose import-time price or CSV cost basis")
    try:
        parsed = parse_broker_csv(text)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    rows = list(parsed.get("holdings") or [])
    skipped = list(parsed.get("skipped") or [])
    if not rows:
        hint = skipped[0]["reason"] if skipped else "no stock/ETF rows"
        raise HTTPException(400, f"No importable holdings ({hint})")

    cash_override: float | None = None
    if str(cash or "").strip():
        try:
            cash_override = float(str(cash).replace(",", "").replace("$", "").strip())
        except ValueError as e:
            raise HTTPException(400, "Cash must be a number") from e
        if cash_override < 0:
            raise HTTPException(400, "Cash cannot be negative")

    marks = _price_map([str(h["symbol"]) for h in rows])
    holdings: dict[str, dict[str, float]] = {}
    trades: list[dict[str, Any]] = []
    cost_sum = 0.0
    used_mark_fallback = 0
    now = _now()
    for h in rows:
        sym = str(h["symbol"]).upper()
        shares = _shares(float(h["shares"]))
        csv_avg = float(h.get("avg_cost") or 0)
        mark_px = float(marks.get(sym) or 0)
        if mode == "mark":
            avg = mark_px
            if avg <= 0:
                skipped.append({"symbol": sym, "reason": "no quote at import"})
                continue
        else:
            avg = csv_avg if csv_avg > 0 else mark_px
            if csv_avg <= 0 and mark_px > 0:
                used_mark_fallback += 1
            if shares <= 0 or avg <= 0:
                skipped.append({"symbol": sym, "reason": "no cost basis or quote"})
                continue
        if shares <= 0:
            skipped.append({"symbol": sym, "reason": "no long shares"})
            continue
        last = mark_px if mark_px > 0 else avg
        holdings[sym] = {
            "shares": shares,
            "avg_cost": _money(avg),
            "last_price": _money(last),
        }
        notional = _money(shares * avg)
        cost_sum += notional
        trades.append(
            {
                "t": now,
                "symbol": sym,
                "side": "buy",
                "shares": shares,
                "price": _money(avg),
                "notional": notional,
                "source": "broker-csv",
            }
        )
    if not holdings:
        raise HTTPException(400, "Could not price any imported names")

    leftover = cash_override
    if leftover is None:
        leftover = float(parsed.get("cash") or 0)
    leftover = max(0.0, leftover)
    initial = _money(leftover + cost_sum)
    fund_name = (name or "").strip() or "Broker snapshot"
    notes: list[str] = [f"Imported {len(holdings)} names."]
    if skipped:
        notes.append(f"Skipped {len(skipped)} row(s) (options, crypto, or invalid).")
    if used_mark_fallback:
        notes.append(f"Used import-time price for {used_mark_fallback} name(s) missing CSV cost.")
    note = " ".join(notes)

    with _lock:
        store = _load()
        if len(store.get("portfolios") or []) >= MAX_PORTFOLIOS:
            raise HTTPException(400, f"At most {MAX_PORTFOLIOS} portfolios")
        p = {
            "id": uuid.uuid4().hex[:12],
            "name": fund_name[:80],
            "initial_cash": initial,
            "cash": _money(leftover),
            "created_at": now,
            "updated_at": now,
            "holdings": holdings,
            "trades": trades[-MAX_TRADES:],
            "snapshots": [
                {
                    "t": now,
                    "nav": _money(
                        leftover
                        + sum(float(h["shares"]) * float(h["last_price"]) for h in holdings.values())
                    ),
                    "cash": _money(leftover),
                }
            ],
            "strategy": {"kind": "manual", "auto": False, "symbol": "SPY", "last_run_at": 0, "note": ""},
            "last_error": None,
            "origin": "import",
            "cost_basis": mode,
        }
        store.setdefault("portfolios", []).append(p)
        _save(store)
        out = _enrich(p, marks)
        out["import_note"] = note
        if skipped:
            out["import_skipped"] = skipped[:20]
        return out


@router.get("/{pid}")
def get_portfolio(pid: str, live: bool = True):
    with _lock:
        p = copy.deepcopy(_find(_load(), pid))
    prices = _holding_prices(p)
    if live:
        prices = {**prices, **_price_map(_symbols(p))}
    out = _enrich(p, prices)
    if live:
        with _lock:
            store = _load()
            cur = _find(store, pid)
            _snapshot(cur, out["nav"])
            holdings = cur.get("holdings") or {}
            for sym, px in prices.items():
                h = holdings.get(sym)
                if h:
                    h["last_price"] = _money(px)
            _save(store)
    return out


@router.delete("/{pid}")
def delete_portfolio(pid: str):
    with _lock:
        store = _load()
        before = len(store.get("portfolios") or [])
        store["portfolios"] = [p for p in store.get("portfolios") or [] if p.get("id") != pid]
        if len(store["portfolios"]) == before:
            raise HTTPException(404, "Portfolio not found")
        _save(store)
    return {"ok": True}


@router.post("/{pid}/orders")
def place_order(pid: str, body: OrderBody):
    symbol = body.symbol.strip().upper().split(":")[-1]
    if body.shares is None and body.notional is None:
        raise HTTPException(400, "Provide shares or notional")
    if _quote is None:
        raise HTTPException(502, "Quote source not configured")
    px = None
    if _mark_session() != "rth" and _extended_marks is not None:
        try:
            ext = _extended_marks([symbol]) or {}
            cand = ext.get(symbol)
            if isinstance(cand, (int, float)) and cand > 0:
                px = float(cand)
        except Exception:
            px = None
    if px is None:
        try:
            q = _quote(symbol)
        except Exception as e:
            raise HTTPException(502, f"Quote failed: {e}") from e
        cand = q.get("price")
        if not isinstance(cand, (int, float)) or cand <= 0:
            raise HTTPException(502, f"No price for {symbol}")
        px = float(cand)
    shares = float(body.shares) if body.shares else float(body.notional) / float(px)
    shares = _shares(shares)
    if shares <= 0:
        raise HTTPException(400, "Order size too small")
    with _lock:
        store = _load()
        p = _find(store, pid)
        _fill_order(p, symbol, body.side, shares, float(px), "manual")
        out = _enrich(p)
        _snapshot(p, out["nav"], force=True)
        _save(store)
        return out


@router.put("/{pid}/strategy")
def set_strategy(pid: str, body: StrategyBody):
    with _lock:
        store = _load()
        p = _find(store, pid)
        p["strategy"] = {
            "kind": body.kind,
            "auto": bool(body.auto) and body.kind != "manual",
            "symbol": body.symbol.strip().upper().split(":")[-1] or "SPY",
            "last_run_at": (p.get("strategy") or {}).get("last_run_at") or 0,
            "note": (p.get("strategy") or {}).get("note") or "",
        }
        p["updated_at"] = _now()
        _save(store)
        return _enrich(p)


def run_due_auto_strategies() -> int:
    """Execute auto strategies that are due (default: every 1 hour). Returns how many ran."""
    ran = 0
    with _lock:
        store = _load()
        interval = _strategy_interval_sec()
        now = _now()
        for p in store.get("portfolios") or []:
            st = p.get("strategy") or {}
            if not st.get("auto") or st.get("kind") in (None, "manual"):
                continue
            last = int(st.get("last_run_at") or 0)
            if last and now - last < interval:
                continue
            prices = _price_map(_symbols(p))
            _run_strategy(p, prices, force=True)
            _snapshot(p, _nav(p, prices), force=True)
            p["updated_at"] = now
            ran += 1
        if ran:
            _save(store)
    return ran


@router.post("/{pid}/tick")
def tick_portfolio(pid: str, force: bool = False):
    with _lock:
        store = _load()
        p = _find(store, pid)
        st = p.get("strategy") or {}
        prices = _price_map(_symbols(p))
        note = None
        kind = st.get("kind")
        if kind not in (None, "manual") and (force or st.get("auto")):
            note = _run_strategy(p, prices, force=force)
        nav = _nav(p, prices)
        _snapshot(p, nav, force=True)
        p["updated_at"] = _now()
        _save(store)
        out = _enrich(p)
        out["tick_note"] = note
        return out
