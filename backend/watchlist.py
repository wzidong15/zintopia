"""Watchlist JSON under ~/.zintopia (same directory as paper funds)."""

from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

DEFAULT_SYMBOLS = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "JPM",
    "UNH",
    "XOM",
]
MAX_SYMBOLS = 80
_SYM = re.compile(r"^[A-Z][A-Z0-9.\-]{0,15}$")
_lock = threading.Lock()
router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchSort(BaseModel):
    by: Literal["added", "name", "pct"] = "added"
    dir: Literal["asc", "desc"] = "asc"


class WatchBody(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    sort: WatchSort | None = None


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


def _path() -> Path:
    return _data_dir() / "watchlist.json"


def _norm_symbols(raw: list[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        sym = str(item).strip().upper()
        if not sym or not _SYM.match(sym) or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
        if len(out) >= MAX_SYMBOLS:
            break
    return out


def _default_sort() -> dict[str, str]:
    return {"by": "added", "dir": "asc"}


def _norm_sort(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return _default_sort()
    by = raw.get("by")
    if by not in ("added", "name", "pct"):
        by = "added"
    direction = "desc" if raw.get("dir") == "desc" else "asc"
    if by == "added":
        direction = "asc"
    return {"by": by, "dir": direction}


def _payload(*, persisted: bool, symbols: list[str] | None = None, sort: Any = None) -> dict[str, Any]:
    return {
        "symbols": list(DEFAULT_SYMBOLS if symbols is None else symbols),
        "sort": _norm_sort(sort),
        "persisted": persisted,
    }


@router.get("")
def get_watchlist() -> dict[str, Any]:
    path = _path()
    if not path.is_file():
        return _payload(persisted=False)
    try:
        data = json.loads(path.read_text())
    except Exception:
        return _payload(persisted=False)
    if not isinstance(data, dict):
        return _payload(persisted=False)
    return _payload(
        persisted=True,
        symbols=_norm_symbols(data.get("symbols") or []),
        sort=data.get("sort"),
    )


@router.put("")
def put_watchlist(body: WatchBody) -> dict[str, Any]:
    path = _path()
    with _lock:
        existing_sort: Any = None
        if path.is_file():
            try:
                prev = json.loads(path.read_text())
                if isinstance(prev, dict):
                    existing_sort = prev.get("sort")
            except Exception:
                existing_sort = None
        symbols = _norm_symbols(body.symbols)
        sort = _norm_sort(body.sort.model_dump() if body.sort is not None else existing_sort)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"symbols": symbols, "sort": sort}, indent=2))
        tmp.replace(path)
    return _payload(persisted=True, symbols=symbols, sort=sort)
