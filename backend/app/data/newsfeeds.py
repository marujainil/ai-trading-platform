"""Extra news sources: Indian financial RSS + crypto feeds (free, no keys).

Feeds are fetched whole (cached 15 min) and filtered per symbol by company/coin
name, so ET/Moneycontrol/CoinDesk headlines join Yahoo's in the sentiment mix.
"""
from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET

import httpx

from app.core import cache

log = logging.getLogger(__name__)

FEEDS = {
    "stock": [
        "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "https://www.moneycontrol.com/rss/buzzingstocks.xml",
        "https://www.livemint.com/rss/markets",
    ],
    "crypto": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss",
    ],
}


def _fetch_feed(url: str) -> list[str]:
    try:
        resp = httpx.get(url, timeout=12, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (news reader)"})
        root = ET.fromstring(resp.content)
        return [t.text.strip() for t in root.findall(".//item/title")
                if t is not None and t.text and t.text.strip()]
    except Exception as exc:
        log.debug("RSS fetch failed %s: %s", url, exc)
        return []


def all_titles(kind: str) -> list[str]:
    key = f"rss:{kind}"
    cached = cache.get(key)
    if cached:
        return json.loads(cached)
    titles, seen = [], set()
    for url in FEEDS.get(kind, []):
        for t in _fetch_feed(url):
            k = re.sub(r"[^a-z0-9]+", "", t.lower())[:80]
            if k not in seen:
                seen.add(k)
                titles.append(t)
    titles = titles[:150]
    cache.set(key, json.dumps(titles), ttl=900)
    return titles


def _name_map() -> dict:
    from app.data import reference
    m = {s: n for s, n in reference.POPULAR_STOCKS}
    m.update({s: n for s, n in reference.POPULAR_CRYPTO})
    return m


def headlines_for(symbol: str, limit: int = 6) -> list[str]:
    su = symbol.strip().upper()
    kind = "crypto" if ("-" in su and not su.endswith((".NS", ".BO"))) else "stock"
    name = _name_map().get(su, "")
    base = su.split(".")[0].split("-")[0]
    terms = [t for t in {name, name.split()[0] if name else "", base} if len(t) >= 3]
    if not terms:
        return []
    pats = [re.compile(rf"\b{re.escape(t)}\b", re.I) for t in terms]
    out = []
    for title in all_titles(kind):
        if any(p.search(title) for p in pats):
            out.append(title)
            if len(out) >= limit:
                break
    return out


# ------------------------ news momentum (recency & velocity) ----------------- #

def _parse_dt(text: str):
    from email.utils import parsedate_to_datetime
    try:
        d = parsedate_to_datetime(text)
        return d if d.tzinfo else d.replace(tzinfo=__import__("datetime").timezone.utc)
    except Exception:
        return None


def _fetch_items(url: str) -> list[dict]:
    """Titles WITH publish timestamps, so we can measure how fresh the flow is."""
    try:
        resp = httpx.get(url, timeout=12, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (news reader)"})
        root = ET.fromstring(resp.content)
        out = []
        for item in root.findall(".//item"):
            t = item.find("title")
            d = item.find("pubDate")
            if t is not None and t.text:
                out.append({"title": t.text.strip(),
                            "ts": _parse_dt(d.text) if (d is not None and d.text) else None})
        return out
    except Exception as exc:
        log.debug("RSS items fetch failed %s: %s", url, exc)
        return []


def all_items(kind: str) -> list[dict]:
    key = f"rssitems:{kind}"
    cached = cache.get(key)
    if cached:
        out = []
        for d in json.loads(cached):
            ts = d.get("ts")
            out.append({"title": d["title"],
                        "ts": __import__("datetime").datetime.fromisoformat(ts) if ts else None})
        return out
    items: list[dict] = []
    seen: set = set()
    for url in FEEDS.get(kind, []):
        for it in _fetch_items(url):
            # the same story is syndicated across outlets — dedupe so news
            # "velocity" measures real events, not how many feeds carried them
            k = re.sub(r"[^a-z0-9]+", "", it["title"].lower())[:80]
            if k in seen:
                continue
            seen.add(k)
            items.append(it)
    items = items[:200]
    cache.set(key, json.dumps([{"title": i["title"],
                                "ts": i["ts"].isoformat() if i["ts"] else None} for i in items]),
              ttl=900)
    return items


def momentum_for(symbol: str) -> dict:
    """How ACTIVE the news flow is for this symbol right now.
    A burst of fresh headlines means the story — not the chart — is driving price,
    which raises both opportunity and risk. Quiet tape = chart signals dominate."""
    import datetime as _dt

    su = symbol.strip().upper()
    kind = "crypto" if ("-" in su and not su.endswith((".NS", ".BO"))) else "stock"
    name = _name_map().get(su, "")
    base = su.split(".")[0].split("-")[0]
    terms = [t for t in {name, name.split()[0] if name else "", base} if len(t) >= 3]
    if not terms:
        return {"available": False}
    pats = [re.compile(rf"\b{re.escape(t)}\b", re.I) for t in terms]
    now = _dt.datetime.now(_dt.timezone.utc)
    hits = [i for i in all_items(kind) if any(p.search(i["title"]) for p in pats)]
    dated = [i for i in hits if i["ts"]]
    if not dated:
        return {"available": bool(hits), "matched": len(hits), "count_24h": 0,
                "surge": False, "latest_age_hours": None}
    ages = [(now - i["ts"]).total_seconds() / 3600 for i in dated]
    c24 = sum(1 for a in ages if a <= 24)
    c7d = sum(1 for a in ages if a <= 168)
    baseline = max(0.3, (c7d - c24) / 6)          # avg/day over the prior 6 days
    return {"available": True, "matched": len(hits), "count_24h": c24,
            "count_7d": c7d, "baseline_per_day": round(baseline, 2),
            "surge": bool(c24 >= 2 and c24 >= 2 * baseline),
            "latest_age_hours": round(min(ages), 1)}
