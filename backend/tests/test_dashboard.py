from app.services.charting import build_chart_payload


def test_chart_payload_shapes(df_up):
    d = build_chart_payload(df_up)
    n = len(df_up)
    assert d["bars"] == n and len(d["candles"]) == n and len(d["volume"]) == n
    c0 = d["candles"][-1]
    assert set(c0) == {"time", "open", "high", "low", "close"}
    assert c0["high"] >= max(c0["open"], c0["close"]) >= min(c0["open"], c0["close"]) >= c0["low"]
    assert len(d["ema20"]) == len(d["ema50"]) == len(d["ema200"]) == n   # ewm EMA: no NaN warmup
    assert all(p["value"] > 0 for p in d["ema200"])
    assert all(30 >= 0 and 0 <= p["value"] <= 100 for p in d["rsi"])
    # supertrend series cover every bar (value or whitespace gap marker)
    assert len(d["supertrend_bull"]) == n and len(d["supertrend_bear"]) == n
    valued = [p for p in d["supertrend_bull"] if "value" in p] + \
             [p for p in d["supertrend_bear"] if "value" in p]
    assert len(valued) >= n - 15                                  # nearly all bars have a side
    assert d["volume"][0]["color"].startswith("rgba")


def test_dashboard_served():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "AI Trading Advisor" in r.text and "Buy Scanner" in r.text
        js = client.get("/static/app.js")
        assert js.status_code == 200 and "renderVerdict" in js.text


def test_universe_endpoint():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        u = client.get("/api/universe").json()
        assert u["counts"]["stocks"] >= 40 and u["counts"]["crypto"] >= 20
        syms = {s["symbol"] for s in u["stocks"]}
        assert "RELIANCE.NS" in syms and "TCS.NS" in syms
        crys = {c["symbol"] for c in u["crypto"]}
        assert "BTC-USD" in crys and "ETH-USD" in crys
        assert all("name" in s and "kind" in s for s in u["stocks"])


def test_analyze_has_summary():
    """The Advisor UI relies on a plain-English summary line."""
    from unittest import mock
    import pandas as pd
    from app.data import market_data
    from tests.conftest import make_ohlcv
    df = make_ohlcv(n=400, drift=0.003, seed=3)
    df.index = pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC")
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        with mock.patch.object(market_data, "get_ohlcv", return_value=df), \
             mock.patch.object(market_data, "get_fundamentals", return_value={"sector": "IT"}), \
             mock.patch.object(market_data, "get_market_regime",
                               return_value={"score": 65, "label": "uptrend", "index": "^NSEI"}):
            r = client.get("/api/analyze/TEST.NS").json()
        assert "summary" in r and isinstance(r["summary"], str) and len(r["summary"]) > 10
        assert r["action"] in r["summary"].upper() or "buy" in r["summary"].lower() \
            or "hold" in r["summary"].lower() or "sell" in r["summary"].lower()


def test_price_endpoint():
    from unittest import mock
    from app.data import market_data
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        with mock.patch.object(market_data, "last_price", return_value=1234.567):
            r = client.get("/api/price/BTC-USD")
        assert r.status_code == 200
        assert r.json()["symbol"] == "BTC-USD" and r.json()["price"] == 1234.57


def test_one_minute_scan_allowed():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        r = client.put("/api/autopilot/config", json={"scan_interval_sec": 60})
        assert r.status_code == 200 and r.json()["config"]["scan_interval_sec"] == 60


def test_browse_lists_scroll_and_batch():
    """Expanded chip lists must scroll and batch-load, never clip."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        page = client.get("/").text
        js = client.get("/static/app.js").text
    assert "max-height:62vh;overflow-y:auto" in page      # scrollable when open
    assert "appendChunk" in js and "CHIP_CHUNK" in js     # infinite batch loading
    assert "onscroll" in js
