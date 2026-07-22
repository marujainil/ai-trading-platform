"""INR-native platform: ONE conversion, at the data layer (get_ohlcv/last_price)."""
import time as _t
from unittest import mock

import pandas as pd

from app.core import cache
from app.data import binance, market_data
from tests.conftest import make_ohlcv


def _usd_df(n=120, seed=2):
    df = make_ohlcv(n=n, seed=seed, start_price=100.0)
    df.index = pd.date_range("2026-01-01", periods=n, freq="D", tz="UTC")
    return df


def test_usd_crypto_ohlcv_converted_to_inr():
    df_usd = _usd_df()
    with mock.patch.object(binance, "get_ohlcv", return_value=df_usd), \
         mock.patch.object(market_data, "get_usd_inr", return_value=80.0):
        out = market_data.get_ohlcv("ZZFXTESTA-USD", period="1y", min_bars=10)
    ratio = float(out["close"].iloc[-1]) / float(df_usd["close"].iloc[-1])
    assert abs(ratio - 80.0) < 1e-6
    assert float(out["volume"].iloc[-1]) == float(df_usd["volume"].iloc[-1])  # volume untouched


def test_indices_and_fx_tickers_not_converted():
    assert market_data.is_usd_asset("BTC-USD") and market_data.is_usd_asset("AAPL")
    assert not market_data.is_usd_asset("^NSEI")
    assert not market_data.is_usd_asset("USDINR=X")
    assert not market_data.is_usd_asset("RELIANCE.NS")


def test_last_price_crypto_converted():
    with mock.patch.object(binance, "last_price", return_value=100.0), \
         mock.patch.object(market_data, "get_usd_inr", return_value=84.0):
        px = market_data.last_price("ZZFXTESTB-USD")
    assert px == 8400.0


def test_fx_fallback_when_fetch_fails():
    cache.clear()
    market_data._FX.update(rate=None, ts=0.0)
    with mock.patch.object(market_data, "get_ohlcv",
                           side_effect=market_data.DataError("net down")):
        rate = market_data.get_usd_inr()
    assert rate == market_data.DEFAULT_USDINR
    cache.clear()
    market_data._FX.update(rate=84.0, ts=_t.time() + 10**9)   # restore suite pin


def test_chart_payload_reports_fx():
    df = _usd_df(400, 3)
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        with mock.patch.object(market_data, "get_ohlcv", return_value=df):
            crypto = cl.get("/api/chart/ZZFXTESTC-USD?tf=1y").json()
            stock = cl.get("/api/chart/ZZFX.NS?tf=1y").json()
    assert crypto["fx_rate"] == 84.0 and crypto["currency"] == "INR"
    assert stock["fx_rate"] is None and stock["currency"] == "INR"


def test_analyze_currency_note_for_crypto():
    df = _usd_df(400, 3)
    regime = {"score": 60.0, "label": "uptrend", "detail": "s", "index": "^NSEI"}
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        with mock.patch.object(market_data, "get_ohlcv", return_value=df), \
             mock.patch.object(market_data, "get_fundamentals", return_value={}), \
             mock.patch.object(market_data, "get_market_regime", return_value=regime), \
             mock.patch.object(market_data, "get_usd_inr", return_value=83.0):
            r = cl.get("/api/analyze/ZZFXTESTD-USD").json()
    assert "₹83.00/$" in r["currency_note"]
    assert r["currency"] == "INR" and r["fx_rate"] == 83.0
