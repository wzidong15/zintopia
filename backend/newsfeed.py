"""Yahoo ticker news and market-wide RSS tape, with heuristic alert flags."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable
from email.utils import parsedate_to_datetime

FetchFn = Callable[[str], str]

BREAKING_MARKERS = (
    "breaking",
    "just in",
    "flash:",
    "developing:",
    "alert:",
    "bulletin:",
)
SWAN_MARKERS = (
    "black swan",
    "circuit breaker",
    "flash crash",
    "trading halt",
    "all trading halted",
    "markets halted",
    "bank failure",
    "bank collapse",
    "emergency rate",
    "emergency cut",
    "emergency meeting",
    "martial law",
    "sovereign default",
    "market crash",
    "stocks crash",
    "systemic risk",
    "liquidity freeze",
    "liquidity crisis",
    "assassination",
    "nuclear strike",
    "invasion of",
)

RSS_MARKET = "https://finance.yahoo.com/news/rssindex"
RSS_SPX = "https://feeds.finance.yahoo.com/rss/2.0/headline?s=%5EGSPC&region=US&lang=en-US"


def _unix(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        n = float(v)
        if n > 1e12:
            n /= 1000.0
        return int(n) if n > 1e9 else None
    s = str(v).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        pass
    try:
        dt = parsedate_to_datetime(str(v))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return None


def classify_headline(title: str | None, published: int | None, now: int | None = None) -> str | None:
    """breaking = labeled in the title; blackswan = severe headline (heuristic, not a news desk)."""
    text = (title or "").strip().lower()
    now = now or int(datetime.now(timezone.utc).timestamp())
    age = (now - published) if published else None
    swan = any(m in text for m in SWAN_MARKERS)
    labeled = any(m in text for m in BREAKING_MARKERS)
    fresh = age is not None and 0 <= age <= 45 * 60
    if swan and (fresh or labeled or (age is not None and age <= 6 * 3600)):
        return "blackswan"
    if labeled:
        return "breaking"
    return None


def _pack(title: Any, url: Any, publisher: Any, published: Any, now: int) -> dict[str, Any] | None:
    t = str(title or "").strip()
    if not t:
        return None
    pub = _unix(published)
    return {
        "title": t,
        "url": str(url).strip() if url else None,
        "publisher": str(publisher).strip() if publisher else None,
        "published": pub,
        "kind": classify_headline(t, pub, now),
    }


def from_yahoo_item(raw: dict[str, Any], now: int | None = None) -> dict[str, Any] | None:
    content = raw.get("content") or raw
    title = content.get("title") or raw.get("title")
    pub = content.get("pubDate") or raw.get("providerPublishTime")
    click = content.get("clickThroughUrl") or {}
    url = click.get("url") if isinstance(click, dict) else None
    if not url:
        url = raw.get("link")
    canon = content.get("canonicalUrl")
    if not url and isinstance(canon, dict):
        url = canon.get("url")
    provider = (
        (content.get("provider") or {}).get("displayName")
        if isinstance(content.get("provider"), dict)
        else raw.get("publisher")
    )
    return _pack(title, url, provider, pub, now or int(datetime.now(timezone.utc).timestamp()))


def from_rss_xml(xml_text: str, now: int | None = None, limit: int = 40) -> list[dict[str, Any]]:
    now = now or int(datetime.now(timezone.utc).timestamp())
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[dict[str, Any]] = []
    for item in root.findall("./channel/item"):
        src = item.find("source")
        packed = _pack(
            item.findtext("title"),
            item.findtext("link"),
            src.text if src is not None else None,
            item.findtext("pubDate"),
            now,
        )
        if packed:
            out.append(packed)
        if len(out) >= limit:
            break
    return out


def _dedupe(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for it in items:
        key = (it.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    rank = {"blackswan": 0, "breaking": 1}
    out.sort(key=lambda r: (rank.get(r.get("kind") or "", 2), -(r.get("published") or 0)))
    return out


def ticker_news(ticker: Any, limit: int = 12) -> list[dict[str, Any]]:
    now = int(datetime.now(timezone.utc).timestamp())
    items: list[dict[str, Any]] = []
    try:
        raw = ticker.news or []
    except Exception:
        raw = []
    for n in raw:
        packed = from_yahoo_item(n, now)
        if packed:
            items.append(packed)
        if len(items) >= limit * 2:
            break
    return _dedupe(items)[:limit]


def market_news(fetch_text: FetchFn, ticker_fn: Callable[[str], Any] | None = None, limit: int = 16) -> list[dict[str, Any]]:
    now = int(datetime.now(timezone.utc).timestamp())
    items: list[dict[str, Any]] = []
    for url in (RSS_MARKET, RSS_SPX):
        try:
            items.extend(from_rss_xml(fetch_text(url), now, limit=40))
        except Exception:
            continue
    if len(items) < 6 and ticker_fn is not None:
        for sym in ("SPY", "QQQ"):
            try:
                items.extend(ticker_news(ticker_fn(sym), limit=8))
            except Exception:
                continue
    return _dedupe(items)[:limit]
