"""Institutional chart layer: Ichimoku, multi-oscillator divergence, drawn levels."""
from unittest import mock

import numpy as np
import pandas as pd

from app.data import market_data
from app.engines import technical as ta
from tests.conftest import make_ohlcv

DF_UP = make_ohlcv(n=400, drift=0.003, seed=3)
DF_DN = make_ohlcv(n=400, drift=-0.0025, seed=13)


def test_no_duplicate_definitions():
    """A shadowed duplicate silently disables the newer implementation."""
    src = open("app/engines/technical.py").read()
    for fn in ("volume_profile", "anchored_vwap", "ichimoku", "fib_position",
               "market_structure", "trend_quality", "monthly_context"):
        assert src.count(f"def {fn}(") == 1, f"{fn} defined more than once"
    # duplicate dict keys silently drop the earlier value
    ind_block = src[src.index('"indicators": {'):src.index('"levels": {')]
    for key in ("ichimoku", "volume_profile", "anchored_vwap"):
        assert ind_block.count(f'"{key}":') == 1, f"{key} appears twice in indicators"


def test_ichimoku_directional():
    up, dn = ta.ichimoku(DF_UP), ta.ichimoku(DF_DN)
    assert up["available"] and dn["available"]
    assert up["position"] == "above_cloud" and up["verdict"] == "bullish"
    assert dn["position"] == "below_cloud" and dn["verdict"] == "bearish"
    for k in ("tenkan", "kijun", "span_a", "span_b"):
        assert isinstance(up[k], float)
    assert ta.ichimoku(DF_UP.head(40))["available"] is False      # needs history


def test_divergence_confluence_detects_and_requires_two():
    # price makes a higher high while momentum fades → bearish divergence
    n = 200
    price = np.concatenate([np.linspace(100, 140, 80), np.linspace(140, 120, 40),
                            np.linspace(120, 145, 80)])
    idx = pd.date_range("2025-01-01", periods=n, freq="D")
    df = pd.DataFrame({"open": price, "high": price * 1.01, "low": price * 0.99,
                       "close": price,
                       "volume": np.concatenate([np.full(80, 3e6), np.full(40, 2e6),
                                                 np.full(80, 8e5)])}, index=idx)
    d = ta.divergence_confluence(df)
    assert d["available"]
    assert d["verdict"] in ("bearish", "none")
    if d["verdict"] == "bearish":
        assert d["strength"] >= 2                     # never fires on one oscillator
    assert ta.divergence_confluence(DF_UP.head(30))["available"] is False


def test_fib_levels_are_real_prices():
    f = ta.fib_position(DF_UP)
    assert f["available"]
    lo, hi = f["swing_low"], f["swing_high"]
    for key in ("level_382", "level_500", "level_618", "level_786"):
        assert lo <= f[key] <= hi
    assert f["level_618"] < f["level_500"] < f["level_382"]   # up-leg: deeper = lower


def test_advanced_layer_in_payload_and_conviction():
    out = ta.full_technical_analysis(DF_UP, patterns=[], bench_df=DF_DN)
    ind = out["indicators"]
    for key in ("ichimoku", "divergence_confluence", "volume_profile",
                "anchored_vwap", "structure", "trend_quality", "fib", "monthly"):
        assert key in ind
    joined = " ".join(out["breakdown"])
    assert "Ichimoku" in joined

    df = DF_UP.copy()
    df.index = pd.date_range("2025-01-01", periods=len(df), freq="D", tz="UTC")
    regime = {"score": 65.0, "label": "uptrend", "detail": "s", "index": "^NSEI"}
    with mock.patch.object(market_data, "get_ohlcv", return_value=df), \
         mock.patch.object(market_data, "get_fundamentals", return_value={"sector": "IT"}), \
         mock.patch.object(market_data, "get_market_regime", return_value=regime):
        from app.engines.decision import analyze_symbol
        r = analyze_symbol("ADV.NS", include_news=False)
    cv = r["conviction"]
    assert cv["total"] == 15
    names = " ".join(c["name"] for c in cv["checks"])
    assert "Ichimoku" in names and "multi-oscillator" in names.lower()


def test_chart_endpoint_returns_drawable_levels():
    df = DF_UP.copy()
    df.index = pd.date_range("2025-01-01", periods=len(df), freq="D")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        with mock.patch.object(market_data, "get_ohlcv", return_value=df):
            p = cl.get("/api/chart/ADV.NS?tf=1y").json()
    lv = p["levels"]
    assert lv["val"] <= lv["poc"] <= lv["vah"]
    assert lv["fib_618"] > 0 and lv["fib_500"] > 0 and lv["fib_zone"]
    lo, hi = float(df["low"].min()), float(df["high"].max())
    assert lo <= lv["poc"] <= hi
    # anchored VWAP is drawn as a full series, not a flat line
    assert len(p["avwap"]) > 5 and all("time" in pt and "value" in pt for pt in p["avwap"])
