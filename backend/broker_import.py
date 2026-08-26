"""Parse read-only broker position exports (CSV/TSV). No login, no trading."""

from __future__ import annotations

import csv
import io
import re
from typing import Any

MAX_HOLDINGS = 80
MAX_BYTES = 1_000_000

_OCC = re.compile(r"^[A-Z]{1,6}\d{6}[CP]\d{8}$")
_TICKER = re.compile(r"^[A-Z][A-Z0-9.]{0,7}$")

SYMBOL_KEYS = (
    "symbol",
    "ticker",
    "instrument",
    "stock",
    "financialinstrument",
    "underlyingsymbol",
    "underlying",
)
QTY_KEYS = ("quantity", "qty", "shares", "position", "sharequantity", "qtyavailable")
AVG_KEYS = (
    "averagecost",
    "averageprice",
    "avgcost",
    "avgprice",
    "costpershare",
    "costbasispershare",
    "costbasisprice",
    "unitcost",
    "openprice",
    "avgcostbasis",
    "averagecostbasis",
)
BASIS_KEYS = ("costbasis", "totalcost", "totalcostbasis", "bookvalue")
TYPE_KEYS = ("type", "assettype", "securitytype", "instrumenttype", "product")


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").strip().lower())


def _num(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s or s in (".", "-", "--", "n/a", "na", "none"):
        return None
    s = s.replace("$", "").replace(",", "").replace("%", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    m = re.search(r"-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", s)
    if not m:
        return None
    try:
        n = float(m.group(0))
    except ValueError:
        return None
    if n != n:  # NaN
        return None
    return n


def _col(header: list[str], keys: tuple[str, ...]) -> int | None:
    for i, h in enumerate(header):
        if h in keys:
            return i
    return None


def _ticker(raw: Any) -> str | None:
    s = str(raw or "").strip().upper()
    if not s:
        return None
    s = s.split(":")[-1].replace("-", ".").replace(" ", ".")
    s = re.sub(r"\.+", ".", s).strip(".")
    if s in ("CASH", "USD", "CURRENCY", "FOR"):
        return None
    if _OCC.match(s.replace(".", "")):
        return None
    if not _TICKER.match(s):
        return None
    return s


def _is_option(symbol: str, type_val: str) -> bool:
    t = type_val.lower()
    if any(w in t for w in ("option", "call", "put", "warrant", "right", "future", "crypto", "forex", "bond")):
        if "etf" in t or "stock" in t or "equity" in t:
            return False
        return True
    if " " in symbol and re.search(r"\d{2}[A-Z]{3}\d{2}", symbol):
        return True
    return False


def parse_broker_csv(text: str) -> dict[str, Any]:
    raw = (text or "").replace("\x00", "")
    if not raw.strip():
        raise ValueError("File is empty")
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
    except csv.Error:
        dialect = csv.excel
        if sample.count("\t") > sample.count(","):
            dialect.delimiter = "\t"
    reader = csv.reader(io.StringIO(raw), dialect)
    rows = [list(r) for r in reader if any(str(c).strip() for c in r)]
    if not rows:
        raise ValueError("No rows in file")

    header_i = 0
    header = [_norm(c) for c in rows[0]]
    if _col(header, SYMBOL_KEYS) is None or _col(header, QTY_KEYS) is None:
        header_i = -1
        for i, row in enumerate(rows[:50]):
            keys = [_norm(c) for c in row]
            if _col(keys, SYMBOL_KEYS) is not None and _col(keys, QTY_KEYS) is not None:
                header_i = i
                header = keys
                break
        if header_i < 0:
            header = ["symbol", "quantity", "averagecost"]
            header_i = -1
            body = rows
        else:
            body = rows[header_i + 1 :]
    else:
        body = rows[1:]

    si = _col(header, SYMBOL_KEYS)
    qi = _col(header, QTY_KEYS)
    ai = _col(header, AVG_KEYS)
    bi = _col(header, BASIS_KEYS)
    ti = _col(header, TYPE_KEYS)
    if si is None or qi is None:
        raise ValueError("Need a Symbol/Ticker column and a Quantity/Shares column")

    merged: dict[str, dict[str, float]] = {}
    skipped: list[dict[str, str]] = []
    cash: float | None = None
    for row in body:
        if len(row) <= max(si, qi):
            continue
        type_val = str(row[ti]).strip() if ti is not None and ti < len(row) else ""
        label = str(row[si]).strip()
        if _norm(label) in ("cash", "usd", "currency"):
            amt = _num(row[qi])
            if amt is not None:
                cash = (cash or 0) + amt
            continue
        if _is_option(label, type_val):
            skipped.append({"symbol": label or "—", "reason": "options / non-stock skipped"})
            continue
        sym = _ticker(label)
        if not sym:
            skipped.append({"symbol": label or "—", "reason": "not a stock/ETF ticker"})
            continue
        shares = _num(row[qi])
        if shares is None or shares <= 0:
            skipped.append({"symbol": sym, "reason": "no long shares"})
            continue
        avg = _num(row[ai]) if ai is not None and ai < len(row) else None
        if (avg is None or avg <= 0) and bi is not None and bi < len(row):
            basis = _num(row[bi])
            if basis is not None and shares > 0:
                avg = abs(basis) / shares
        prev = merged.get(sym)
        if prev:
            total_shares = prev["shares"] + shares
            cost = prev["shares"] * prev["avg_cost"] + shares * (avg or prev["avg_cost"])
            prev["shares"] = total_shares
            prev["avg_cost"] = cost / total_shares if total_shares else 0
        else:
            merged[sym] = {"shares": shares, "avg_cost": float(avg or 0)}

    holdings = [{"symbol": s, **v} for s, v in merged.items()]
    holdings.sort(key=lambda h: h["symbol"])
    if len(holdings) > MAX_HOLDINGS:
        extra = holdings[MAX_HOLDINGS:]
        holdings = holdings[:MAX_HOLDINGS]
        for h in extra:
            skipped.append({"symbol": h["symbol"], "reason": f"over {MAX_HOLDINGS} names"})
    return {"holdings": holdings, "cash": cash, "skipped": skipped[:40]}
