"""India-specific data the chart alone cannot show.

**Delivery percentage** — NSE publishes, for every stock every day, how much of the
traded volume was actually *delivered* (taken into demat) versus churned intraday.
A breakout on 65% delivery is real accumulation; the same breakout on 20% delivery
is day-traders passing paper to each other. This is the single most useful Indian
data point that price charts cannot reveal.

NSE blocks plain scripted requests, so we prime a session for cookies and degrade
gracefully: if the data can't be had, callers simply skip the check rather than
guessing. Never fabricate a number.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta

import httpx
import pandas as pd

from app.core import cache

log = logging.getLogger(__name__)

_HOME = "https://www.nseindia.com"
_BHAV = "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{d}.csv"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"),
    "Accept": "text/csv,*/*",
    "Accept-Language": "en-IN,en;q=0.9",
    "Referer": f"{_HOME}/all-reports",
}


def _fetch_bhavcopy(day: datetime) -> pd.DataFrame | None:
    """One day's full security-wise bhavcopy (includes delivery columns)."""
    stamp = day.strftime("%d%m%Y")
    try:
        with httpx.Client(timeout=20, follow_redirects=True, headers=_HEADERS) as cl:
            cl.get(_HOME)                      # prime cookies
            r = cl.get(_BHAV.format(d=stamp))
            if r.status_code != 200 or len(r.content) < 500:
                return None
            df = pd.read_csv(io.StringIO(r.text))
    except Exception as exc:
        log.debug("NSE bhavcopy %s failed: %s", stamp, exc)
        return None
    df.columns = [c.strip().upper() for c in df.columns]
    if "SYMBOL" not in df.columns:
        return None
    df["SYMBOL"] = df["SYMBOL"].astype(str).str.strip()
    return df


def _recent_bhavcopies(days_back: int = 12, want: int = 5) -> list[pd.DataFrame]:
    """Up to `want` recent trading-day bhavcopies, newest first (cached 6h)."""
    key = f"nse:bhav:{datetime.now().strftime('%Y%m%d')}:{want}"
    cached = cache.get(key)
    if cached == "EMPTY":
        return []
    frames: list[pd.DataFrame] = []
    day = datetime.now()
    for _ in range(days_back):
        if len(frames) >= want:
            break
        if day.weekday() < 5:                  # skip weekends
            df = _fetch_bhavcopy(day)
            if df is not None:
                frames.append(df)
        day -= timedelta(days=1)
    if not frames:
        cache.set(key, "EMPTY", ttl=3600)
    return frames


def delivery_stats(symbol: str) -> dict:
    """Delivery % for an NSE symbol: latest day and recent average, plus a trend
    read. Returns {"available": False} whenever NSE data can't be obtained."""
    su = symbol.strip().upper()
    if not su.endswith(".NS"):
        return {"available": False, "reason": "NSE stocks only"}
    base = su[:-3]

    key = f"delivery:{base}:{datetime.now().strftime('%Y%m%d')}"
    cached = cache.get(key)
    if cached:
        return json.loads(cached)

    frames = _recent_bhavcopies()
    vals: list[float] = []
    for df in frames:
        row = df[(df["SYMBOL"] == base) & (df.get("SERIES", "EQ").astype(str).str.strip() == "EQ")] \
            if "SERIES" in df.columns else df[df["SYMBOL"] == base]
        if row.empty:
            continue
        col = next((c for c in df.columns if "DELIV_PER" in c or "DELIVERY_PER" in c), None)
        if not col:
            continue
        try:
            v = float(str(row.iloc[0][col]).strip())
        except (ValueError, TypeError):
            continue
        if 0 <= v <= 100:
            vals.append(v)

    if not vals:
        out = {"available": False, "reason": "NSE delivery data unavailable right now"}
        cache.set(key, json.dumps(out), ttl=1800)
        return out

    latest = vals[0]
    avg = sum(vals) / len(vals)
    if latest >= avg * 1.25 and latest >= 45:
        label, note = "strong_accumulation", "delivery well above its own recent average"
    elif latest >= 50:
        label, note = "healthy", "over half the volume was actually delivered"
    elif latest <= 25:
        label, note = "churn", "mostly intraday churn, little real delivery"
    else:
        label, note = "normal", "delivery in its usual range"
    out = {"available": True, "latest_pct": round(latest, 1),
           "avg_pct": round(avg, 1), "days": len(vals), "label": label, "note": note}
    cache.set(key, json.dumps(out), ttl=6 * 3600)
    return out
