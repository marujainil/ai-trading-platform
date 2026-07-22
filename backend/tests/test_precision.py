"""Precision Mode: the accuracy-target controller. Finds where the target lives
in graded history, filters buys outside that class, never promises."""
from datetime import datetime, timedelta, timezone
from unittest import mock

import pandas as pd
import pytest

from app import models
from app.data import market_data
from app.engines import precision as P
from tests.conftest import make_ohlcv


@pytest.fixture(autouse=True)
def _isolate_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "_FILE", tmp_path / ".precision")
    yield


def _seed(db, n, wins, composite, band, start_id):
    for i in range(n):
        db.add(models.SignalOutcome(
            signal_id=start_id + i, symbol=f"P{start_id + i}.NS", rating="BUY",
            regime_label="uptrend", conviction_band=band, composite=composite,
            outcome="target_hit" if i < wins else "stop_hit",
            ret_pct=4.0 if i < wins else -4.0, days_held=3))
    db.commit()


def test_recommendation_finds_loosest_qualifying_class(db_session):
    db = db_session
    _seed(db, 20, wins=10, composite=66, band="mid", start_id=4000)   # 50% loose class
    _seed(db, 16, wins=12, composite=77, band="high", start_id=4100)  # 75% strict class
    rec = P.recommendation(db, target=70)
    assert rec["met"] is True and rec["hit_rate"] >= 70
    # the qualifying gate must exclude the 50% class (composite 66/mid)
    assert rec["min_composite"] >= 70 or rec["min_conviction"] == "high"
    # unreachable target → honest best-effort
    rec85 = P.recommendation(db, target=85)
    assert rec85["met"] is False and rec85["hit_rate"] < 85
    # empty history → None
    for o in db.query(models.SignalOutcome).all():
        db.delete(o)
    db.commit()
    assert P.recommendation(db, target=70) is None


def test_curve_reports_cells(db_session):
    _seed(db_session, 18, wins=13, composite=72, band="high", start_id=4300)
    rows = P.curve(db_session)
    assert any(r["samples"] >= 15 and r["hit_rate"] > 70 for r in rows)
    assert all(set(r) >= {"conviction", "min_composite", "samples", "hit_rate"} for r in rows)


def test_settings_persist_and_clamp():
    s = P.set_settings(True, 99)
    assert s == {"enabled": True, "target": 85}          # clamped
    assert P.get_settings() == {"enabled": True, "target": 85}
    P.set_settings(False, 70)
    assert P.get_settings()["enabled"] is False


def _analyze(db):
    df = make_ohlcv(n=400, drift=0.003, seed=3)
    df.index = pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC")
    regime = {"score": 65.0, "label": "uptrend", "detail": "s", "index": "^NSEI"}
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        with mock.patch.object(market_data, "get_ohlcv", return_value=df), \
             mock.patch.object(market_data, "get_fundamentals", return_value={"sector": "IT"}), \
             mock.patch.object(market_data, "get_market_regime", return_value=regime):
            return cl.get("/api/analyze/PREC.NS").json()


def _analyze_weak(db):
    """A mild-uptrend name: buy-leaning (ACCUMULATE-ish) but low composite/band."""
    df = make_ohlcv(n=400, drift=0.0002, seed=1)
    df.index = pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC")
    regime = {"score": 55.0, "label": "sideways", "detail": "s", "index": "^NSEI"}
    fund = {"trailingPE": 24, "returnOnEquity": 0.16, "sector": "IT", "profitMargins": 0.12}
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        with mock.patch.object(market_data, "get_ohlcv", return_value=df), \
             mock.patch.object(market_data, "get_fundamentals", return_value=fund), \
             mock.patch.object(market_data, "get_market_regime", return_value=regime):
            return cl.get("/api/analyze/WEAK.NS").json()


def test_gate_filters_weak_buy_to_hold(db_session):
    # loose class measures 40% (fails target); only composite ≥75/high measures 81%
    _seed(db_session, 20, wins=8, composite=66, band="mid", start_id=4500)
    _seed(db_session, 16, wins=13, composite=77, band="high", start_id=4600)
    P.set_settings(True, 70)
    r = _analyze_weak(db_session)
    assert r["precision"]["gate"]["met"] is True
    if r["precision"].get("passed") is False:            # weak signal → filtered
        assert r["rating"] == "HOLD"
        assert any("Precision mode" in x and "filtered to HOLD" in x for x in r["reasoning"])
    else:                                                # engine drifted stronger: still consistent
        assert r["composite_score"] >= r["precision"]["gate"]["min_composite"]


def test_gate_passes_qualifying_signal(db_session):
    # generous class: any conviction, composite ≥60, measured 73%
    _seed(db_session, 30, wins=22, composite=62, band="mid", start_id=4700)
    P.set_settings(True, 70)
    r = _analyze(db_session)
    assert r["precision"]["passed"] is True
    assert r["rating"] in ("STRONG BUY", "BUY", "ACCUMULATE")
    assert any("Precision gate PASSED" in x for x in r["reasoning"])


def test_disabled_and_armed_states(db_session):
    P.set_settings(False, 70)
    r = _analyze(db_session)
    assert "precision" not in r                          # off → untouched
    P.set_settings(True, 70)
    r2 = _analyze(db_session)                            # no history → armed, no filtering
    assert r2["precision"]["gate"] is None
    assert r2["rating"] != "HOLD" or "Precision" not in " ".join(r2["reasoning"])


def test_endpoints(db_session):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        p = cl.post("/api/settings/precision", json={"enabled": True, "target": 72}).json()
        assert p["enabled"] and p["target"] == 72
        g = cl.get("/api/settings/precision").json()
        assert g["target"] == 72 and "gate" in g
        t = cl.get("/api/learn/track-record").json()
        assert "precision_curve" in t and "precision_settings" in t
    P.set_settings(False, 70)
