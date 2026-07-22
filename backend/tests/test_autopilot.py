from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

import pandas as pd

from app.data import market_data
from app.services import autopilot as ap
from tests.conftest import make_ohlcv

IST = ZoneInfo("Asia/Kolkata")

REGIME = {"score": 70.0, "label": "uptrend", "detail": "synthetic", "index": "^NSEI"}
FUND = {"trailingPE": 20, "returnOnEquity": 0.22, "debtToEquity": 40, "profitMargins": 0.18,
        "revenueGrowth": 0.15, "earningsGrowth": 0.2, "freeCashflow": 1e9,
        "heldPercentInsiders": 0.5, "heldPercentInstitutions": 0.25, "sector": "IT"}
DF_BUY = make_ohlcv(n=400, drift=0.003, seed=3)          # → BUY 84/100, conf 74


def test_market_hours_ist():
    mon_1000 = datetime(2026, 7, 13, 10, 0, tzinfo=IST)   # Monday
    mon_0900 = datetime(2026, 7, 13, 9, 0, tzinfo=IST)
    mon_1600 = datetime(2026, 7, 13, 16, 0, tzinfo=IST)
    sat_1100 = datetime(2026, 7, 18, 11, 0, tzinfo=IST)
    assert ap.market_open_ist(mon_1000)
    assert not ap.market_open_ist(mon_0900)
    assert not ap.market_open_ist(mon_1600)
    assert not ap.market_open_ist(sat_1100)


def test_asset_classes_and_us_hours():
    assert ap.asset_class("BTC-USD") == "crypto" and ap.asset_class("eth-inr") == "crypto"
    assert ap.asset_class("RELIANCE.NS") == "india" and ap.asset_class("RELIANCE.BO") == "india"
    assert ap.asset_class("AAPL") == "us"
    NY = ZoneInfo("America/New_York")
    assert ap.market_open_us(datetime(2026, 7, 13, 10, 0, tzinfo=NY))      # Monday 10:00
    assert not ap.market_open_us(datetime(2026, 7, 13, 8, 0, tzinfo=NY))
    assert not ap.market_open_us(datetime(2026, 7, 18, 11, 0, tzinfo=NY))  # Saturday


DF_HOLD_BULLISH = make_ohlcv(n=400, drift=0.0002, seed=1)   # engine: HOLD 61.3, supertrend ↑
REGIME_FLAT = {"score": 55.0, "label": "sideways", "detail": "s", "index": "^NSEI"}
FUND_LEAN = {"trailingPE": 24, "returnOnEquity": 0.16, "sector": "IT", "profitMargins": 0.12}


def test_entry_gate_decoupled_from_engine_threshold():
    """User's bug: composite 62 + bullish, engine says HOLD (fixed 65) — the
    Autopilot must still buy when the user's own min_composite allows it."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.engines.decision import analyze_symbol

    with TestClient(app) as cl:
        cl.put("/api/autopilot/config", json={
            "watchlist": ["MID.NS"], "min_composite": 58, "min_confidence": 0,
            "max_risk_score": 10, "max_new_positions_per_cycle": 1})
        with mock.patch.object(market_data, "get_ohlcv", return_value=DF_HOLD_BULLISH),              mock.patch.object(market_data, "get_fundamentals", return_value=FUND_LEAN),              mock.patch.object(market_data, "get_market_regime", return_value=REGIME_FLAT),              mock.patch.object(market_data, "last_price",
                               return_value=float(DF_HOLD_BULLISH["close"].iloc[-1])),              mock.patch.object(ap, "market_open_ist", return_value=True):
            eng = analyze_symbol("MID.NS", include_news=False)
            assert eng["action"] == "HOLD" and 58 <= eng["composite_score"] < 65
            out = cl.post("/api/autopilot/run-once").json()
        assert out["buys"] == 1
        evs = cl.get("/api/autopilot/events").json()
        assert any(e["kind"] == "SCAN" and "Scores:" in e["message"] for e in evs)


def test_resample_10m():
    df5 = make_ohlcv(n=12, seed=1)
    df5.index = pd.date_range("2026-07-13 09:15", periods=12, freq="5min", tz=IST)
    out = market_data.resample_ohlcv(df5, "10min")
    assert len(out) in (6, 7)
    assert out["volume"].sum() == df5["volume"].sum()
    assert out["high"].max() == df5["high"].max()
    assert out["low"].min() == df5["low"].min()


def _mocks(price=None):
    return (
        mock.patch.object(market_data, "get_ohlcv", return_value=DF_BUY),
        mock.patch.object(market_data, "get_fundamentals", return_value=FUND),
        mock.patch.object(market_data, "get_market_regime", return_value=REGIME),
        mock.patch.object(market_data, "last_price",
                          return_value=price or float(DF_BUY["close"].iloc[-1])),
        mock.patch.object(ap, "market_open_ist", return_value=True),
    )


def test_autopilot_full_cycle(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as cl:
        # configure a tiny watchlist with permissive thresholds
        r = cl.put("/api/autopilot/config", json={
            "watchlist": ["TEST.NS"], "min_composite": 65, "min_confidence": 40,
            "max_risk_score": 10, "max_new_positions_per_cycle": 1})
        assert r.status_code == 200 and r.json()["config"]["watchlist"] == ["TEST.NS"]

        m = _mocks()
        with m[0], m[1], m[2], m[3], m[4]:
            out = cl.post("/api/autopilot/run-once").json()      # cycle 1: should BUY
        assert out["buys"] == 1 and out["exits"] == 0

        events = cl.get("/api/autopilot/events").json()
        kinds = [e["kind"] for e in events]
        assert "BUY" in kinds and "SCAN" in kinds
        pf = cl.get("/api/portfolio").json()
        assert pf["open_positions"] == 1
        pos = pf["positions"][0]
        entry_stop = pos["stop_loss"] if "stop_loss" in pos else None

        # cycle 2 at a price above T1 → stop should move to breakeven, no exit
        sig = next(e for e in events if e["kind"] == "BUY")
        t1_price = float(DF_BUY["close"].iloc[-1]) * 1.10
        m2 = _mocks(price=t1_price)
        with m2[0], m2[1], m2[2], m2[3], m2[4]:
            out2 = cl.post("/api/autopilot/run-once").json()
        infos = [e for e in cl.get("/api/autopilot/events").json() if e["kind"] == "INFO"]
        assert any("breakeven" in e["message"] for e in infos)

        # cycle 3 at a crash price → stop-loss exit fires
        m3 = _mocks(price=1.0)
        with m3[0], m3[1], m3[2], m3[3], m3[4]:
            out3 = cl.post("/api/autopilot/run-once").json()
        assert out3["exits"] == 1
        assert cl.get("/api/portfolio").json()["open_positions"] == 0
        assert any(e["kind"] == "EXIT" for e in cl.get("/api/autopilot/events").json())


def test_autopilot_respects_market_closed():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as cl:
        cl.put("/api/autopilot/config", json={
            "watchlist": ["ONLYEQ.NS"], "min_composite": 65, "min_confidence": 40})
        m = _mocks()
        with mock.patch.object(ap, "market_open_ist", return_value=False), m[0], m[1], m[2], m[3]:
            out = cl.post("/api/autopilot/run-once").json()
        assert "skipped" in out and out["buys"] == 0
        assert out.get("after_hours_candidates", 0) >= 1        # queued for the open
        evs = cl.get("/api/autopilot/events").json()
        assert any("After-hours research" in e["message"] for e in evs)


def test_chart_timeframes_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app

    df5 = make_ohlcv(n=120, seed=2)
    df5.index = pd.date_range("2026-07-13 09:15", periods=120, freq="5min", tz=IST)

    def fake_ohlcv(symbol, period="1y", interval="1d", min_bars=60):
        if interval == "1d":
            return DF_BUY
        return market_data.resample_ohlcv(df5, "10min") if interval == "5m" and False else df5

    with TestClient(app) as cl:
        with mock.patch.object(market_data, "get_ohlcv", side_effect=fake_ohlcv):
            daily = cl.get("/api/chart/TEST.NS?tf=1y").json()
            intra = cl.get("/api/chart/TEST.NS?tf=15m").json()
        assert daily["intraday"] is False and isinstance(daily["candles"][0]["time"], str)
        assert intra["intraday"] is True and isinstance(intra["candles"][0]["time"], int)
        # IST epoch shift: 09:15 IST should render as 09:15 on a UTC axis
        first = intra["candles"][0]["time"]
        assert (first % 86400) // 3600 == 9 and (first % 3600) // 60 == 15
        bad = cl.get("/api/chart/TEST.NS?tf=42m")
        assert bad.status_code == 400
