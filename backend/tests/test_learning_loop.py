"""The learning loop: grade past signals against reality; calibrate future confidence."""
from datetime import datetime, timedelta, timezone
from unittest import mock

import pandas as pd

from app import models
from app.data import market_data
from app.engines import learning as L
from tests.conftest import make_ohlcv


def _df_after(created, path):
    """Bars AFTER `created`: path drives highs/lows so hits are deterministic."""
    idx = pd.date_range(created + timedelta(days=1), periods=len(path), freq="D", tz="UTC")
    rows = [{"open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1e6} for p in path]
    return pd.DataFrame(rows, index=idx)


def _mk_signal(db, symbol, rating, entry, stop, t1, days_ago=12):
    s = models.Signal(symbol=symbol, action="BUY" if rating in L.BUY_RATINGS else "SELL",
                      composite_score=70, confidence=60, risk_score=4,
                      entry=entry, stop_loss=stop, target_1=t1, target_2=t1 * 1.1,
                      payload={"rating": rating, "market": {"label": "uptrend"}},
                      created_at=datetime.now(timezone.utc) - timedelta(days=days_ago))
    db.add(s); db.commit(); db.refresh(s)
    return s


def test_evaluate_grades_target_and_stop(db_session):
    db = db_session
    created = datetime.now(timezone.utc) - timedelta(days=12)
    s_win = _mk_signal(db, "WINX.NS", "BUY", entry=100, stop=95, t1=110)
    s_loss = _mk_signal(db, "LOSX.NS", "BUY", entry=100, stop=95, t1=110)

    def fake(symbol, period="6mo", interval="1d", min_bars=10):
        if symbol == "WINX.NS":
            return _df_after(created, [101, 104, 112, 113])      # rallies through T1
        return _df_after(created, [99, 96, 93, 92])              # sinks through stop

    with mock.patch.object(market_data, "get_ohlcv", side_effect=fake):
        res = L.evaluate_signals(db)
    assert res["evaluated"] == 2 and res["wins"] == 1 and res["losses"] == 1
    out = {o.symbol: o for o in db.query(models.SignalOutcome).all()}
    assert out["WINX.NS"].outcome == "target_hit" and out["LOSX.NS"].outcome == "stop_hit"
    # re-running never double-grades
    with mock.patch.object(market_data, "get_ohlcv", side_effect=fake):
        res2 = L.evaluate_signals(db)
    assert res2["evaluated"] == 0


def test_track_record_and_lessons(db_session):
    db = db_session
    for i in range(12):
        db.add(models.SignalOutcome(signal_id=1000 + i, symbol=f"A{i}.NS", rating="BUY",
                                    regime_label="uptrend",
                                    outcome="target_hit" if i < 8 else "stop_hit",
                                    ret_pct=5.0 if i < 8 else -4.0, days_held=3))
    db.commit()
    t = L.track_record(db)
    row = next(r for r in t["by_rating"] if r["rating"] == "BUY")
    assert row["graded"] == 12 and abs(row["hit_rate"] - 66.7) < 0.1
    assert any("BUY: 66.7%" in x for x in t["lessons"])


def test_analyze_confidence_calibrated(db_session):
    db = db_session
    for j_, rating_ in enumerate(("BUY", "STRONG BUY")):     # cover whichever the engine emits
        for i in range(14):
            db.add(models.SignalOutcome(signal_id=2000 + j_ * 100 + i, symbol=f"B{j_}{i}.NS",
                                        rating=rating_, regime_label="uptrend",
                                        outcome="target_hit" if i < 5 else "stop_hit",
                                        ret_pct=4.0 if i < 5 else -4.0, days_held=4))
    db.commit()   # weak 35.7% record should PULL CONFIDENCE DOWN

    df = make_ohlcv(n=400, drift=0.003, seed=3)
    df.index = pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC")
    regime = {"score": 65.0, "label": "uptrend", "detail": "s", "index": "^NSEI"}
    from fastapi.testclient import TestClient
    from app.main import app as fastapp
    with TestClient(fastapp) as cl:
        with mock.patch.object(market_data, "get_ohlcv", return_value=df), \
             mock.patch.object(market_data, "get_fundamentals", return_value={"sector": "IT"}), \
             mock.patch.object(market_data, "get_market_regime", return_value=regime):
            r = cl.get("/api/analyze/CALIB.NS").json()
    # weak measured record (35.7%) triggers BOTH calibration and the guardrail
    assert r["guardrail"]["hit_rate"] < 45 and r["guardrail"]["to"] == r["rating"]
    assert r["rating"] in ("ACCUMULATE", "BUY")          # stepped down from BUY/STRONG BUY
    assert r["track_record"]["samples"] == 14
    assert any("Track record" in line for line in r["reasoning"])
    assert any("guardrail" in line for line in r["reasoning"])


def test_learn_endpoints_empty(db_session):
    from fastapi.testclient import TestClient
    from app.main import app as fastapp
    with TestClient(fastapp) as cl:
        assert cl.post("/api/learn/evaluate").json()["evaluated"] == 0
        t = cl.get("/api/learn/track-record").json()
        assert t["total_outcomes"] == 0 and any("No graded history" in x for x in t["lessons"])


def test_conviction_band_stored_and_reported(db_session):
    db = db_session
    created = datetime.now(timezone.utc) - timedelta(days=12)
    s = models.Signal(symbol="BANDX.NS", action="BUY", composite_score=80, confidence=70,
                      risk_score=3, entry=100, stop_loss=95, target_1=110, target_2=120,
                      payload={"rating": "STRONG BUY", "market": {"label": "uptrend"},
                               "conviction": {"passed": 11, "total": 13}},
                      created_at=created)
    db.add(s); db.commit()
    with mock.patch.object(market_data, "get_ohlcv",
                           return_value=_df_after(created, [101, 105, 111, 112])):
        L.evaluate_signals(db)
    o = db.query(models.SignalOutcome).filter_by(symbol="BANDX.NS").one()
    assert o.conviction_band == "high" and o.outcome == "target_hit"
    t = L.track_record(db)
    hi = next(b for b in t["by_conviction"] if b["band"] == "high")
    assert hi["graded"] == 1 and hi["hit_rate"] == 100.0


def test_regime_guardrail_thresholds(db_session):
    db = db_session
    for i in range(12):
        db.add(models.SignalOutcome(signal_id=3000 + i, symbol=f"G{i}.NS", rating="BUY",
                                    regime_label="downtrend", conviction_band="mid",
                                    outcome="target_hit" if i < 4 else "stop_hit",
                                    ret_pct=4.0 if i < 4 else -4.0, days_held=3))
    db.commit()
    g = L.regime_guardrail(db, "BUY", "downtrend")
    assert g and g["samples"] == 12 and g["hit_rate"] < 45
    assert L.regime_guardrail(db, "BUY", "uptrend") is None      # no data there
    assert L.regime_guardrail(db, "SELL", "downtrend") is None   # buy-side only
