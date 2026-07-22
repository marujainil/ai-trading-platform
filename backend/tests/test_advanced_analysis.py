from unittest import mock

import pandas as pd

from app.data import market_data
from app.engines import technical as ta
from tests.conftest import make_ohlcv

DF_UP = make_ohlcv(n=400, drift=0.003, seed=3)
DF_DN = make_ohlcv(n=400, drift=-0.002, seed=13)


def test_advanced_helpers_sane():
    assert ta.roc(DF_UP["close"], 60) > 0 > ta.roc(DF_DN["close"], 60)
    assert ta.obv_slope(DF_UP) >= -1 and isinstance(ta.updown_volume_ratio(DF_UP), float)
    assert 0 <= ta.bb_bandwidth_pct(DF_UP["close"]) <= 100
    assert 0 <= ta.pos_52w_pct(DF_UP) <= 100
    wk = ta.weekly_context(DF_UP)
    assert wk["available"] and wk["trend"] in ("up", "down", "mixed")


def test_relative_strength_sign():
    bench = make_ohlcv(n=400, drift=0.0005, seed=9)
    rs_up = ta.relative_strength(DF_UP, bench)
    rs_dn = ta.relative_strength(DF_DN, bench)
    assert rs_up > 0 > rs_dn
    assert ta.relative_strength(DF_UP, None) is None


def test_full_analysis_exposes_advanced_fields():
    out = ta.full_technical_analysis(DF_UP, patterns=[], bench_df=DF_DN)
    ind = out["indicators"]
    for key in ("weekly", "rs_3m_vs_bench", "obv_slope", "updown_vol_ratio",
                "roc60_pct", "bb_bandwidth_pct", "pos_52w_pct"):
        assert key in ind
    assert ind["rs_3m_vs_bench"] > 0            # up vs down benchmark
    assert 0 <= out["score"] <= 100


def test_decision_rating_and_summary():
    df = DF_UP.copy()
    df.index = pd.date_range("2025-01-01", periods=len(df), freq="D", tz="UTC")
    regime = {"score": 65.0, "label": "uptrend", "detail": "s", "index": "^NSEI"}
    fund = {"trailingPE": 20, "returnOnEquity": 0.2, "sector": "IT"}

    def fake_ohlcv(symbol, period="1y", interval="1d", min_bars=60):
        return df
    with mock.patch.object(market_data, "get_ohlcv", side_effect=fake_ohlcv), \
         mock.patch.object(market_data, "get_fundamentals", return_value=fund), \
         mock.patch.object(market_data, "get_market_regime", return_value=regime):
        from app.engines.decision import analyze_symbol
        r = analyze_symbol("TEST.NS", include_news=False)
    assert r["rating"] in ("STRONG BUY", "BUY", "ACCUMULATE", "HOLD",
                           "REDUCE", "SELL", "STRONG SELL")
    assert r["action"] == "BUY" and r["rating"] in ("BUY", "STRONG BUY")
    joined = " ".join(r["reasoning"])
    assert "Weekly timeframe" in joined or "Relative" in joined or "momentum" in joined


def test_scanner_end_to_end():
    df = DF_UP.copy()
    df.index = pd.date_range("2025-01-01", periods=len(df), freq="D", tz="UTC")
    regime = {"score": 65.0, "label": "uptrend", "detail": "s", "index": "^NSEI"}

    from app.services import scanner
    scanner.state.update(running=False, top=[], scanned=0, total=0, errors=0)
    with mock.patch.object(scanner, "_universe",
                           return_value=(["AAA.NS", "BBB-USD", "CCC.NS"], "3 test symbols")), \
         mock.patch.object(market_data, "get_ohlcv", return_value=df), \
         mock.patch.object(market_data, "get_fundamentals", return_value={"sector": "IT"}), \
         mock.patch.object(market_data, "get_market_regime", return_value=regime):
        scanner.run_full_scan()                  # synchronous for the test
    st = scanner.status()
    assert st["scanned"] == 3 and st["running"] is False
    assert len(st["top"]) >= 1
    top = st["top"][0]
    assert top["composite"] >= 55 and top["rating"]
    kinds = {t["kind"] for t in st["top"]}
    assert "crypto" in kinds and "stock" in kinds


def test_scan_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import scanner
    scanner.state.update(running=False, top=[{"symbol": "X.NS", "kind": "stock",
                                              "rating": "BUY", "action": "BUY", "composite": 70,
                                              "confidence": 60, "risk": 3, "price": 100,
                                              "trend": "uptrend", "sector": "IT"}],
                         scanned=5, total=5)
    with TestClient(app) as cl:
        st = cl.get("/api/scan/status").json()
        assert st["scanned"] == 5 and st["top"][0]["symbol"] == "X.NS"
        assert "groww_connected" in st and "binance_connected" in st


def test_trade_plan_structure_aware_rr_varies():
    from app.engines.decision import build_trade_plan
    entry, atr = 100.0, 2.0
    # resistance close by (T1 snaps lower → RR < 1.5); support tightens the stop
    sr = {"support": [{"level": 98.0}],
          "resistance": [{"level": 103.5}, {"level": 109.0}]}
    p = build_trade_plan(entry, atr, sr)
    assert p["stop"] < entry < p["t1"] < p["t2"]
    assert p["rr"] != 1.5                       # no longer a constant
    assert any("support" in n for n in p["notes"]) and any("resistance" in n for n in p["notes"])
    # no structure → clean ATR fallback with the documented 1.5 default
    p2 = build_trade_plan(entry, atr, {"support": [], "resistance": []})
    assert p2["rr"] == 1.5 and "default" in p2["notes"][0]
    # SELL framing stays mirrored and ordered
    p3 = build_trade_plan(entry, atr, sr, direction="short")
    assert p3["t2"] < p3["t1"] < entry < p3["stop"]


def test_conviction_checklist_in_payload():
    df = DF_UP.copy()
    df.index = pd.date_range("2025-01-01", periods=len(df), freq="D", tz="UTC")
    regime = {"score": 65.0, "label": "uptrend", "detail": "s", "index": "^NSEI"}
    with mock.patch.object(market_data, "get_ohlcv", return_value=df), \
         mock.patch.object(market_data, "get_fundamentals", return_value={"sector": "IT"}), \
         mock.patch.object(market_data, "get_market_regime", return_value=regime):
        from app.engines.decision import analyze_symbol
        up = analyze_symbol("UP.NS", include_news=False)
    cv = up["conviction"]
    assert cv["total"] == 15 and 0 <= cv["passed"] <= 15
    assert cv["passed"] >= 7                       # strong uptrend passes most checks
    names = {c["name"] for c in cv["checks"]}
    assert any("Weekly" in n for n in names) and any("OBV" in n for n in names)
    assert any("value area" in n for n in names) and any("anchored VWAP" in n for n in names)
    assert any("Structure" in n for n in names) and any("Monthly" in n for n in names)

    dn = DF_DN.copy()
    dn.index = pd.date_range("2025-01-01", periods=len(dn), freq="D", tz="UTC")
    with mock.patch.object(market_data, "get_ohlcv", return_value=dn), \
         mock.patch.object(market_data, "get_fundamentals", return_value={"sector": "IT"}), \
         mock.patch.object(market_data, "get_market_regime",
                           return_value={"score": 30.0, "label": "downtrend", "index": "^NSEI"}):
        from app.engines.decision import analyze_symbol
        down = analyze_symbol("DN.NS", include_news=False)
    assert down["conviction"]["passed"] < cv["passed"]   # downtrend fails more checks


def test_backtest_batch_endpoint():
    df = DF_UP.copy()
    df.index = pd.date_range("2021-01-01", periods=len(df), freq="D")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        with mock.patch.object(market_data, "get_ohlcv", return_value=df):
            r = cl.post("/api/backtest/batch",
                        json={"symbols": ["A.NS", "B.NS", "C.NS"], "period": "2y"}).json()
    assert r["symbols_tested"] == 3
    agg = r["aggregate"]
    assert agg["total_trades"] >= 3 and 0 <= (agg["win_rate_pct"] or 0) <= 100
    assert "no 99% system exists" in r["honest_note"]


def test_currency_field_matches_asset():
    df = DF_UP.copy()
    df.index = pd.date_range("2025-01-01", periods=len(df), freq="D", tz="UTC")
    regime = {"score": 60.0, "label": "uptrend", "detail": "s", "index": "^NSEI"}
    from app.engines.decision import analyze_symbol
    with mock.patch.object(market_data, "get_ohlcv", return_value=df), \
         mock.patch.object(market_data, "get_fundamentals", return_value={"sector": "IT"}), \
         mock.patch.object(market_data, "get_market_regime", return_value=regime):
        rel = analyze_symbol("RELIANCE.NS", include_news=False)
        assert rel["currency"] == "INR" and rel["fx_rate"] is None
        btc = analyze_symbol("BTC-USD", include_news=False)
        assert btc["currency"] == "INR" and btc["fx_rate"] == 84.0
        # conversion happens ONCE in the data layer (mocked here), so entries match
        assert abs(btc["entry"] - rel["entry"]) < 1e-9
        assert "₹84.00/$" in (btc["currency_note"] or "")
        assert "entry_timing" in btc and len(btc["entry_timing"]) > 10


def test_usd_inr_rate_fetch(monkeypatch):
    import time as _t
    from app.core import cache as _c
    from app.data import market_data as md
    _c.clear()
    md._FX.update(rate=None, ts=0.0)
    fx_df = DF_UP.copy() * 0 + 84.37
    with mock.patch.object(md, "get_ohlcv", return_value=fx_df):
        rate = md.usd_inr_rate()
    assert rate == 84.37
    _c.clear()
    md._FX.update(rate=84.0, ts=_t.time() + 10**9)   # restore the pin


def test_chart_endpoint_reports_inr():
    df = DF_UP.copy()
    df.index = pd.date_range("2025-01-01", periods=len(df), freq="D")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        with mock.patch.object(market_data, "get_ohlcv", return_value=df):
            stock = cl.get("/api/chart/REL.NS?tf=1y").json()
            coin = cl.get("/api/chart/BTC-USD?tf=1y").json()
    assert stock["fx_rate"] is None and coin["fx_rate"] == 84.0
    assert stock["currency"] == "INR" and coin["currency"] == "INR"


def test_scan_results_browsable_and_filterable():
    """Every scanned symbol must be inspectable — not just the qualifying top list."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import scanner

    scanner.state.update(running=False, top=[], scanned=40, total=40, errors=0)
    scanner.state["all"] = (
        [{"symbol": f"C{i}-USD", "kind": "crypto", "rating": "HOLD", "action": "HOLD",
          "composite": 40 + i, "confidence": 30, "risk": 5, "price": 100.0 + i,
          "trend": "sideways", "sector": None, "bullish": False} for i in range(20)]
        + [{"symbol": f"S{i}.NS", "kind": "stock", "rating": "BUY", "action": "BUY",
            "composite": 70 + i, "confidence": 60, "risk": 4, "price": 500.0 + i,
            "trend": "uptrend", "sector": "IT", "bullish": True} for i in range(10)])

    # a mixed bag of ratings so filtering is meaningfully exercised
    scanner.state["all"][0]["rating"] = "STRONG BUY"
    scanner.state["all"][1]["rating"] = "ACCUMULATE"
    scanner.state["all"][2]["rating"] = "SELL"

    with TestClient(app) as cl:
        # DEFAULT is buy-side only: STRONG BUY + BUY + ACCUMULATE, never HOLD/SELL
        d = cl.get("/api/scan/results?limit=100").json()
        assert {r["rating"] for r in d["rows"]} <= {"STRONG BUY", "BUY", "ACCUMULATE"}
        assert d["counts"]["buy_side"] == d["total_matching"]
        assert d["counts"]["by_rating"]["STRONG BUY"] == 1
        assert d["counts"]["by_rating"]["ACCUMULATE"] == 1
        assert d["counts"]["by_rating"]["BUY"] == 10

        allr = cl.get("/api/scan/results?limit=100&only_buy=false").json()
        assert allr["counts"] == {**allr["counts"], "crypto": 20, "stock": 10, "all": 30}
        assert allr["total_matching"] == 30
        assert allr["rows"][0]["composite"] >= allr["rows"][-1]["composite"]   # sorted
        assert any(r["rating"] in ("HOLD", "SELL") for r in allr["rows"])

        sb = cl.get("/api/scan/results?ratings=STRONG%20BUY&only_buy=false").json()
        assert sb["total_matching"] == 1 and sb["rows"][0]["rating"] == "STRONG BUY"
        two = cl.get("/api/scan/results?ratings=STRONG%20BUY,ACCUMULATE&only_buy=false").json()
        assert two["total_matching"] == 2

        cry = cl.get("/api/scan/results?kind=crypto&limit=100&only_buy=false").json()
        assert cry["total_matching"] == 20
        assert all(r["kind"] == "crypto" for r in cry["rows"])
        assert any(r["composite"] < 55 for r in cry["rows"])   # low scores now visible

        gated = cl.get("/api/scan/results?min_score=75&only_buy=false").json()
        assert all(r["composite"] >= 75 for r in gated["rows"])

        page1 = cl.get("/api/scan/results?limit=10&offset=0&only_buy=false").json()
        page2 = cl.get("/api/scan/results?limit=10&offset=10&only_buy=false").json()
        assert len(page1["rows"]) == 10 and len(page2["rows"]) == 10
        assert page1["rows"][0]["symbol"] != page2["rows"][0]["symbol"]

        by_name = cl.get("/api/scan/results?sort=symbol&limit=5&only_buy=false").json()["rows"]
        assert by_name == sorted(by_name, key=lambda r: r["symbol"])

        st = cl.get("/api/scan/status").json()
        assert st["result_counts"]["crypto"] == 20

    scanner.state["all"] = []


def test_edge_score_breaks_composite_saturation():
    """Composites clamp at 100, so many strong names tie. Edge must still separate
    them — otherwise the scanner ranking is arbitrary among the leaders."""
    import pandas as pd
    from unittest import mock
    from app.data import market_data
    from app.engines.decision import analyze_symbol
    from tests.conftest import make_ohlcv

    clean = make_ohlcv(n=400, drift=0.004, seed=3)
    choppy = make_ohlcv(n=400, drift=0.004, vol=0.05, seed=17)
    for d in (clean, choppy):
        d.index = pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC")

    res = {}
    for name, d in (("clean", clean), ("choppy", choppy)):
        with mock.patch.object(market_data, "get_ohlcv", return_value=d), \
             mock.patch.object(market_data, "get_fundamentals", return_value={}), \
             mock.patch.object(market_data, "get_market_regime",
                               return_value={"score": 60.0, "label": "uptrend", "index": "^NSEI"}):
            res[name] = analyze_symbol("X-USD", include_news=False)

    assert res["clean"]["composite_score"] == res["choppy"]["composite_score"]   # saturated
    assert res["clean"]["edge_score"] > res["choppy"]["edge_score"]              # still ranked
    for r in res.values():
        assert 0 <= r["edge_score"] <= 100
        assert set(r["edge_parts"]) == {"conviction", "trend_quality", "rel_strength",
                                        "volume_flow", "buy_pressure", "htf_agree",
                                        "momentum_health", "not_extended"}


def test_scanner_ranks_by_edge_when_scores_tie():
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import scanner

    scanner.state.update(running=False, top=[], scanned=3, total=3, errors=0)
    scanner.state["all"] = [
        {"symbol": "LOW-USD", "kind": "crypto", "rating": "BUY", "action": "BUY",
         "composite": 79.0, "edge": 51.2, "confidence": 52, "risk": 7, "price": 10.0,
         "trend": "uptrend", "sector": None, "bullish": True},
        {"symbol": "HIGH-USD", "kind": "crypto", "rating": "BUY", "action": "BUY",
         "composite": 79.0, "edge": 71.8, "confidence": 52, "risk": 7, "price": 12.0,
         "trend": "uptrend", "sector": None, "bullish": True},
    ]
    with TestClient(app) as cl:
        rows = cl.get("/api/scan/results?limit=10").json()["rows"]
        assert rows[0]["symbol"] == "HIGH-USD"          # tie broken by edge
        by_edge = cl.get("/api/scan/results?sort=edge&limit=10").json()["rows"]
        assert by_edge[0]["edge"] >= by_edge[-1]["edge"]
    scanner.state["all"] = []
