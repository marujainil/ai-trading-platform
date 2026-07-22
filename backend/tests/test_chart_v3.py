"""Institutional chart layer: volume profile, anchored VWAP, structure,
trend quality, fib, monthly context — plus news momentum and market breadth."""
import datetime as dt
from email.utils import format_datetime
from types import SimpleNamespace
from unittest import mock

import numpy as np
import pandas as pd

from app.core import cache
from app.engines import technical as ta
from tests.conftest import make_ohlcv


def _dated(df, start="2024-01-01"):
    df = df.copy()
    df.index = pd.date_range(start, periods=len(df), freq="D")
    return df


DF_UP = _dated(make_ohlcv(n=400, drift=0.003, seed=3))
DF_DN = _dated(make_ohlcv(n=400, drift=-0.002, seed=13))


def test_volume_profile_finds_value_area():
    vp = ta.volume_profile(DF_UP)
    assert vp["available"]
    assert vp["val"] <= vp["poc"] <= vp["vah"]          # POC inside the value area
    assert vp["zone"] in ("above_value", "inside_value", "below_value")
    # a strong uptrend should end above the volume it built earlier
    assert vp["zone"] == "above_value"
    assert ta.volume_profile(DF_UP.head(5))["available"] is False


def test_anchored_vwap_tracks_the_52w_low():
    av = ta.anchored_vwap(DF_UP)
    assert av["available"] and av["above"] is True       # uptrend closes above its AVWAP
    assert av["value"] > 0 and len(av["anchor_date"]) == 10
    av_dn = ta.anchored_vwap(DF_DN)
    assert av_dn["available"] and av_dn["dist_pct"] < av["dist_pct"]


def _zigzag(start, step, n=240, up=True):
    """Deterministic staircase: unambiguous HH/HL (or LH/LL) structure."""
    px, out = start, []
    for i in range(n):
        leg = (i // 12) * step * (1 if up else -1)
        wob = 4.0 if (i % 12) < 6 else -4.0
        px = start + leg + wob
        out.append(px)
    s = pd.Series(out)
    return _dated(pd.DataFrame({"open": s, "high": s + 2, "low": s - 2,
                                "close": s, "volume": 1e6}))


def test_market_structure_detects_hh_hl_and_bos():
    up = ta.market_structure(_zigzag(100, 6, up=True))
    dn = ta.market_structure(_zigzag(400, 6, up=False))
    assert up["available"] and up["bias"] == "bullish"
    assert "higher highs" in up["label"]
    assert dn["available"] and dn["bias"] == "bearish"
    assert "lower highs" in dn["label"]
    assert up["last_swing_low"] < up["last_swing_high"]
    assert up["bos"] in (None, "bullish", "bearish")


def test_trend_quality_separates_clean_from_choppy():
    n = 200
    clean = _dated(pd.DataFrame({
        "open": np.linspace(100, 200, n), "high": np.linspace(101, 202, n),
        "low": np.linspace(99, 198, n), "close": np.linspace(100, 200, n),
        "volume": np.full(n, 1e6)}))
    rng = np.random.default_rng(7)
    chop_px = 100 + np.cumsum(rng.normal(0, 2, n))
    chop = _dated(pd.DataFrame({"open": chop_px, "high": chop_px + 1, "low": chop_px - 1,
                                "close": chop_px, "volume": np.full(n, 1e6)}))
    tq_clean, tq_chop = ta.trend_quality(clean), ta.trend_quality(chop)
    assert tq_clean["r2"] > 0.95 and tq_clean["efficiency"] > 0.9
    assert tq_clean["direction"] == "up"
    assert tq_chop["efficiency"] < tq_clean["efficiency"]


def test_fib_and_monthly_context():
    fib = ta.fib_position(DF_UP)
    assert fib["available"] and 0.0 <= fib["retracement"] <= 1.0
    assert fib["swing_low"] < fib["swing_high"]
    mo = ta.monthly_context(DF_UP)
    assert mo["available"] and mo["trend"] in ("up", "down", "mixed")


def test_full_analysis_exposes_institutional_block_and_reasons():
    out = ta.full_technical_analysis(DF_UP, patterns=[])
    ind = out["indicators"]
    for k in ("volume_profile", "anchored_vwap", "structure", "trend_quality", "fib", "monthly"):
        assert k in ind
    joined = " ".join(out["breakdown"])
    assert "value area" in joined or "anchored VWAP" in joined or "structure" in joined
    assert 0 <= out["score"] <= 100


def test_chart_endpoint_serves_overlays():
    from fastapi.testclient import TestClient
    from app.data import market_data
    from app.main import app
    with TestClient(app) as cl:
        with mock.patch.object(market_data, "get_ohlcv", return_value=DF_UP):
            p = cl.get("/api/chart/TEST.NS?tf=1y").json()
    assert p["levels"]["val"] <= p["levels"]["poc"] <= p["levels"]["vah"]
    assert len(p["avwap"]) > 50
    assert all(set(pt) == {"time", "value"} for pt in p["avwap"][:5])


def test_news_momentum_detects_surge_and_quiet():
    from app.data import newsfeeds
    cache.clear()
    now = dt.datetime.now(dt.timezone.utc)
    items = b"".join(
        (f"<item><title>Reliance Industries signs deal {i}</title>"
         f"<pubDate>{format_datetime(now - dt.timedelta(hours=h))}</pubDate></item>").encode()
        for i, h in enumerate((1, 4, 9, 30, 120)))
    rss = b"<rss><channel>" + items + b"</channel></rss>"
    with mock.patch.object(newsfeeds.httpx, "get", return_value=SimpleNamespace(content=rss)):
        m = newsfeeds.momentum_for("RELIANCE.NS")
        quiet = newsfeeds.momentum_for("WIPRO.NS")
    assert m["available"] and m["count_24h"] == 3 and m["surge"] is True
    assert m["latest_age_hours"] <= 1.5
    assert quiet["available"] is False or quiet["count_24h"] == 0
    cache.clear()


def test_scanner_market_breadth():
    from app.services import scanner
    b = scanner._breadth({"n": 100, "above_ema50": 72, "bullish_st": 65, "adv": 72, "dec": 28})
    assert b["pct_above_ema50"] == 72.0 and "broad participation" in b["mood"]
    weak = scanner._breadth({"n": 100, "above_ema50": 22, "bullish_st": 18, "adv": 22, "dec": 78})
    assert "narrow" in weak["mood"] or "weak" in weak["mood"]
    assert scanner._breadth({"n": 5, "above_ema50": 3, "bullish_st": 2, "adv": 3, "dec": 2}) is None
