from unittest import mock

import pandas as pd

from app.data import groww
from app.services import autopilot as ap


def test_split_symbol():
    assert groww.split_symbol("RELIANCE.NS") == ("NSE", "RELIANCE")
    assert groww.split_symbol("tcs.bo") == ("BSE", "TCS")
    assert groww.split_symbol("INFY") == ("NSE", "INFY")


def test_get_ohlcv_parses_groww_candles():
    fake = {"status": "SUCCESS", "payload": {"candles": [
        [1633072800, 150.1, 155.0, 145.0, 152.4, 10000],
        [1633159200, 152.4, 158.0, 151.0, 157.2, 12000],
    ]}}

    class R:
        def raise_for_status(self): pass
        def json(self): return fake

    with mock.patch.object(groww, "settings") as st, \
         mock.patch.object(groww.httpx, "get", return_value=R()) as g:
        st.groww_api_token = "tok"
        df = groww.get_ohlcv("RELIANCE.NS", period="1y", interval="1d")
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2 and df["close"].iloc[-1] == 157.2
    # request used the correct endpoint + NSE mapping
    args, kwargs = g.call_args
    assert "/v1/historical/candle/range" in args[0]
    assert kwargs["params"]["trading_symbol"] == "RELIANCE" and kwargs["params"]["exchange"] == "NSE"


def test_full_universe_dedupes_dual_listings():
    with mock.patch.object(groww, "universe", side_effect=lambda ex, equity_only=True:
                           ["RELIANCE.NS", "TCS.NS", "INFY.NS"] if ex == "NSE"
                           else ["RELIANCE.BO", "SOMEBSE.BO"]):
        merged = groww.full_universe()
    assert "RELIANCE.NS" in merged and "RELIANCE.BO" not in merged   # NSE preferred
    assert "SOMEBSE.BO" in merged                                    # BSE-only kept
    assert len(merged) == 4


def test_autopilot_entire_market_rotation():
    """entire_market mode should pull the universe and rotate slices across cycles."""
    fake_universe = [f"S{i}.NS" for i in range(600)]
    cfg = mock.Mock()
    cfg.universe_mode = "entire_market"
    cfg.watchlist = ["BTC-USD"]

    ap._state["universe_offset"] = 0
    with mock.patch.object(ap, "settings") as st, \
         mock.patch("app.data.groww.is_enabled", return_value=True), \
         mock.patch("app.data.groww.full_universe", return_value=fake_universe):
        st.universe_scan_slice = 250
        syms1, note1 = ap._resolve_symbols(cfg)
        syms2, note2 = ap._resolve_symbols(cfg)

    assert "BTC-USD" in syms1                      # crypto always included
    stocks1 = [s for s in syms1 if s != "BTC-USD"]
    stocks2 = [s for s in syms2 if s != "BTC-USD"]
    assert len(stocks1) == 250 and len(stocks2) == 250
    assert stocks1[0] == "S0.NS" and stocks2[0] == "S250.NS"   # slice advanced
    assert "entire_market" in note1


def test_autopilot_entire_market_without_any_source_falls_back():
    cfg = mock.Mock()
    cfg.universe_mode = "entire_market"
    cfg.watchlist = ["RELIANCE.NS", "BTC-USD"]
    with mock.patch("app.data.groww.is_enabled", return_value=False), \
         mock.patch("app.data.binance.universe", return_value=[]):
        syms, note = ap._resolve_symbols(cfg)
    assert syms == ["RELIANCE.NS", "BTC-USD"]
    assert "no data source" in note


def test_autopilot_entire_market_binance_only():
    """Even without a Groww token, entire_market scans the Binance crypto universe."""
    cfg = mock.Mock()
    cfg.universe_mode = "entire_market"
    cfg.watchlist = ["BTC-USD"]
    ap._state["universe_offset"] = 0
    with mock.patch.object(ap, "settings") as st, \
         mock.patch("app.data.groww.is_enabled", return_value=False), \
         mock.patch("app.data.binance.universe",
                    return_value=[f"C{i}-USD" for i in range(80)]):
        st.universe_scan_slice = 250
        syms, note = ap._resolve_symbols(cfg)
    assert "BTC-USD" in syms
    assert any(s.startswith("C") for s in syms)          # crypto universe scanned
    assert "Groww off" in note


def test_status_reports_groww_flag():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        st = cl.get("/api/autopilot").json()["status"]
        assert "groww_connected" in st and st["groww_connected"] is False   # no token in tests


def test_groww_runtime_token_set_and_clear(tmp_path, monkeypatch):
    import app.data.groww as gw
    # point the token file at a temp location so we don't touch the real one
    monkeypatch.setattr(gw, "_TOKEN_FILE", tmp_path / ".groww_token")
    monkeypatch.setattr(gw, "_RUNTIME_TOKEN", None)

    fake_instruments = {("NSE", "RELIANCE"): {"exchange": "NSE", "trading_symbol": "RELIANCE"}}
    with mock.patch.object(gw, "_load_instruments", return_value=fake_instruments), \
         mock.patch.object(gw, "last_price", return_value=1234.5):
        res = gw.set_token("daily-token-123")
    assert res["connected"] is True and res["instruments"] == 1
    assert res["probe"]["ltp"] == 1234.5
    assert gw.is_enabled() is True
    assert (tmp_path / ".groww_token").read_text() == "daily-token-123"

    cleared = gw.set_token("")
    assert cleared["connected"] is False and gw.is_enabled() is False
    assert not (tmp_path / ".groww_token").exists()


def test_groww_token_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    import app.data.groww as gw
    with TestClient(app) as cl:
        with mock.patch.object(gw, "_load_instruments",
                               return_value={("NSE", "X"): {}}), \
             mock.patch.object(gw, "last_price", return_value=100.0):
            r = cl.post("/api/settings/groww-token", json={"token": "abc"})
        assert r.status_code == 200 and r.json()["connected"] is True
        gw.set_token("")   # cleanup


def test_set_token_rejects_unauthorized(tmp_path, monkeypatch):
    import app.data.groww as gw
    monkeypatch.setattr(gw, "_TOKEN_FILE", tmp_path / ".groww_token")
    monkeypatch.setattr(gw, "_RUNTIME_TOKEN", None)
    with mock.patch.object(gw, "_load_instruments", return_value={("NSE", "X"): {}}), \
         mock.patch.object(gw, "last_price",
                           side_effect=gw.GrowwError("Groww LTP failed: 401 Unauthorized")):
        res = gw.set_token("bad-token")
    assert res["connected"] is False and "rejected" in res["error"]
    gw.set_token("")
