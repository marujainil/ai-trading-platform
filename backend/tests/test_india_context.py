"""Beyond-the-chart context: delivery %, sector RS, earnings blackout, concentration."""
import io
import time
from types import SimpleNamespace
from unittest import mock

import pandas as pd

from app.core import cache
from app.data import india_flows, market_data
from tests.conftest import make_ohlcv

BHAV = ("SYMBOL, SERIES, DELIV_PER\n"
        "TCS, EQ, 62.5\nRELIANCE, EQ, 18.2\nINFY, EQ, 47.0\n")


def _client(rows=BHAV):
    class C:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, url, **k):
            return SimpleNamespace(status_code=200, text=rows,
                                   content=(rows * 40).encode())
    return C


def test_delivery_stats_parses_and_labels():
    cache.clear()
    with mock.patch.object(india_flows.httpx, "Client", _client()):
        d = india_flows.delivery_stats("TCS.NS")
    assert d["available"] and d["latest_pct"] == 62.5
    assert d["label"] in ("healthy", "strong_accumulation", "normal")
    cache.clear()
    with mock.patch.object(india_flows.httpx, "Client", _client()):
        churn = india_flows.delivery_stats("RELIANCE.NS")
    assert churn["available"] and churn["label"] == "churn"
    cache.clear()


def test_delivery_degrades_gracefully_never_guesses():
    """If NSE is unreachable the check must be skipped, not invented."""
    cache.clear()
    class Boom:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def get(self, *a, **k): raise OSError("network down")
    with mock.patch.object(india_flows.httpx, "Client", Boom):
        d = india_flows.delivery_stats("TCS.NS")
    assert d["available"] is False and "latest_pct" not in d
    assert india_flows.delivery_stats("BTC-USD")["available"] is False
    cache.clear()


def test_earnings_helper_windows():
    now = time.time()
    assert market_data.earnings_days_away({"earningsTimestamp": now + 2 * 86400}) == 2
    assert market_data.earnings_days_away({"earningsTimestamp": now - 30 * 86400}) is None
    assert market_data.earnings_days_away({}) is None
    assert market_data.earnings_days_away({"earningsTimestamp": "bad"}) is None


def test_sector_index_mapping():
    assert market_data.sector_index_for("Technology") == "^CNXIT"
    assert market_data.sector_index_for("Financial Services") == "^NSEBANK"
    assert market_data.sector_index_for(None) is None
    assert market_data.sector_index_for("Nonexistent Sector") is None


def _run(fund, delivery, sector_drift=0.0005):
    df = make_ohlcv(n=400, drift=0.003, seed=3)
    df.index = pd.date_range("2025-01-01", periods=400, freq="D", tz="UTC")
    sec = make_ohlcv(n=400, drift=sector_drift, seed=9)
    sec.index = df.index

    def fake(symbol, period="1y", interval="1d", min_bars=60):
        return sec if symbol.startswith("^") and symbol != "^NSEI" else df
    from app.engines.decision import analyze_symbol
    with mock.patch.object(market_data, "get_ohlcv", side_effect=fake), \
         mock.patch.object(market_data, "get_fundamentals", return_value=fund), \
         mock.patch.object(market_data, "get_market_regime",
                           return_value={"score": 65.0, "label": "uptrend", "index": "^NSEI"}), \
         mock.patch.object(india_flows, "delivery_stats", return_value=delivery):
        return analyze_symbol("TCS.NS", include_news=False)


def test_earnings_blackout_caps_rating():
    fund = {"sector": "Technology", "trailingPE": 22, "returnOnEquity": 0.19,
            "earningsTimestamp": time.time() + 86400}
    r = _run(fund, {"available": False})
    assert r["earnings_in_days"] == 1 and r["rating"] == "HOLD"
    assert any("Earnings in" in x for x in r["reasoning"])

    fund_far = dict(fund, earningsTimestamp=time.time() + 30 * 86400)
    r2 = _run(fund_far, {"available": False})
    assert r2["rating"] != "HOLD"          # far-off earnings must not block


def test_sector_rs_and_delivery_shift_confidence():
    fund = {"sector": "Technology", "trailingPE": 22, "returnOnEquity": 0.19}
    strong = _run(fund, {"available": True, "latest_pct": 68.0, "avg_pct": 44.0,
                         "days": 5, "label": "strong_accumulation", "note": "n"})
    churn = _run(fund, {"available": True, "latest_pct": 18.0, "avg_pct": 40.0,
                        "days": 5, "label": "churn", "note": "n"})
    assert strong["confidence"] > churn["confidence"]
    assert strong["sector_rs"] > 0 and strong["sector_index"] == "^CNXIT"
    assert any("Delivery" in x for x in strong["reasoning"])
    assert any("churn" in x for x in churn["reasoning"])


def test_scanner_concentration_warning():
    from app.services import scanner
    clustered = [{"sector": "Financial Services"}] * 6 + [{"sector": "Technology"}] * 4
    assert "Concentration risk" in scanner.concentration_warning(clustered)
    assert scanner.concentration_warning([{"sector": f"S{i}"} for i in range(10)]) is None
    assert scanner.concentration_warning([{"sector": "X"}] * 3) is None      # too few
