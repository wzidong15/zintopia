"""US equity terminal API.

Free sources (same stack as the TradingView / Vibe-Trading MCPs):
  - TradingView scanner  -> live-ish quotes, movers, ratings (delayed ~15m unsigned)
  - Yahoo Finance        -> OHLCV candles, company profile, news, movers fallback
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dt_time
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import threading

import httpx
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
import yfinance as yf
from fastapi import FastAPI, HTTPException, Query as Q
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from tradingview_screener import Query, col
from tradingview_ta import Interval, TA_Handler

import congress_ptr
import fundamentals as fundamentals_mod
import llm_advice
import newsfeed
import ownership as ownership_mod
import vibe_portfolio

POLYGON_KEY = os.environ.get("POLYGON_API_KEY") or os.environ.get("MASSIVE_API_KEY") or ""
POLYGON_BASE = os.environ.get("MASSIVE_API_BASE_URL") or "https://api.polygon.io"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    congress_ptr.kick_refresh()
    yield
    _close_http_pools()


app = FastAPI(title="Zintopia", version="0.1.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

INDEX_TICKERS = {
    "SPY": "AMEX:SPY",
    "QQQ": "NASDAQ:QQQ",
    "DIA": "AMEX:DIA",
    "IWM": "AMEX:IWM",
    "VIX": "CBOE:VIX",
}

TV_SECTORS = [
    "Commercial Services",
    "Communications",
    "Consumer Durables",
    "Consumer Non-Durables",
    "Consumer Services",
    "Distribution Services",
    "Electronic Technology",
    "Energy Minerals",
    "Finance",
    "Health Services",
    "Health Technology",
    "Industrial Services",
    "Miscellaneous",
    "Non-Energy Minerals",
    "Process Industries",
    "Producer Manufacturing",
    "Retail Trade",
    "Technology Services",
    "Transportation",
    "Utilities",
]
TV_EXTRA_COLS = ["price_sales_ratio", "earnings_release_next_date"]

QUOTE_COLS = [
    "name",
    "description",
    "close",
    "change",
    "change_abs",
    "open",
    "high",
    "low",
    "volume",
    "average_volume_30d_calc",
    "market_cap_basic",
    "price_earnings_ttm",
    "earnings_per_share_basic_ttm",
    "dividend_yield_recent",
    "High.1Y",
    "Low.1Y",
    "Perf.W",
    "Perf.1M",
    "Perf.3M",
    "Perf.Y",
    "RSI",
    "Recommend.All",
    "Recommend.MA",
    "Recommend.Other",
    "SMA20",
    "SMA50",
    "SMA200",
    "MACD.macd",
    "MACD.signal",
    "sector",
    "industry",
    "exchange",
    "type",
    "is_primary",
]

RANGE_TO_YF = {
    "1d": ("1d", "1m"),
    "5d": ("5d", "5m"),
    "1mo": ("1mo", "30m"),
    "3mo": ("3mo", "1d"),
    "6mo": ("6mo", "1d"),
    "1y": ("1y", "1d"),
    "5y": ("5y", "1wk"),
}

_cache: dict[str, tuple[float, Any]] = {}


def _env(*names: str, default: str = "") -> str:
    for name in names:
        raw = os.environ.get(name)
        if raw is not None and str(raw).strip() != "":
            return str(raw).strip()
    return default


def _live_refresh_sec() -> float:
    raw = _env("ZINTOPIA_LIVE_REFRESH_SEC", "FINTOPIA_LIVE_REFRESH_SEC", "UTOPIA_LIVE_REFRESH_SEC", default="10")
    try:
        sec = float(raw)
    except ValueError:
        sec = 10.0
    return max(2.0, min(sec, 300.0))


def _chart_refresh_sec() -> float:
    raw = _env("ZINTOPIA_CHART_REFRESH_SEC", "FINTOPIA_CHART_REFRESH_SEC", "UTOPIA_CHART_REFRESH_SEC", default="30")
    try:
        sec = float(raw)
    except ValueError:
        sec = 30.0
    return max(2.0, min(sec, 3600.0))


def _live_cache_ttl() -> float:
    """Shorter than the UI poll so /api/quote is not served stale."""
    return max(1.0, _live_refresh_sec() * 0.4)


def _chart_cache_ttl() -> float:
    """Shorter than the chart poll so /api/history is not served stale."""
    return max(1.0, _chart_refresh_sec() * 0.4)


def _refresh_bucket() -> int:
    return int(time.time() // max(_chart_refresh_sec(), 5.0))


def _cached(key: str, ttl: float, fn):
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = fn()
    _cache[key] = (now, value)
    return value


def _clean(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (float, int)) and (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return None
    if pd.isna(v):
        return None
    if hasattr(v, "item"):
        try:
            return _clean(v.item())
        except Exception:
            return str(v)
    return v


def _unix_ts(v: Any) -> int | None:
    n = _clean(v)
    if not isinstance(n, (int, float)) or n <= 0:
        return None
    ts = int(n)
    if ts > 1_000_000_000_000:
        ts //= 1000
    return ts if ts > 1_000_000_000 else None


def _next_earnings_unix(info: dict[str, Any]) -> int | None:
    """Soonest Yahoo earnings timestamp that is still today or later; else the latest past print."""
    cands: list[int] = []
    for key in ("earningsTimestampStart", "earningsTimestamp", "earningsTimestampEnd"):
        ts = _unix_ts(info.get(key))
        if ts is not None:
            cands.append(ts)
    if not cands:
        return None
    today = int(time.time()) - 16 * 3600
    future = [t for t in cands if t >= today]
    return min(future) if future else max(cands)


YAHOO_UA = "Mozilla/5.0 (compatible; Zintopia/1.0)"
QUOTE_HTTP_TIMEOUT = float(_env("ZINTOPIA_QUOTE_HTTP_TIMEOUT", "FINTOPIA_QUOTE_HTTP_TIMEOUT", "UTOPIA_QUOTE_HTTP_TIMEOUT", default="6") or "6")
TV_SCAN_HEADERS = {
    "accept": "application/json",
    "content-type": "application/json",
    "origin": "https://www.tradingview.com",
    "referer": "https://www.tradingview.com/",
}
_requests_lock = threading.Lock()


def _http_pool_size() -> int:
    raw = _env("ZINTOPIA_HTTP_POOL_SIZE", "FINTOPIA_HTTP_POOL_SIZE", "UTOPIA_HTTP_POOL_SIZE", default="20")
    try:
        n = int(raw)
    except ValueError:
        n = 20
    return max(2, min(n, 128))


def _http_keepalive() -> int:
    n = _http_pool_size()
    return max(1, min(n, 10 if n > 10 else n))


@lru_cache(maxsize=1)
def _outbound_config() -> tuple[str | None, str | None]:
    """(interface_name, bind_ip) for broken macOS TCP source selection (Errno 49)."""
    iface = _env("ZINTOPIA_BIND_INTERFACE", "FINTOPIA_BIND_INTERFACE", "UTOPIA_BIND_INTERFACE") or None
    ip = _env("ZINTOPIA_BIND_IP", "FINTOPIA_BIND_IP", "UTOPIA_BIND_IP") or None
    if not ip:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.settimeout(2.0)
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
        except OSError:
            ip = None
    return iface, ip


class _BindAdapter(HTTPAdapter):
    def __init__(self, bind_ip: str | None = None, **kwargs):
        self._bind_ip = bind_ip
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        if self._bind_ip:
            kwargs["source_address"] = (self._bind_ip, 0)
        return super().init_poolmanager(*args, **kwargs)


@lru_cache(maxsize=1)
def _requests_session() -> requests.Session:
    _, bind_ip = _outbound_config()
    n = _http_pool_size()
    session = requests.Session()
    session.headers.update({"User-Agent": YAHOO_UA})
    adapter = _BindAdapter(bind_ip, pool_connections=n, pool_maxsize=n)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


@lru_cache(maxsize=1)
def _httpx_client() -> httpx.Client:
    _, bind_ip = _outbound_config()
    n = _http_pool_size()
    keep = _http_keepalive()
    limits = httpx.Limits(max_connections=n, max_keepalive_connections=keep)
    kwargs: dict[str, Any] = {
        "timeout": httpx.Timeout(15.0),
        "headers": {"User-Agent": YAHOO_UA},
        "follow_redirects": True,
        "limits": limits,
    }
    if bind_ip:
        kwargs["transport"] = httpx.HTTPTransport(local_address=bind_ip, limits=limits)
    return httpx.Client(**kwargs)


@lru_cache(maxsize=1)
def _yf_session():
    """One curl_cffi session for all yfinance calls (required by yfinance 0.2)."""
    try:
        from curl_cffi import requests as cf_requests

        return cf_requests.Session(impersonate="chrome")
    except Exception:
        return None


def _yf_ticker(symbol: str) -> yf.Ticker:
    sess = _yf_session()
    if sess is None:
        return yf.Ticker(symbol)
    return yf.Ticker(symbol, session=sess)


def _yf_download(*args, **kwargs):
    sess = _yf_session()
    if sess is not None:
        kwargs.setdefault("session", sess)
    kwargs.setdefault("progress", False)
    kwargs.setdefault("threads", False)
    return yf.download(*args, **kwargs)


def _close_http_pools() -> None:
    if _httpx_client.cache_info().currsize:
        try:
            _httpx_client().close()
        except Exception:
            pass
        _httpx_client.cache_clear()
    if _requests_session.cache_info().currsize:
        try:
            _requests_session().close()
        except Exception:
            pass
        _requests_session.cache_clear()
    if _yf_session.cache_info().currsize:
        try:
            sess = _yf_session()
            if sess is not None:
                sess.close()
        except Exception:
            pass
        _yf_session.cache_clear()


def _curl_fetch(
    method: str,
    url: str,
    *,
    body: str | None = None,
    timeout: float = 15.0,
    ip_version: int | None = None,
    interface: str | None = None,
) -> str:
    cmd = [
        "curl",
        "-sS",
        "-L",
        "--max-time",
        str(max(1, int(timeout))),
        "-A",
        YAHOO_UA,
    ]
    if interface:
        cmd.extend(["--interface", interface])
    if ip_version == 4:
        cmd.insert(1, "-4")
    elif ip_version == 6:
        cmd.insert(1, "-6")
    if method.upper() == "POST":
        cmd.extend(["-X", "POST", "-H", "Content-Type: application/json"])
        if body is not None:
            cmd.extend(["-d", body])
    cmd.append(url)
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5, check=False)
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(err or f"curl exit {proc.returncode}")
    return proc.stdout


def _request_headers(json_body: dict[str, Any] | None) -> dict[str, str]:
    headers = {"User-Agent": YAHOO_UA}
    if json_body is not None:
        headers.update(TV_SCAN_HEADERS)
        headers["User-Agent"] = (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    return headers


def _requests_fetch(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float = 15.0,
    bind_ip: str | None = None,
) -> str:
    headers = _request_headers(json_body)
    session = _requests_session()
    with _requests_lock:
        if method.upper() == "GET":
            r = session.get(url, headers=headers, timeout=timeout)
        else:
            r = session.post(url, json=json_body, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def _httpx_fetch(
    method: str,
    url: str,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float = 15.0,
    bind_ip: str | None = None,
) -> str:
    headers = _request_headers(json_body)
    client = _httpx_client()
    if method.upper() == "GET":
        r = client.get(url, headers=headers, timeout=timeout)
    else:
        r = client.post(url, json=json_body, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.text


def _http_text(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> str:
    if method.upper() == "GET" and params:
        url = f"{url}?{urlencode(params)}"
    errors: list[str] = []
    body = json.dumps(json_body) if json_body is not None else None
    iface, bind_ip = _outbound_config()
    # Keep-alive first. curl is a last resort: each subprocess burns a TIME_WAIT 4-tuple.
    attempts: list[tuple[str, Any]] = [
        ("httpx", lambda: _httpx_fetch(method, url, json_body=json_body, timeout=timeout)),
        ("requests", lambda: _requests_fetch(method, url, json_body=json_body, timeout=timeout)),
    ]
    if iface:
        attempts.append(
            ("curl-if", lambda: _curl_fetch(method, url, body=body, timeout=timeout, interface=iface))
        )
    if bind_ip:
        attempts.append(
            ("curl-bind", lambda: _curl_fetch(method, url, body=body, timeout=timeout, interface=bind_ip))
        )
    attempts.extend(
        [
            ("curl", lambda: _curl_fetch(method, url, body=body, timeout=timeout)),
            ("curl6", lambda: _curl_fetch(method, url, body=body, timeout=timeout, ip_version=6)),
            ("curl4", lambda: _curl_fetch(method, url, body=body, timeout=timeout, ip_version=4)),
        ]
    )
    for label, fn in attempts:
        try:
            return fn()
        except Exception as e:
            errors.append(f"{label}: {e}")
    raise RuntimeError("; ".join(errors))


def _http_json(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: float = 15.0,
) -> Any:
    text = _http_text(method, url, params=params, json_body=json_body, timeout=timeout)
    if not (text or "").strip():
        raise RuntimeError("empty response")
    return json.loads(text)


def _tv_get_scanner_data(q: Query) -> tuple[int, pd.DataFrame]:
    q.query.setdefault("range", [0, 50])
    json_obj = _http_json("POST", q.url, json_body=q.query, timeout=20)
    rows_count = int(json_obj.get("totalCount") or 0)
    data = json_obj.get("data") or []
    df = pd.DataFrame(
        data=([row["s"], *row["d"]] for row in data),
        columns=["ticker", *q.query.get("columns", ())],
    )
    return rows_count, df


def _make_quote(
    symbol: str,
    *,
    price: Any,
    prev: Any = None,
    open_: Any = None,
    high: Any = None,
    low: Any = None,
    volume: Any = None,
    market_cap: Any = None,
    year_high: Any = None,
    year_low: Any = None,
    exchange: Any = None,
    name: Any = None,
    source: str,
    delay: str,
    regular_close: Any = None,
) -> dict[str, Any]:
    yf_sym = symbol.strip().upper().split(":")[-1]
    price = _clean(price)
    prev = _clean(prev)
    regular_close = _clean(regular_close)
    change = (price - prev) if price is not None and prev else None
    change_pct = (change / prev * 100) if change is not None and prev else None
    return {
        "ticker": yf_sym,
        "symbol": yf_sym,
        "name": name or yf_sym,
        "exchange": _clean(exchange),
        "price": price,
        "change_pct": change_pct,
        "change": change,
        "open": _clean(open_),
        "high": _clean(high),
        "low": _clean(low),
        "volume": _clean(volume),
        "market_cap": _clean(market_cap),
        "year_high": _clean(year_high),
        "year_low": _clean(year_low),
        "prev_close": prev,
        "regular_close": regular_close if regular_close is not None else price,
        "source": source,
        "delay": delay,
        "as_of": int(time.time()),
    }


def _row_to_quote(row: pd.Series) -> dict[str, Any]:
    ticker = str(row.get("ticker") or "")
    symbol = str(row.get("name") or ticker.split(":")[-1])
    rec = _clean(row.get("Recommend.All"))
    rec_label = None
    if rec is not None:
        if rec >= 0.5:
            rec_label = "STRONG BUY"
        elif rec >= 0.1:
            rec_label = "BUY"
        elif rec > -0.1:
            rec_label = "NEUTRAL"
        elif rec > -0.5:
            rec_label = "SELL"
        else:
            rec_label = "STRONG SELL"
    return {
        "ticker": ticker,
        "symbol": symbol,
        "name": _clean(row.get("description")) or symbol,
        "exchange": _clean(row.get("exchange")) or (ticker.split(":")[0] if ":" in ticker else None),
        "price": _clean(row.get("close")),
        "change_pct": _clean(row.get("change")),
        "change": _clean(row.get("change_abs")),
        "open": _clean(row.get("open")),
        "high": _clean(row.get("high")),
        "low": _clean(row.get("low")),
        "volume": _clean(row.get("volume")),
        "avg_volume": _clean(row.get("average_volume_30d_calc")),
        "market_cap": _clean(row.get("market_cap_basic")),
        "pe": _clean(row.get("price_earnings_ttm")),
        "eps": _clean(row.get("earnings_per_share_basic_ttm")),
        "dividend_yield": _clean(row.get("dividend_yield_recent")),
        "year_high": _clean(row.get("High.1Y")),
        "year_low": _clean(row.get("Low.1Y")),
        "perf_w": _clean(row.get("Perf.W")),
        "perf_1m": _clean(row.get("Perf.1M")),
        "perf_3m": _clean(row.get("Perf.3M")),
        "perf_y": _clean(row.get("Perf.Y")),
        "rsi": _clean(row.get("RSI")),
        "sma20": _clean(row.get("SMA20")),
        "sma50": _clean(row.get("SMA50")),
        "sma200": _clean(row.get("SMA200")),
        "macd": _clean(row.get("MACD.macd")),
        "macd_signal": _clean(row.get("MACD.signal")),
        "recommend": rec,
        "recommend_label": rec_label,
        "recommend_ma": _clean(row.get("Recommend.MA")),
        "recommend_os": _clean(row.get("Recommend.Other")),
        "sector": _clean(row.get("sector")),
        "industry": _clean(row.get("industry")),
        "ps": _clean(row.get("price_sales_ratio")),
        "earnings_at": _tv_date_unix(row.get("earnings_release_next_date")),
        "prev_close": None,
        "regular_close": _clean(row.get("close")),
        "source": "tradingview-screener",
        "delay": "delayed_streaming_900",
        "as_of": int(time.time()),
    }


def _tv_date_unix(v: Any) -> int | None:
    ts = _unix_ts(v)
    if ts:
        return ts
    s = str(v or "").strip()
    if not s or s.lower() in ("nan", "none", "nat"):
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            d = datetime.strptime(s[:19] if "T" in s else s[:10], fmt)
            return int(d.replace(tzinfo=NYSE_TZ).timestamp())
        except Exception:
            continue
    return None


def _tv_query(
    tickers: list[str] | None = None,
    extra_where=None,
    order=None,
    limit=50,
    extra_cols: list[str] | None = None,
) -> pd.DataFrame:
    cols = list(QUOTE_COLS)
    for name in extra_cols or []:
        if name not in cols:
            cols.append(name)
    q = Query().select(*cols).set_markets("america")
    if tickers:
        q = q.set_tickers(*tickers)
    filters = []
    if extra_where:
        filters.extend(extra_where)
    if filters:
        q = q.where(*filters)
    if order:
        q = q.order_by(*order) if isinstance(order, tuple) else q.order_by(order)
    q = q.limit(limit)
    try:
        _, df = _tv_get_scanner_data(q)
    except Exception:
        if extra_cols:
            return _tv_query(tickers=tickers, extra_where=extra_where, order=order, limit=limit, extra_cols=None)
        raise
    if df is None or df.empty:
        return pd.DataFrame()
    return df


def _us_stock_filters(extra=None) -> list:
    filters = [
        col("type") == "stock",
        col("is_primary") == True,  # noqa: E712
        col("exchange").isin(["NASDAQ", "NYSE", "AMEX"]),
    ]
    if extra:
        filters.extend(extra)
    return filters


def _overlay_tv_fields(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill P/E, RVOL, sector, earnings date from one TradingView scan (watchlist / screen)."""
    if not rows:
        return rows
    tickers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        sym = str(row.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        try:
            tickers.append(resolve_tv_ticker(sym))
        except Exception:
            continue
    if not tickers:
        return rows
    key = "tv-overlay:" + ",".join(sorted({t.split(":")[-1] for t in tickers}))

    def fetch():
        df = _tv_query(tickers=tickers, limit=max(len(tickers) + 4, 10), extra_cols=TV_EXTRA_COLS)
        by: dict[str, dict[str, Any]] = {}
        for _, src in df.iterrows():
            q = _row_to_quote(src)
            by[str(q.get("symbol") or "").upper()] = q
        return by

    try:
        by = _cached(key, 60.0, fetch)
    except Exception:
        return rows
    fill = (
        "pe",
        "eps",
        "avg_volume",
        "dividend_yield",
        "rsi",
        "sector",
        "industry",
        "perf_1m",
        "market_cap",
        "ps",
        "earnings_at",
    )
    out: list[dict[str, Any]] = []
    for q in rows:
        extra = by.get(str(q.get("symbol") or "").upper(), {})
        merged = dict(q)
        for k in fill:
            if merged.get(k) in (None, "") and extra.get(k) not in (None, ""):
                merged[k] = extra[k]
        extra_name = extra.get("name")
        if extra_name and (not merged.get("name") or merged.get("name") == merged.get("symbol")):
            merged["name"] = extra_name
        out.append(merged)
    return out


@lru_cache(maxsize=512)
def resolve_tv_ticker(symbol: str) -> str:
    symbol = symbol.strip().upper()
    if ":" in symbol:
        return symbol
    if symbol in INDEX_TICKERS:
        return INDEX_TICKERS[symbol]
    try:
        _, df = _tv_get_scanner_data(
            Query()
            .select("name", "exchange", "type", "is_primary", "market_cap_basic")
            .set_markets("america")
            .where(col("name") == symbol)
            .limit(20)
        )
    except Exception as e:
        raise HTTPException(502, f"TradingView lookup failed: {e}") from e
    if df is None or df.empty:
        raise HTTPException(404, f"Unknown symbol {symbol}")
    primary = df[df["is_primary"] == True] if "is_primary" in df.columns else df  # noqa: E712
    if primary.empty:
        primary = df
    stocks = primary[primary.get("type", "stock") == "stock"] if "type" in primary.columns else primary
    pick = stocks.iloc[0] if not stocks.empty else primary.iloc[0]
    return str(pick["ticker"])


NYSE_TZ = ZoneInfo("America/New_York")
PRE_OPEN = dt_time(4, 0)
RTH_OPEN = dt_time(9, 30)
RTH_CLOSE = dt_time(16, 0)
POST_CLOSE = dt_time(20, 0)


def _us_equity_session() -> str:
    """NYSE cash session in Eastern time: pre / rth / post / closed (overnight + weekend)."""
    now = datetime.now(NYSE_TZ)
    if now.weekday() >= 5:
        return "closed"
    t = now.time()
    if PRE_OPEN <= t < RTH_OPEN:
        return "pre"
    if RTH_OPEN <= t < RTH_CLOSE:
        return "rth"
    if RTH_CLOSE <= t < POST_CLOSE:
        return "post"
    return "closed"


SESSION_LABELS = {
    "pre": "Pre-market",
    "rth": "Open market",
    "post": "Post-market",
    "closed": "Closed",
}
SESSION_HOURS = {
    "pre": "4:00–9:30 AM ET",
    "rth": "9:30 AM–4:00 PM ET",
    "post": "4:00–8:00 PM ET",
    "closed": "8:00 PM–4:00 AM ET",
}


def _us_equity_session_info() -> dict[str, Any]:
    now = datetime.now(NYSE_TZ)
    sess = _us_equity_session()
    hours = "Weekend" if now.weekday() >= 5 else SESSION_HOURS[sess]
    time_et = now.strftime("%I:%M:%S %p").lstrip("0") + " ET"
    return {
        "session": sess,
        "label": SESSION_LABELS[sess],
        "hours": hours,
        "time_et": time_et,
        "tz": "America/New_York",
    }


def _yahoo_ticker_symbol(symbol: str) -> str:
    yf_sym = symbol.strip().upper().split(":")[-1]
    return "^VIX" if yf_sym == "VIX" else yf_sym


def _yahoo_session_fields(symbol: str) -> dict[str, Any]:
    t = _yf_ticker(_yahoo_ticker_symbol(symbol))
    try:
        info = t.info or {}
    except Exception:
        return {}
    return {
        "regular_close": _clean(info.get("regularMarketPrice")),
        "prev_close": _clean(info.get("regularMarketPreviousClose") or info.get("previousClose")),
        "pre_price": _clean(info.get("preMarketPrice")),
        "post_price": _clean(info.get("postMarketPrice")),
    }


def _apply_session_last(
    q: dict[str, Any],
    sess: str,
    marks: dict[str, float],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = dict(q)
    out["session"] = sess
    if not out.get("regular_close"):
        out["regular_close"] = out.get("prev_close") or out.get("price")
    extra = extra or {}
    if extra.get("regular_close"):
        out["regular_close"] = extra["regular_close"]
    if extra.get("prev_close"):
        out["prev_close"] = extra["prev_close"]
    if extra.get("pre_price") is not None:
        out["pre_price"] = extra.get("pre_price")
    if extra.get("post_price") is not None:
        out["post_price"] = extra.get("post_price")
    if sess != "rth":
        sym = str(out.get("symbol") or "").upper()
        live_px = marks.get(sym)
        if not (isinstance(live_px, (int, float)) and live_px > 0):
            live_px = extra.get("pre_price") if sess == "pre" else extra.get("post_price")
        if isinstance(live_px, (int, float)) and live_px > 0:
            out["price"] = live_px
            out["delay"] = "yahoo_pre" if sess == "pre" else "yahoo_post"
            if sess == "pre":
                out["pre_price"] = live_px
            else:
                out["post_price"] = live_px
    prev = out.get("prev_close")
    price = out.get("price")
    if isinstance(price, (int, float)) and isinstance(prev, (int, float)) and prev:
        out["change"] = round(price - prev, 4)
        out["change_pct"] = round((price - prev) / prev * 100, 4)
    close = out.get("regular_close")
    if (
        sess != "rth"
        and isinstance(price, (int, float))
        and isinstance(close, (int, float))
        and close
    ):
        out["vs_close"] = round(price - close, 4)
        out["vs_close_pct"] = round((price - close) / close * 100, 4)
    return out


def _enrich_quote_session(q: dict[str, Any]) -> dict[str, Any]:
    """Attach regular close and Yahoo pre/post last when the cash session is not open."""
    sess = _us_equity_session()
    if sess == "rth":
        out = dict(q)
        out["session"] = sess
        if not out.get("regular_close"):
            out["regular_close"] = out.get("prev_close") or out.get("price")
        return out
    sym = str(q.get("symbol") or "")
    extra: dict[str, Any] = {}
    try:
        extra = _cached(f"yahoo-sess:{sym.upper()}", 20.0, lambda: _yahoo_session_fields(sym))
    except Exception:
        extra = {}
    marks: dict[str, float] = {}
    try:
        marks = _yahoo_extended_marks([sym])
    except Exception:
        marks = {}
    return _apply_session_last(q, sess, marks, extra)


def _enrich_quotes_session(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sess = _us_equity_session()
    if sess == "rth" or not rows:
        return [_apply_session_last(q, sess, {}, {}) for q in rows]
    syms = [str(q.get("symbol") or "") for q in rows if q.get("symbol")]
    marks: dict[str, float] = {}
    try:
        marks = _yahoo_extended_marks(syms)
    except Exception:
        marks = {}
    return [_apply_session_last(q, sess, marks, {}) for q in rows]


def _yahoo_info_extended_price(symbol: str, session: str) -> float | None:
    t = _yf_ticker(_yahoo_ticker_symbol(symbol))
    try:
        info = t.info or {}
    except Exception:
        return None
    if session == "pre":
        return _clean(info.get("preMarketPrice"))
    if session == "post":
        return _clean(info.get("postMarketPrice"))
    return _clean(info.get("postMarketPrice")) or _clean(info.get("preMarketPrice"))


def _last_close_from_yahoo_df(df: pd.DataFrame) -> float | None:
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    if "Close" not in df.columns:
        return None
    closes = df["Close"].dropna()
    if closes.empty:
        return None
    return _clean(closes.iloc[-1])


def _yahoo_prepost_download_marks(symbols: list[str]) -> dict[str, float]:
    session = _us_equity_session()
    period = "1d" if session in ("pre", "post") else "5d"
    yf_syms = [_yahoo_ticker_symbol(s) for s in symbols]
    orig = { _yahoo_ticker_symbol(s): s.strip().upper().split(":")[-1] for s in symbols }
    out: dict[str, float] = {}
    if len(yf_syms) == 1:
        df = _yf_download(
            yf_syms[0],
            period=period,
            interval="1m",
            prepost=True,
            auto_adjust=True,
            progress=False,
            threads=False,
        )
        px = _last_close_from_yahoo_df(df)
        if isinstance(px, (int, float)) and px > 0:
            out[orig[yf_syms[0]]] = float(px)
        return out
    df = _yf_download(
        yf_syms,
        period=period,
        interval="1m",
        prepost=True,
        auto_adjust=True,
        progress=False,
        threads=False,
        group_by="ticker",
    )
    for yf_sym, key in orig.items():
        try:
            sub = df[yf_sym] if isinstance(df.columns, pd.MultiIndex) else df
            px = _last_close_from_yahoo_df(sub)
        except Exception:
            continue
        if isinstance(px, (int, float)) and px > 0:
            out[key] = float(px)
    return out


def _yahoo_extended_marks(symbols: list[str]) -> dict[str, float]:
    """Yahoo pre-market / post-market last when the regular NYSE session is closed."""
    session = _us_equity_session()
    if session == "rth" or not symbols:
        return {}
    uniq = [s.strip().upper().split(":")[-1] for s in dict.fromkeys(symbols) if s]
    uniq = [s for s in uniq if s]
    if not uniq:
        return {}
    bucket = _refresh_bucket()
    ttl = max(_chart_refresh_sec(), 15.0)
    now = time.time()
    found: dict[str, float] = {}
    missing: list[str] = []
    for sym in uniq:
        hit = _cache.get(f"yahoo-ext1:{session}:{sym}:{bucket}")
        if hit and now - hit[0] < ttl and isinstance(hit[1], (int, float)) and hit[1] > 0:
            found[sym] = float(hit[1])
        else:
            missing.append(sym)
    if not missing:
        return found
    fetched: dict[str, float] = {}
    try:
        fetched.update(_yahoo_prepost_download_marks(missing))
    except Exception:
        pass
    still = [s for s in missing if s not in fetched]

    def one(sym: str) -> tuple[str, float | None]:
        return sym, _yahoo_info_extended_price(sym, session)

    if still:
        with ThreadPoolExecutor(max_workers=min(8, len(still))) as pool:
            futs = [pool.submit(one, s) for s in still]
            for fut in as_completed(futs):
                try:
                    sym, px = fut.result()
                except Exception:
                    continue
                if isinstance(px, (int, float)) and px > 0:
                    fetched[sym] = float(px)
    stamped = time.time()
    for sym, px in fetched.items():
        if isinstance(px, (int, float)) and px > 0:
            found[sym] = float(px)
            _cache[f"yahoo-ext1:{session}:{sym}:{bucket}"] = (stamped, float(px))
    return found


def _yahoo_quote(symbol: str) -> dict[str, Any]:
    ticker_sym = _yahoo_ticker_symbol(symbol)
    t = _yf_ticker(ticker_sym)

    try:
        fi = t.fast_info
        price = _clean(getattr(fi, "last_price", None))
        if price is not None:
            return _make_quote(
                symbol,
                price=price,
                prev=_clean(getattr(fi, "previous_close", None)),
                open_=_clean(getattr(fi, "open", None)),
                high=_clean(getattr(fi, "day_high", None)),
                low=_clean(getattr(fi, "day_low", None)),
                volume=_clean(getattr(fi, "last_volume", None)),
                market_cap=_clean(getattr(fi, "market_cap", None)),
                year_high=_clean(getattr(fi, "year_high", None)),
                year_low=_clean(getattr(fi, "year_low", None)),
                exchange=_clean(getattr(fi, "exchange", None)),
                source="yfinance",
                delay="yahoo",
            )
    except Exception:
        pass

    for period in ("5d", "1mo", "3mo"):
        try:
            hist = t.history(period=period, interval="1d")
            if hist is not None and not hist.empty:
                row = hist.iloc[-1]
                prev = _clean(hist.iloc[-2]["Close"]) if len(hist) >= 2 else None
                return _make_quote(
                    symbol,
                    price=_clean(row.get("Close")),
                    prev=prev,
                    open_=_clean(row.get("Open")),
                    high=_clean(row.get("High")),
                    low=_clean(row.get("Low")),
                    volume=_clean(row.get("Volume")),
                    source="yfinance",
                    delay="yahoo",
                )
        except Exception:
            continue

    try:
        info = t.info or {}
        price = _clean(info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose"))
        if price is not None:
            prev = _clean(info.get("regularMarketPreviousClose") or info.get("previousClose"))
            delay = "yahoo" if info.get("regularMarketPrice") or info.get("currentPrice") else "previous_close"
            return _make_quote(
                symbol,
                price=price,
                prev=prev if delay == "yahoo" else None,
                open_=_clean(info.get("regularMarketOpen") or info.get("open")),
                high=_clean(info.get("regularMarketDayHigh") or info.get("dayHigh")),
                low=_clean(info.get("regularMarketDayLow") or info.get("dayLow")),
                volume=_clean(info.get("regularMarketVolume") or info.get("volume")),
                market_cap=_clean(info.get("marketCap")),
                year_high=_clean(info.get("fiftyTwoWeekHigh")),
                year_low=_clean(info.get("fiftyTwoWeekLow")),
                exchange=_clean(info.get("exchange")),
                name=_clean(info.get("shortName") or info.get("longName")),
                source="yfinance",
                delay=delay,
            )
    except Exception:
        pass

    raise RuntimeError(f"Yahoo quote unavailable for {symbol.strip().upper().split(':')[-1]}")


def _bar_session(ts: pd.Timestamp) -> str:
    t = ts
    try:
        if t.tzinfo is None:
            t = t.tz_localize(NYSE_TZ, ambiguous="NaT", nonexistent="shift_forward")
        else:
            t = t.tz_convert(NYSE_TZ)
    except Exception:
        t = ts
    if pd.isna(t):
        return "rth"
    tm = t.time()
    if tm < RTH_OPEN:
        return "pre"
    if tm >= RTH_CLOSE:
        return "post"
    return "rth"


def _bar_unix(ts: pd.Timestamp) -> int | None:
    """Unix seconds. Naive Yahoo dates are the ET trading day, not UTC midnight."""
    t = pd.Timestamp(ts)
    if pd.isna(t):
        return None
    if t.tzinfo is None:
        try:
            t = t.tz_localize(NYSE_TZ, ambiguous="NaT", nonexistent="shift_forward")
        except Exception:
            t = pd.Timestamp(ts)
        if pd.isna(t):
            return None
    return int(t.timestamp())


def _yfinance_history_bars(symbol: str, range: str) -> dict[str, Any]:
    period, interval = RANGE_TO_YF[range]
    yf_sym = _yahoo_ticker_symbol(symbol)
    session = _us_equity_session()
    prepost = interval not in ("1d", "1wk") and session != "rth"
    df = _yf_download(
        yf_sym,
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
        threads=False,
        prepost=prepost,
    )
    if df is None or df.empty:
        raise RuntimeError(f"No history for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    time_col = "Datetime" if "Datetime" in df.columns else "Date"
    bars: list[dict[str, Any]] = []
    for _, r in df.iterrows():
        ts = pd.Timestamp(r[time_col])
        unix = _bar_unix(ts)
        if unix is None:
            continue
        bars.append(
            {
                "time": unix,
                "open": _clean(r.get("Open")),
                "high": _clean(r.get("High")),
                "low": _clean(r.get("Low")),
                "close": _clean(r.get("Close")),
                "volume": _clean(r.get("Volume")),
                "session": _bar_session(ts) if prepost else "rth",
            }
        )
    bars = [b for b in bars if b["close"] is not None]
    if not bars:
        raise RuntimeError(f"No history bars for {symbol}")
    if session != "rth":
        px = None
        if interval != "1m":
            try:
                marks = _yahoo_extended_marks([symbol])
                px = marks.get(symbol.strip().upper().split(":")[-1])
            except Exception:
                px = None
        if isinstance(px, (int, float)) and px > 0:
            last = dict(bars[-1])
            last["close"] = float(px)
            high = last.get("high")
            low = last.get("low")
            last["high"] = max(float(high), float(px)) if isinstance(high, (int, float)) else float(px)
            last["low"] = min(float(low), float(px)) if isinstance(low, (int, float)) else float(px)
            if session in ("pre", "post"):
                last["session"] = session
            bars = bars[:-1] + [last]
        elif session in ("pre", "post"):
            last = dict(bars[-1])
            last["session"] = session
            bars = bars[:-1] + [last]
    return {
        "symbol": yf_sym,
        "interval": interval,
        "range": range,
        "source": "yfinance",
        "prepost": prepost,
        "session": session,
        "bars": bars,
    }


def _yahoo_download_close(symbol: str) -> dict[str, Any]:
    ticker_sym = _yahoo_ticker_symbol(symbol)
    for period in ("5d", "1mo", "3mo", "6mo", "1y", "max"):
        try:
            df = _yf_download(
                ticker_sym,
                period=period,
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        row = df.iloc[-1]
        prev = _clean(df.iloc[-2]["Close"]) if len(df) >= 2 else None
        return _make_quote(
            symbol,
            price=_clean(row.get("Close")),
            prev=prev,
            open_=_clean(row.get("Open")),
            high=_clean(row.get("High")),
            low=_clean(row.get("Low")),
            volume=_clean(row.get("Volume")),
            source="yfinance",
            delay="previous_close",
        )
    raise RuntimeError(f"No historical close for {symbol.strip().upper().split(':')[-1]}")


def _stooq_symbol(symbol: str) -> str:
    yf_sym = symbol.strip().upper().split(":")[-1]
    if yf_sym == "VIX":
        return "^vix"
    if yf_sym in INDEX_TICKERS:
        return f"{yf_sym.lower()}.us"
    return f"{yf_sym.lower()}.us"


def _stooq_quote(symbol: str) -> dict[str, Any]:
    stooq_sym = _stooq_symbol(symbol)
    text = _http_text(
        "GET",
        "https://stooq.com/q/l/",
        params={"s": stooq_sym, "f": "sd2t2ohlcv", "h": "", "e": "csv"},
        timeout=QUOTE_HTTP_TIMEOUT,
    )
    rows = list(csv.reader(io.StringIO(text.strip())))
    if len(rows) < 2 or len(rows[1]) < 7:
        text = _http_text("GET", "https://stooq.com/q/d/l/", params={"s": stooq_sym, "i": "d"})
        rows = list(csv.reader(io.StringIO(text.strip())))
        if len(rows) < 2 or len(rows[-1]) < 5:
            raise RuntimeError(f"Stooq returned no rows for {stooq_sym}")
        last = rows[-1]
        prev = _clean(float(rows[-2][4])) if len(rows) >= 3 else None
        return _make_quote(
            symbol,
            price=_clean(float(last[4])),
            prev=prev,
            open_=_clean(float(last[1])) if last[1] else None,
            high=_clean(float(last[2])) if last[2] else None,
            low=_clean(float(last[3])) if last[3] else None,
            volume=_clean(float(last[5])) if len(last) > 5 and last[5] else None,
            source="stooq",
            delay="previous_close",
        )

    row = rows[1]
    open_ = _clean(float(row[3])) if row[3] else None
    high = _clean(float(row[4])) if row[4] else None
    low = _clean(float(row[5])) if row[5] else None
    close = _clean(float(row[6])) if row[6] else None
    volume = _clean(float(row[7])) if len(row) > 7 and row[7] else None
    if close is None:
        raise RuntimeError(f"Stooq close missing for {stooq_sym}")
    return _make_quote(
        symbol,
        price=close,
        open_=open_,
        high=high,
        low=low,
        volume=volume,
        source="stooq",
        delay="previous_close",
    )


def _tv_quote(symbol: str) -> dict[str, Any]:
    raw = symbol.strip().upper()
    yf_sym = raw.split(":")[-1]
    candidates: list[str] = []
    if yf_sym in INDEX_TICKERS:
        candidates.append(INDEX_TICKERS[yf_sym])
    elif ":" in raw:
        candidates.append(raw)
    else:
        try:
            candidates.append(resolve_tv_ticker(symbol))
        except Exception:
            pass
        candidates.extend([f"NASDAQ:{yf_sym}", f"NYSE:{yf_sym}", f"AMEX:{yf_sym}"])

    seen: set[str] = set()
    for tv in candidates:
        if tv in seen:
            continue
        seen.add(tv)
        df = _tv_query([tv], limit=1)
        if not df.empty and _clean(df.iloc[0].get("close")) is not None:
            return _row_to_quote(df.iloc[0])

    try:
        _, df = _tv_get_scanner_data(
            Query()
            .select(*QUOTE_COLS)
            .set_markets("america")
            .where(col("name") == yf_sym, col("is_primary") == True)  # noqa: E712
            .limit(1)
        )
        if df is not None and not df.empty and _clean(df.iloc[0].get("close")) is not None:
            return _row_to_quote(df.iloc[0])
    except Exception:
        pass

    raise RuntimeError(f"TradingView quote unavailable for {yf_sym}")


def _polygon_prev_quote(symbol: str) -> dict[str, Any]:
    yf_sym = symbol.strip().upper().split(":")[-1]
    if yf_sym == "VIX":
        raise RuntimeError("VIX is not a Polygon stock ticker")
    url = f"{POLYGON_BASE}/v2/aggs/ticker/{yf_sym}/prev"
    body = _http_json("GET", url, params={"adjusted": "true", "apiKey": POLYGON_KEY})
    rows = body.get("results") or []
    if not rows:
        raise RuntimeError(f"Polygon prev close unavailable for {yf_sym}")
    bar = rows[0]
    price = _clean(bar.get("c"))
    if price is None:
        raise RuntimeError(f"Polygon prev close empty for {yf_sym}")
    return _make_quote(
        symbol,
        price=price,
        open_=_clean(bar.get("o")),
        high=_clean(bar.get("h")),
        low=_clean(bar.get("l")),
        volume=_clean(bar.get("v")),
        source="polygon",
        delay="previous_close",
    )


def _polygon_quote(symbol: str) -> dict[str, Any]:
    yf_sym = symbol.strip().upper().split(":")[-1]
    if yf_sym == "VIX":
        raise RuntimeError("VIX is not a Polygon stock ticker")
    url = f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{yf_sym}"
    body = _http_json("GET", url, params={"apiKey": POLYGON_KEY})
    t = body.get("ticker") or {}
    day = t.get("day") or {}
    prev = t.get("prevDay") or {}
    last = t.get("lastTrade") or {}
    price = _clean(last.get("p")) or _clean(day.get("c"))
    prev_close = _clean(prev.get("c"))
    change = _clean(t.get("todaysChange"))
    change_pct = _clean(t.get("todaysChangePerc"))
    return {
        "ticker": yf_sym,
        "symbol": yf_sym,
        "name": yf_sym,
        "exchange": None,
        "price": price,
        "change_pct": change_pct,
        "change": change,
        "open": _clean(day.get("o")),
        "high": _clean(day.get("h")),
        "low": _clean(day.get("l")),
        "volume": _clean(day.get("v")),
        "year_high": None,
        "year_low": None,
        "prev_close": prev_close,
        "regular_close": _clean(day.get("c")) or prev_close,
        "source": "polygon",
        "delay": "realtime",
        "as_of": int(time.time()),
    }


def _try_quote_source(fn, symbol: str) -> dict[str, Any] | None:
    try:
        quote = fn(symbol)
        if quote.get("price") is not None:
            return quote
    except Exception:
        pass
    return None


def _best_quote(symbol: str) -> dict[str, Any]:
    yf_sym = symbol.strip().upper().split(":")[-1]
    attempts: list[tuple[str, Any]] = []
    if POLYGON_KEY:
        attempts.append(("polygon", _polygon_quote))
    attempts.extend(
        [
            ("tradingview", _tv_quote),
            ("yfinance", _yahoo_quote),
            ("yfinance-close", _yahoo_download_close),
            ("stooq", _stooq_quote),
        ]
    )
    if POLYGON_KEY:
        attempts.append(("polygon-prev", _polygon_prev_quote))
    for _name, fn in attempts:
        result = _try_quote_source(fn, symbol)
        if result:
            return result
    raise RuntimeError(f"No quote for {yf_sym}")


def _best_quotes(symbols: list[str]) -> list[dict[str, Any]]:
    if not symbols:
        return []
    if len(symbols) == 1:
        return [_best_quote(symbols[0])]
    out: dict[str, dict[str, Any]] = {}
    workers = min(4, len(symbols))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_best_quote, sym): sym for sym in symbols}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                out[sym] = fut.result()
            except Exception:
                continue
    return [out[s] for s in symbols if s in out]


def _polygon_movers(kind: Literal["gainers", "losers"], limit: int) -> list[dict[str, Any]]:
    url = f"{POLYGON_BASE}/v2/snapshot/locale/us/markets/stocks/{kind}"
    body = _http_json("GET", url, params={"apiKey": POLYGON_KEY})
    status = str(body.get("status") or "").upper()
    if status in ("NOT_AUTHORIZED", "ERROR", "NOT_FOUND"):
        raise RuntimeError(body.get("message") or status or "polygon movers failed")
    out: list[dict[str, Any]] = []
    for t in (body.get("tickers") or [])[:limit]:
        sym = str(t.get("ticker") or "").upper()
        if not sym:
            continue
        day = t.get("day") or {}
        last = t.get("lastTrade") or {}
        minute = t.get("min") or {}
        price = _clean(last.get("p")) or _clean(minute.get("c")) or _clean(day.get("c"))
        if price is None:
            continue
        out.append(
            {
                "ticker": sym,
                "symbol": sym,
                "name": sym,
                "exchange": None,
                "price": price,
                "change_pct": _clean(t.get("todaysChangePerc")),
                "change": _clean(t.get("todaysChange")),
                "open": _clean(day.get("o")),
                "high": _clean(day.get("h")),
                "low": _clean(day.get("l")),
                "volume": _clean(day.get("v")),
                "source": "polygon",
                "delay": "realtime",
                "as_of": int(time.time()),
            }
        )
    return out


def _yahoo_movers(kind: Literal["gainers", "losers", "active"], limit: int) -> list[dict[str, Any]]:
    scr = {"gainers": "day_gainers", "losers": "day_losers", "active": "most_actives"}[kind]
    kwargs: dict[str, Any] = {"count": max(limit, 5)}
    sess = _yf_session()
    if sess is not None:
        kwargs["session"] = sess
    raw = yf.screen(scr, **kwargs)
    quotes = raw.get("quotes") if isinstance(raw, dict) else None
    out: list[dict[str, Any]] = []
    for q in quotes or []:
        if not isinstance(q, dict):
            continue
        sym = str(q.get("symbol") or "").upper()
        if not sym:
            continue
        row = _make_quote(
            sym,
            price=q.get("regularMarketPrice"),
            prev=q.get("regularMarketPreviousClose"),
            open_=q.get("regularMarketOpen"),
            high=q.get("regularMarketDayHigh"),
            low=q.get("regularMarketDayLow"),
            volume=q.get("regularMarketVolume"),
            market_cap=q.get("marketCap"),
            name=q.get("shortName") or q.get("longName") or q.get("displayName") or sym,
            source="yfinance",
            delay="yahoo",
        )
        if row["change_pct"] is None:
            row["change_pct"] = _clean(q.get("regularMarketChangePercent"))
        if row["change"] is None:
            row["change"] = _clean(q.get("regularMarketChange"))
        out.append(row)
        if len(out) >= limit:
            break
    return out


def _fetch_movers_items(kind: Literal["gainers", "losers", "active"], limit: int = 15) -> list[dict[str, Any]]:
    limit = max(5, min(limit, 40))
    order = {
        "gainers": ("change", False),
        "losers": ("change", True),
        "active": ("volume", False),
    }[kind]
    errors: list[str] = []
    try:
        df = _tv_query(
            extra_where=[
                col("type") == "stock",
                col("is_primary") == True,  # noqa: E712
                col("exchange").isin(["NASDAQ", "NYSE", "AMEX"]),
                col("market_cap_basic") > 1_000_000_000,
                col("volume") > 200_000,
                col("close") > 5,
            ],
            order=order,
            limit=limit,
        )
        if not df.empty:
            return [_row_to_quote(r) for _, r in df.iterrows()]
        errors.append("tradingview: empty")
    except Exception as e:
        errors.append(f"tradingview: {e}")

    if POLYGON_KEY and kind in ("gainers", "losers"):
        try:
            items = _polygon_movers(kind, limit)
            if items:
                return items
            errors.append("polygon: empty")
        except Exception as e:
            errors.append(f"polygon: {e}")

    try:
        items = _yahoo_movers(kind, limit)
        if items:
            return items
        errors.append("yahoo: empty")
    except Exception as e:
        errors.append(f"yahoo: {e}")

    raise RuntimeError("; ".join(errors) if errors else "Movers unavailable")


@app.get("/api/health")
def health():
    llm = llm_advice.llm_configured()
    return {
        "ok": True,
        "polygon": bool(POLYGON_KEY),
        "llm": llm,
        "sources": {
            "quotes": (
                "Polygon/Massive realtime"
                if POLYGON_KEY
                else "TradingView scanner (~15m delay) with Yahoo fallback"
            ),
            "charts": "Yahoo Finance / yfinance",
            "news": "Yahoo Finance",
        },
        "market": _us_equity_session_info(),
    }


@app.get("/api/network-test")
def network_test():
    """Probe outbound HTTPS with multiple clients (for Mac networking diagnostics)."""
    probe_url = "https://query1.finance.yahoo.com/v8/finance/chart/AAPL?interval=1d&range=1d"
    results: dict[str, str] = {}
    body = None
    for label, fn in (
        ("httpx", lambda: _httpx_fetch("GET", probe_url, timeout=8)),
        ("requests", lambda: _requests_fetch("GET", probe_url, timeout=8)),
        ("curl", lambda: _curl_fetch("GET", probe_url, timeout=8)),
        ("curl6", lambda: _curl_fetch("GET", probe_url, timeout=8, ip_version=6)),
        ("curl4", lambda: _curl_fetch("GET", probe_url, timeout=8, ip_version=4)),
    ):
        try:
            text = fn()
            body = json.loads(text)
            meta = ((body.get("chart") or {}).get("result") or [{}])[0].get("meta") or {}
            price = meta.get("regularMarketPrice") or meta.get("previousClose")
            results[label] = f"ok price={price}"
        except Exception as e:
            results[label] = str(e)
    return {"probe": probe_url, "results": results, "any_ok": any(v.startswith("ok") for v in results.values())}


@app.get("/api/indices")
def indices():
    def fetch():
        return _enrich_quotes_session(_best_quotes(list(INDEX_TICKERS)))

    try:
        return {"items": _cached(f"indices:{_us_equity_session()}", 15, fetch)}
    except Exception as e:
        raise HTTPException(502, f"Indices failed: {e}") from e


@app.get("/api/quote/{symbol}")
def quote(symbol: str):
    def fetch():
        return _enrich_quote_session(_best_quote(symbol))

    try:
        return _cached(f"quote:{symbol.upper()}:{_us_equity_session()}", _live_cache_ttl(), fetch)
    except Exception as e:
        raise HTTPException(502, f"Quote failed: {e}") from e


@app.get("/api/quotes")
def quotes(symbols: str = Q(..., description="Comma-separated tickers")):
    raw = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not raw:
        raise HTTPException(400, "No symbols")
    def fetch():
        return _overlay_tv_fields(_enrich_quotes_session(_best_quotes(raw[:40])))

    try:
        return {
            "items": _cached(
                "quotes:" + ",".join(sorted(raw[:40])) + f":{_us_equity_session()}",
                8,
                fetch,
            )
        }
    except Exception as e:
        raise HTTPException(502, f"Quotes failed: {e}") from e


@app.get("/api/movers")
def movers(kind: Literal["gainers", "losers", "active"] = "gainers", limit: int = 15):
    limit = max(5, min(limit, 40))

    def fetch():
        return _fetch_movers_items(kind, limit)

    try:
        items = _cached(f"movers:{kind}:{limit}", 20, fetch)
        return {"kind": kind, "items": items}
    except Exception as e:
        return {"kind": kind, "items": [], "error": str(e)}


@app.get("/api/search")
def search(q: str, limit: int = 12):
    q = q.strip()
    if len(q) < 1:
        return {"items": []}
    limit = max(1, min(limit, 25))

    def fetch():
        needle = q.upper()
        base = (
            Query()
            .select("name", "description", "exchange", "type", "close", "change", "market_cap_basic")
            .set_markets("america")
            .limit(limit)
        )
        _, df = _tv_get_scanner_data(
            base.where(
                col("type").isin(["stock", "fund", "etf"]),
                col("is_primary") == True,  # noqa: E712
                col("name") == needle,
            )
        )
        if df is None or df.empty:
            _, df = _tv_get_scanner_data(
                Query()
                .select("name", "description", "exchange", "type", "close", "change", "market_cap_basic")
                .set_markets("america")
                .where(
                    col("type").isin(["stock", "fund", "etf"]),
                    col("is_primary") == True,  # noqa: E712
                    col("name").like(f"%{needle}%"),
                )
                .limit(limit)
            )
        if df is None or df.empty:
            return []
        items = []
        for _, r in df.iterrows():
            items.append(
                {
                    "ticker": r.get("ticker"),
                    "symbol": r.get("name"),
                    "name": r.get("description"),
                    "exchange": r.get("exchange"),
                    "type": r.get("type"),
                    "price": _clean(r.get("close")),
                    "change_pct": _clean(r.get("change")),
                }
            )
        return items

    try:
        return {"items": _cached(f"search:{q.lower()}:{limit}", 30, fetch)}
    except Exception as e:
        raise HTTPException(502, f"Search failed: {e}") from e


@app.get("/api/history/{symbol}")
def history(symbol: str, range: str = "6mo"):
    if range not in RANGE_TO_YF:
        raise HTTPException(400, f"range must be one of {list(RANGE_TO_YF)}")
    yf_sym = symbol.strip().upper().split(":")[-1]
    cache_sym = "^VIX" if yf_sym == "VIX" else yf_sym

    def fetch():
        return _yfinance_history_bars(symbol, range)

    fresh = _chart_cache_ttl()
    session = _us_equity_session()
    ttl = {"1d": fresh, "5d": fresh, "1mo": max(fresh, _chart_refresh_sec() * 0.8)}.get(
        range, max(15.0, _chart_refresh_sec())
    )
    try:
        tag = "ext" if session != "rth" and range in ("1d", "5d", "1mo") else "rth"
        return _cached(f"hist:{cache_sym}:{range}:{tag}:{_refresh_bucket()}", ttl, fetch)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"History failed: {e}") from e


@app.get("/api/profile/{symbol}")
def profile(symbol: str):
    yf_sym = symbol.strip().upper().split(":")[-1]

    def fetch():
        t = _yf_ticker(yf_sym)
        info = t.info or {}
        keys = [
            "longName",
            "shortName",
            "sector",
            "industry",
            "website",
            "fullTimeEmployees",
            "longBusinessSummary",
            "city",
            "state",
            "country",
            "marketCap",
            "enterpriseValue",
            "trailingPE",
            "forwardPE",
            "pegRatio",
            "priceToBook",
            "profitMargins",
            "operatingMargins",
            "returnOnEquity",
            "returnOnAssets",
            "revenueGrowth",
            "earningsGrowth",
            "grossMargins",
            "ebitdaMargins",
            "currentRatio",
            "debtToEquity",
            "freeCashflow",
            "totalCash",
            "totalDebt",
            "trailingEps",
            "forwardEps",
            "dividendYield",
            "payoutRatio",
            "beta",
            "fiftyTwoWeekHigh",
            "fiftyTwoWeekLow",
            "averageVolume",
            "sharesOutstanding",
            "floatShares",
            "sharesShort",
            "shortPercentOfFloat",
            "shortRatio",
            "sharesShortPriorMonth",
            "dateShortInterest",
            "heldPercentInsiders",
            "heldPercentInstitutions",
            "targetMeanPrice",
            "targetHighPrice",
            "targetLowPrice",
            "numberOfAnalystOpinions",
            "recommendationKey",
            "earningsTimestamp",
            "earningsTimestampStart",
            "earningsTimestampEnd",
        ]
        out = {k: _clean(info.get(k)) for k in keys}
        out["symbol"] = yf_sym
        out["source"] = "yfinance"
        out["earnings_at"] = _next_earnings_unix(info)
        return out

    try:
        return _cached(f"profile:{yf_sym}:si", 300, fetch)
    except Exception as e:
        raise HTTPException(502, f"Profile failed: {e}") from e


@app.get("/api/market-news")
def market_news(limit: int = 24):
    limit = max(5, min(limit, 36))

    def fetch():
        def pull(url: str) -> str:
            return _httpx_fetch("GET", url, timeout=8)

        items = newsfeed.market_news(pull, ticker_fn=_yf_ticker, limit=limit)
        return {"source": "yahoo-rss", "items": items}

    try:
        return _cached(f"market-news:{limit}", 20, fetch)
    except Exception as e:
        raise HTTPException(502, f"Market news failed: {e}") from e


@app.get("/api/news/{symbol}")
def news(symbol: str, limit: int = 12):
    yf_sym = symbol.strip().upper().split(":")[-1]
    limit = max(1, min(limit, 25))

    def fetch():
        t = _yf_ticker(yf_sym)
        items = newsfeed.ticker_news(t, limit=limit)
        return {"symbol": yf_sym, "source": "yfinance", "items": items}

    try:
        return _cached(f"news:{yf_sym}", 20, fetch)
    except Exception as e:
        raise HTTPException(502, f"News failed: {e}") from e


@app.get("/api/fundamentals/{symbol}")
def fundamentals(symbol: str):
    yf_sym = symbol.strip().upper().split(":")[-1]

    def fetch():
        t = _yf_ticker(yf_sym)
        info: dict[str, Any] = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        return fundamentals_mod.build_fundamentals(yf_sym, t, info, _clean, _next_earnings_unix)

    try:
        return _cached(f"fundamentals:{yf_sym}:eh", 900, fetch)
    except Exception as e:
        raise HTTPException(502, f"Fundamentals failed: {e}") from e


@app.get("/api/ownership/{symbol}")
def ownership(symbol: str):
    yf_sym = symbol.strip().upper().split(":")[-1]

    def fetch():
        t = _yf_ticker(yf_sym)
        info: dict[str, Any] = {}
        try:
            info = t.info or {}
        except Exception:
            info = {}
        return ownership_mod.build_ownership(yf_sym, t, info, _clean)

    try:
        return _cached(f"ownership:{yf_sym}", 900, fetch)
    except Exception as e:
        raise HTTPException(502, f"Ownership failed: {e}") from e


def _symbol_tv_sector(symbol: str) -> str | None:
    try:
        tv = resolve_tv_ticker(symbol)
        df = _tv_query(tickers=[tv], limit=1)
        if df is not None and not df.empty:
            sector = _clean(df.iloc[0].get("sector"))
            if sector:
                return str(sector)
    except Exception:
        return None
    return None


@app.get("/api/peers/{symbol}")
def peers(symbol: str, limit: int = 5):
    yf_sym = symbol.strip().upper().split(":")[-1]
    limit = max(3, min(limit, 8))

    def fetch():
        sector = _symbol_tv_sector(yf_sym)
        if not sector:
            return {"symbol": yf_sym, "sector": None, "items": [], "source": "tradingview-screener"}
        df = _tv_query(
            extra_where=_us_stock_filters(
                [
                    col("sector") == sector,
                    col("market_cap_basic") > 500_000_000,
                ]
            ),
            order=("market_cap_basic", False),
            limit=limit + 10,
            extra_cols=TV_EXTRA_COLS,
        )
        items: list[dict[str, Any]] = []
        self_row: dict[str, Any] | None = None
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                q = _row_to_quote(row)
                if str(q.get("symbol") or "").upper() == yf_sym:
                    self_row = q
                    continue
                items.append(q)
                if len(items) >= limit:
                    break
        if self_row:
            items = [self_row, *items]
        return {"symbol": yf_sym, "sector": sector, "items": items, "source": "tradingview-screener"}

    try:
        return _cached(f"peers:{yf_sym}:{limit}", 180, fetch)
    except Exception as e:
        raise HTTPException(502, f"Peers failed: {e}") from e


@app.get("/api/screener")
def screener(
    sector: str | None = None,
    cap_min: float | None = None,
    pe_max: float | None = None,
    rsi_min: float | None = None,
    rsi_max: float | None = None,
    change_min: float | None = None,
    change_max: float | None = None,
    order: Literal["change", "volume", "market_cap"] = "change",
    limit: int = 20,
):
    limit = max(5, min(limit, 40))
    sector = (sector or "").strip() or None
    order_col = {"change": "change", "volume": "volume", "market_cap": "market_cap_basic"}[order]

    def fetch():
        wheres = _us_stock_filters([col("close") > 5, col("volume") > 100_000])
        if sector:
            wheres.append(col("sector") == sector)
        if cap_min:
            wheres.append(col("market_cap_basic") > cap_min)
        if pe_max:
            wheres.append(col("price_earnings_ttm") > 0)
            wheres.append(col("price_earnings_ttm") < pe_max)
        if rsi_min is not None:
            wheres.append(col("RSI") >= rsi_min)
        if rsi_max is not None:
            wheres.append(col("RSI") <= rsi_max)
        if change_min is not None:
            wheres.append(col("change") >= change_min)
        if change_max is not None:
            wheres.append(col("change") <= change_max)
        df = _tv_query(
            extra_where=wheres,
            order=(order_col, False),
            limit=limit,
            extra_cols=TV_EXTRA_COLS,
        )
        items = [_row_to_quote(r) for _, r in df.iterrows()] if df is not None and not df.empty else []
        return {"items": items, "source": "tradingview-screener", "sectors": TV_SECTORS}

    cache_key = (
        f"screen:{sector}:{cap_min}:{pe_max}:{rsi_min}:{rsi_max}:{change_min}:{change_max}:{order}:{limit}"
    )
    try:
        return _cached(cache_key, 45, fetch)
    except Exception as e:
        raise HTTPException(502, f"Screener failed: {e}") from e


@app.get("/api/ta/{symbol}")
def ta(symbol: str, interval: str = "1d"):
    tv = resolve_tv_ticker(symbol)
    exchange, name = tv.split(":", 1)
    interval_map = {
        "15m": Interval.INTERVAL_15_MINUTES,
        "1h": Interval.INTERVAL_1_HOUR,
        "4h": Interval.INTERVAL_4_HOURS,
        "1d": Interval.INTERVAL_1_DAY,
        "1w": Interval.INTERVAL_1_WEEK,
    }
    iv = interval_map.get(interval)
    if not iv:
        raise HTTPException(400, f"interval must be one of {list(interval_map)}")

    def fetch():
        handler = TA_Handler(
            symbol=name,
            screener="america",
            exchange=exchange,
            interval=iv,
        )
        a = handler.get_analysis()
        return {
            "symbol": name,
            "exchange": exchange,
            "interval": interval,
            "summary": a.summary,
            "oscillators": a.oscillators,
            "moving_averages": a.moving_averages,
            "indicators": {k: _clean(v) for k, v in (a.indicators or {}).items()},
            "source": "tradingview-ta",
        }

    try:
        return _cached(f"ta:{tv}:{interval}", 30, fetch)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"TA failed: {e}") from e


def _congress_block(symbol: str) -> dict[str, Any]:
    try:
        return congress_ptr.query(symbol)
    except Exception:
        return {
            "buy_count": 0,
            "sell_count": 0,
            "tilt": "neutral",
            "items": [],
            "source": "House Clerk + Senate eFD",
            "status": "error",
        }


def _news_items(t: yf.Ticker, limit: int = 8) -> list[dict[str, Any]]:
    return newsfeed.ticker_news(t, limit=limit)


def _insider_block(t: yf.Ticker) -> dict[str, Any]:
    rows = []
    net = 0.0
    try:
        df = t.insider_transactions
        if df is not None and not df.empty:
            for _, r in df.head(12).iterrows():
                text = str(r.get("Text") or r.get("Transaction") or "")
                shares = _clean(r.get("Shares"))
                value = _clean(r.get("Value")) or 0
                is_buy = "purchase" in text.lower() or "buy" in text.lower()
                is_sell = "sale" in text.lower() or "sell" in text.lower()
                signed = (value or 0) if is_buy else -(value or 0) if is_sell else 0
                net += signed or 0
                start = r.get("Start Date")
                rows.append(
                    {
                        "date": str(start)[:10] if start is not None else None,
                        "insider": _clean(r.get("Insider")) or _clean(r.get("Name")),
                        "title": _clean(r.get("Position")) or _clean(r.get("Title")),
                        "text": text,
                        "shares": shares,
                        "value": _clean(r.get("Value")),
                    }
                )
    except Exception:
        pass
    tilt = "buy" if net > 0 else "sell" if net < 0 else "neutral"
    return {"net_value": net, "tilt": tilt, "items": rows, "source": "yfinance"}


def _empty_options(error: str | None = None) -> dict[str, Any]:
    return {
        "expiry": None,
        "call_volume": 0,
        "put_volume": 0,
        "put_call": None,
        "items": [],
        "source": "yfinance",
        "error": error,
    }


def _options_from_ticker(t: yf.Ticker) -> dict[str, Any]:
    try:
        expiries = list(t.options or [])
    except Exception as e:
        return _empty_options(f"Yahoo options list failed: {e}")
    if not expiries:
        return _empty_options("Yahoo returned no option expirations")

    def pack(df: pd.DataFrame | None, side: str, expiry: str) -> tuple[int, list[dict[str, Any]]]:
        if df is None or getattr(df, "empty", True):
            return 0, []
        work = df.copy()
        work["volume"] = pd.to_numeric(work.get("volume"), errors="coerce").fillna(0)
        work["openInterest"] = pd.to_numeric(work.get("openInterest"), errors="coerce").fillna(0)
        work["ratio"] = work["volume"] / work["openInterest"].clip(lower=1)
        vol = int(work["volume"].sum())
        notable = work[work["volume"] >= 50].sort_values("volume", ascending=False).head(8)
        if notable.empty:
            notable = work.sort_values("volume", ascending=False).head(8)
        out = []
        for _, r in notable.iterrows():
            out.append(
                {
                    "side": side,
                    "expiry": expiry,
                    "contract": _clean(r.get("contractSymbol")),
                    "strike": _clean(r.get("strike")),
                    "last": _clean(r.get("lastPrice")),
                    "volume": _clean(r.get("volume")),
                    "open_interest": _clean(r.get("openInterest")),
                    "iv": _clean(r.get("impliedVolatility")),
                    "vol_oi": _clean(r.get("ratio")),
                }
            )
        return vol, out

    call_vol = 0
    put_vol = 0
    items: list[dict[str, Any]] = []
    chain_errors: list[str] = []
    used_expiry = expiries[0]
    for expiry in expiries[:3]:
        try:
            chain = t.option_chain(expiry)
        except Exception as e:
            chain_errors.append(f"{expiry}: {e}")
            continue
        c_vol, calls = pack(getattr(chain, "calls", None), "call", expiry)
        p_vol, puts = pack(getattr(chain, "puts", None), "put", expiry)
        call_vol += c_vol
        put_vol += p_vol
        items.extend(calls + puts)
        if expiry == expiries[0]:
            used_expiry = expiry
    if not items and call_vol == 0 and put_vol == 0:
        detail = "; ".join(chain_errors) if chain_errors else "nearest chains had no contracts"
        return _empty_options(f"Yahoo option chain empty ({detail})")
    pc = (put_vol / call_vol) if call_vol else None
    items = sorted(items, key=lambda x: x.get("volume") or 0, reverse=True)[:10]
    return {
        "expiry": used_expiry,
        "call_volume": call_vol,
        "put_volume": put_vol,
        "put_call": pc,
        "items": items,
        "source": "yfinance",
    }


def _options_block(t: yf.Ticker | str) -> dict[str, Any]:
    """Yahoo option chain. Always uses a dedicated Ticker — sharing one across threads
    races yfinance's session and often returns an empty chain (looks like 'no volume')."""
    symbol = (t if isinstance(t, str) else getattr(t, "ticker", None) or str(t)).strip().upper().split(":")[-1]
    last = _empty_options("Yahoo options unavailable")
    for attempt in range(3):
        last = _options_from_ticker(_yf_ticker(symbol))
        if last.get("expiry") or last.get("items") or last.get("call_volume") or last.get("put_volume"):
            return last
        if attempt < 2:
            time.sleep(0.4)
    return last


def _forecast_block(info: dict[str, Any], price: float | None) -> dict[str, Any]:
    mean = _clean(info.get("targetMeanPrice"))
    high = _clean(info.get("targetHighPrice"))
    low = _clean(info.get("targetLowPrice"))
    n = _clean(info.get("numberOfAnalystOpinions"))
    rec = _clean(info.get("recommendationKey"))
    upside = None
    if mean and price:
        upside = (mean - price) / price * 100
    return {
        "target_mean": mean,
        "target_high": high,
        "target_low": low,
        "analysts": n,
        "recommendation": rec,
        "upside_pct": upside,
        "source": "yfinance",
    }


def _suggest(ta_label: str | None, rsi: float | None, insiders: dict, options: dict, congress: dict, forecast: dict) -> dict[str, Any]:
    score = 50
    reasons: list[str] = []
    label = (ta_label or "").upper()
    if "STRONG BUY" in label:
        score += 18
        reasons.append("TradingView daily rating is Strong Buy")
    elif "BUY" in label:
        score += 10
        reasons.append("TradingView daily rating is Buy")
    elif "STRONG SELL" in label:
        score -= 18
        reasons.append("TradingView daily rating is Strong Sell")
    elif "SELL" in label:
        score -= 10
        reasons.append("TradingView daily rating is Sell")

    if rsi is not None:
        if rsi < 30:
            score += 6
            reasons.append(f"RSI {rsi:.0f} is oversold")
        elif rsi > 70:
            score -= 6
            reasons.append(f"RSI {rsi:.0f} is overbought")

    if insiders.get("tilt") == "buy":
        score += 8
        reasons.append("Recent Form 4 flow nets to insider buying")
    elif insiders.get("tilt") == "sell":
        score -= 8
        reasons.append("Recent Form 4 flow nets to insider selling")

    pc = options.get("put_call")
    if pc is not None:
        if pc < 0.7:
            score += 6
            reasons.append(f"Near-term put/call volume {pc:.2f} is call-heavy")
        elif pc > 1.3:
            score -= 6
            reasons.append(f"Near-term put/call volume {pc:.2f} is put-heavy")

    if congress.get("tilt") == "buy":
        score += 4
        reasons.append("More Senate/House disclosures are purchases than sales")
    elif congress.get("tilt") == "sell":
        score -= 4
        reasons.append("More Senate/House disclosures are sales than purchases")

    upside = forecast.get("upside_pct")
    if upside is not None:
        if upside >= 15:
            score += 10
            reasons.append(f"Analyst mean target implies {upside:.0f}% upside")
        elif upside <= -10:
            score -= 10
            reasons.append(f"Analyst mean target implies {upside:.0f}% downside")

    rec = str(forecast.get("recommendation") or "").lower()
    if rec in ("buy", "strong_buy"):
        score += 6
        reasons.append(f"Street consensus is {rec.replace('_', ' ')}")
    elif rec in ("sell", "strong_sell", "underperform"):
        score -= 6
        reasons.append(f"Street consensus is {rec.replace('_', ' ')}")

    score = max(0, min(100, int(round(score))))
    if score >= 72:
        action = "ACCUMULATE"
    elif score >= 58:
        action = "LEAN LONG"
    elif score >= 45:
        action = "HOLD"
    elif score >= 32:
        action = "REDUCE"
    else:
        action = "AVOID"
    if not reasons:
        reasons.append("Insufficient overlapping signals; defaulting to a mid score")
    return {
        "action": action,
        "score": score,
        "reasons": reasons[:6],
        "disclaimer": "Heuristic research readout, not financial advice. You can lose money.",
    }


def _macro_context() -> dict[str, Any]:
    macro: dict[str, Any] = {}
    for sym in ("SPY", "QQQ", "DIA", "IWM", "VIX"):
        try:
            q = _best_quote(sym)
            macro[sym] = {
                "name": q.get("name"),
                "price": q.get("price"),
                "change_pct": q.get("change_pct"),
                "change": q.get("change"),
                "rsi": q.get("rsi"),
                "recommend_label": q.get("recommend_label"),
                "volume": q.get("volume"),
            }
        except Exception as e:
            macro[sym] = {"error": str(e)}
    return macro


def _build_llm_context(yf_sym: str) -> dict[str, Any]:
    quote = _best_quote(yf_sym)
    t = _yf_ticker(yf_sym)
    info: dict[str, Any] = {}
    try:
        info = t.info or {}
    except Exception:
        pass

    profile_keys = [
        "sector",
        "industry",
        "marketCap",
        "trailingPE",
        "forwardPE",
        "pegRatio",
        "priceToBook",
        "profitMargins",
        "revenueGrowth",
        "earningsGrowth",
        "beta",
        "fiftyTwoWeekHigh",
        "fiftyTwoWeekLow",
        "targetMeanPrice",
        "recommendationKey",
        "numberOfAnalystOpinions",
    ]
    profile = {k: _clean(info.get(k)) for k in profile_keys}
    summary = _clean(info.get("longBusinessSummary"))
    if isinstance(summary, str):
        profile["summary"] = summary[:600]

    ta_block: dict[str, Any] = {}
    try:
        tv = resolve_tv_ticker(yf_sym)
        exchange, name = tv.split(":", 1)
        handler = TA_Handler(
            symbol=name,
            screener="america",
            exchange=exchange,
            interval=Interval.INTERVAL_1_DAY,
        )
        analysis = handler.get_analysis()
        ta_block = {
            "summary": analysis.summary,
            "indicators": {
                k: _clean(v)
                for k, v in (analysis.indicators or {}).items()
                if k in ("RSI", "MACD.macd", "MACD.signal", "close", "open", "volume")
            },
        }
    except Exception as e:
        ta_block = {"error": str(e), "label": quote.get("recommend_label")}

    insiders = _insider_block(t)
    options = _options_block(yf_sym)
    congress = _congress_block(yf_sym)
    news = _news_items(t, 8)
    price = quote.get("price")
    forecast = _forecast_block(info, price if isinstance(price, (int, float)) else None)

    recent_bars: list[dict[str, Any]] = []
    try:
        hist = t.history(period="1mo", interval="1d")
        if hist is not None and not hist.empty:
            tail = hist.tail(10)
            for idx, row in tail.iterrows():
                recent_bars.append(
                    {
                        "date": str(idx.date()) if hasattr(idx, "date") else str(idx),
                        "close": _clean(row.get("Close")),
                        "volume": _clean(row.get("Volume")),
                    }
                )
    except Exception:
        pass

    return {
        "symbol": yf_sym,
        "as_of": int(time.time()),
        "quote": quote,
        "profile": profile,
        "technicals": ta_block,
        "insiders": {
            "net_value": insiders.get("net_value"),
            "tilt": insiders.get("tilt"),
            "recent": insiders.get("items", [])[:5],
        },
        "options": {
            "put_call": options.get("put_call"),
            "call_volume": options.get("call_volume"),
            "put_volume": options.get("put_volume"),
            "notable": options.get("items", [])[:6],
            "error": options.get("error"),
        },
        "congress": {
            "buy_count": congress.get("buy_count"),
            "sell_count": congress.get("sell_count"),
            "tilt": congress.get("tilt"),
            "source": congress.get("source"),
            "filed_through": congress.get("filed_through"),
            "recent": congress.get("items", [])[:5],
        },
        "forecast": forecast,
        "news_headlines": [{"title": n.get("title"), "publisher": n.get("publisher")} for n in news[:6]],
        "recent_daily_bars": recent_bars,
        "macro_market": _macro_context(),
    }


@app.post("/api/llm-advice/{symbol}")
def llm_advice_route(symbol: str):
    yf_sym = symbol.strip().upper().split(":")[-1]
    if not llm_advice.llm_configured()["any"]:
        raise HTTPException(
            503,
            "LLM not configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env",
        )
    try:
        ctx = _build_llm_context(yf_sym)
        return llm_advice.start_research_conversation(yf_sym, ctx)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"LLM advice failed: {e}") from e


class LlmChatBody(BaseModel):
    conversation_id: str = Field(min_length=1)
    message: str = Field(min_length=1, max_length=4000)


@app.post("/api/llm-advice/{symbol}/chat")
def llm_advice_chat_route(symbol: str, body: LlmChatBody):
    yf_sym = symbol.strip().upper().split(":")[-1]
    if not llm_advice.llm_configured()["any"]:
        raise HTTPException(
            503,
            "LLM not configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env",
        )
    try:
        return llm_advice.follow_up_research_conversation(body.conversation_id, yf_sym, body.message)
    except KeyError:
        raise HTTPException(404, "Conversation not found. Generate a suggestion first.") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"LLM follow-up failed: {e}") from e


@app.get("/api/deep/{symbol}")
def deep(symbol: str):
    yf_sym = symbol.strip().upper().split(":")[-1]
    if yf_sym in {"SPY", "QQQ", "DIA", "IWM", "VIX"}:
        # still run; ETFs have thinner insider/congress hits
        pass

    def fetch():
        def yahoo_bundle():
            t = _yf_ticker(yf_sym)
            try:
                info = t.info or {}
            except Exception:
                info = {}
            return info, _insider_block(t), _options_block(yf_sym)

        def safe_quote():
            try:
                return _best_quote(yf_sym)
            except Exception:
                return {}

        def safe_ta():
            try:
                return ta(yf_sym, "1d")
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=4) as pool:
            yf_f = pool.submit(yahoo_bundle)
            quote_f = pool.submit(safe_quote)
            congress_f = pool.submit(_congress_block, yf_sym)
            ta_f = pool.submit(safe_ta)
            info, insiders, options = yf_f.result()
            quote = quote_f.result()
            congress = congress_f.result()
            ta_data = ta_f.result()

        price = quote.get("price")
        forecast = _forecast_block(info, price if isinstance(price, (int, float)) else None)
        ta_label = quote.get("recommend_label")
        rsi = quote.get("rsi")
        if ta_data:
            ta_label = (ta_data.get("summary") or {}).get("RECOMMENDATION") or ta_label
            rsi = _clean((ta_data.get("indicators") or {}).get("RSI")) or rsi
        suggestion = _suggest(
            ta_label,
            rsi if isinstance(rsi, (int, float)) else quote.get("rsi"),
            insiders,
            options,
            congress,
            forecast,
        )
        return {
            "symbol": yf_sym,
            "price": price,
            "name": quote.get("name") or info.get("shortName") or yf_sym,
            "insiders": insiders,
            "options": options,
            "congress": congress,
            "forecast": forecast,
            "ta": {"label": ta_label, "rsi": quote.get("rsi") or rsi},
            "suggestion": suggestion,
            "as_of": int(time.time()),
        }

    try:
        now = time.time()
        hit = _cache.get(f"deep:{yf_sym}")
        ttl = 90.0
        if hit:
            prev = hit[1] if isinstance(hit[1], dict) else {}
            opt = prev.get("options") or {}
            cong = prev.get("congress") or {}
            opt_ok = bool(opt.get("expiry") or opt.get("items"))
            cong_pending = cong.get("status") == "refreshing"
            ttl = 8.0 if (not opt_ok or cong_pending) else 90.0
            if now - hit[0] < ttl:
                return hit[1]
        value = fetch()
        _cache[f"deep:{yf_sym}"] = (now, value)
        return value
    except Exception as e:
        raise HTTPException(502, f"Deep analysis failed: {e}") from e


@app.get("/api/snapshot")
def snapshot():
    """One payload for the dashboard: indices + three mover boards."""

    def fetch():
        payload: dict[str, Any] = {
            "indices": [],
            "gainers": [],
            "losers": [],
            "active": [],
            "as_of": int(time.time()),
            "errors": {},
        }
        try:
            payload["indices"] = _cached(
                f"indices:{_us_equity_session()}",
                15,
                lambda: _enrich_quotes_session(_best_quotes(list(INDEX_TICKERS))),
            )
        except Exception as e:
            payload["errors"]["indices"] = str(e)
        for kind in ("gainers", "losers", "active"):
            try:
                payload[kind] = _cached(
                    f"movers:{kind}:15",
                    20,
                    lambda k=kind: _fetch_movers_items(k, 15),
                )
            except Exception as e:
                payload["errors"][kind] = str(e)
        return payload

    try:
        return _cached("snapshot", 15, fetch)
    except Exception as e:
        raise HTTPException(502, f"Snapshot failed: {e}") from e


import portfolios as portfolio_mod


def _session_quote(symbol: str) -> dict[str, Any]:
    return _enrich_quote_session(_best_quote(symbol))


def _session_quotes(symbols: list[str]) -> list[dict[str, Any]]:
    return _enrich_quotes_session(_best_quotes(symbols))


portfolio_mod.configure(
    quote=_session_quote,
    quotes=_session_quotes,
    history=_yfinance_history_bars,
    movers=_fetch_movers_items,
    session=_us_equity_session,
    extended_marks=_yahoo_extended_marks,
)
app.include_router(portfolio_mod.router)


def _vibe_research_pack(pid: str) -> dict[str, Any]:
    fund = portfolio_mod.get_portfolio(pid, live=True)

    def news_fn(symbol: str) -> list[dict[str, Any]]:
        return _news_items(_yf_ticker(symbol), 4)

    def ta_fn(symbol: str) -> dict[str, Any]:
        try:
            return ta(symbol, "1d")
        except Exception:
            return {}

    return vibe_portfolio.build_research(
        fund,
        quote_fn=_session_quote,
        ta_fn=ta_fn,
        news_fn=news_fn,
        macro_fn=_macro_context,
    )


@app.post("/api/portfolios/{pid}/vibe")
def vibe_portfolio_review(pid: str):
    """Start a Vibe-style paper-fund conversation (Yahoo + daily TA, then LLM)."""
    if not llm_advice.llm_configured()["any"]:
        raise HTTPException(
            503,
            "LLM not configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env",
        )
    try:
        research = _vibe_research_pack(pid)
        started = llm_advice.start_portfolio_conversation(pid, research)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Vibe review failed: {e}") from e
    return {
        "portfolio_id": pid,
        "conversation_id": started.get("conversation_id"),
        "engine": "llm",
        "advice": started.get("advice"),
        "messages": started.get("messages"),
        "research": {
            "as_of": research.get("as_of"),
            "stack": research.get("stack"),
            "cash_weight_pct": research.get("cash_weight_pct"),
            "top_weight_pct": research.get("top_weight_pct"),
            "holdings": research.get("holdings"),
            "fund": research.get("fund"),
        },
        "llm": llm_advice.llm_configured(),
    }


@app.post("/api/portfolios/{pid}/vibe/chat")
def vibe_portfolio_chat(pid: str, body: LlmChatBody):
    if not llm_advice.llm_configured()["any"]:
        raise HTTPException(
            503,
            "LLM not configured. Set OPENAI_API_KEY or ANTHROPIC_API_KEY in .env",
        )
    try:
        return llm_advice.follow_up_portfolio_conversation(body.conversation_id, pid, body.message)
    except KeyError:
        raise HTTPException(404, "Conversation not found. Analyze the fund first.") from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Vibe follow-up failed: {e}") from e
