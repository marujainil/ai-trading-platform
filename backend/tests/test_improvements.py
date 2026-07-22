"""Auto-learning, RSS news, Telegram alerts, watchlist — the four upgrades."""
import json
from types import SimpleNamespace
from unittest import mock

from app.core import cache

STOCK_RSS = b"""<rss><channel>
<item><title>Reliance Industries hits record high on energy deal</title></item>
<item><title>Markets slip as IT drags</title></item>
</channel></rss>"""
CRYPTO_RSS = b"""<rss><channel>
<item><title>Bitcoin surges past resistance as ETF flows return</title></item>
<item><title>Altcoins mixed in quiet trade</title></item>
</channel></rss>"""


def test_newsfeeds_parse_and_filter():
    from app.data import newsfeeds
    cache.clear()

    def fake_get(url, **kw):
        return SimpleNamespace(content=CRYPTO_RSS if ("coindesk" in url or "cointelegraph" in url)
                               else STOCK_RSS)
    with mock.patch.object(newsfeeds.httpx, "get", side_effect=fake_get):
        assert len(newsfeeds.all_titles("stock")) >= 2
        rel = newsfeeds.headlines_for("RELIANCE.NS")
        btc = newsfeeds.headlines_for("BTC-USD")
    assert any("Reliance" in t for t in rel)
    assert any("Bitcoin" in t for t in btc)
    assert not any("Reliance" in t for t in btc)
    cache.clear()


def test_watchlist_flow_and_change_alert(db_session):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import watch, notify

    ratings = iter(["BUY", "HOLD"])
    def fake_analyze(symbol, period="1y", include_news=False):
        return {"rating": next(ratings), "action": "BUY", "composite_score": 66.0,
                "confidence": 55.0, "entry": 123.45}
    sent = []
    with TestClient(app) as cl:
        with mock.patch.object(watch, "analyze_symbol", side_effect=fake_analyze), \
             mock.patch.object(notify, "send", side_effect=lambda t: (sent.append(t), (True, None))[1]):
            assert cl.post("/api/watchlist/reliance.ns").json()["items"][0]["symbol"] == "RELIANCE.NS"
            r1 = cl.post("/api/watchlist/refresh").json()
            assert r1["items"][0]["last_rating"] == "BUY" and r1["changes"] == []
            r2 = cl.post("/api/watchlist/refresh").json()
            assert r2["changes"][0]["from"] == "BUY" and r2["changes"][0]["to"] == "HOLD"
            assert any("BUY → HOLD" in t for t in sent)
            assert cl.delete("/api/watchlist/RELIANCE.NS").json()["items"] == []


def test_telegram_settings_endpoint(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.services import notify
    monkeypatch.setattr(notify, "_CFG_FILE", tmp_path / ".telegram")
    with TestClient(app) as cl:
        with mock.patch.object(notify.httpx, "post",
                               return_value=SimpleNamespace(status_code=200)):
            r = cl.post("/api/settings/telegram",
                        json={"bot_token": "123:abc", "chat_id": "42"}).json()
        assert r["connected"] is True
        assert json.loads((tmp_path / ".telegram").read_text())["chat_id"] == "42"
        r2 = cl.post("/api/settings/telegram", json={"bot_token": "", "chat_id": ""}).json()
        assert r2["connected"] is False and not (tmp_path / ".telegram").exists()


def test_scanner_strong_buy_alert_dedupes():
    from app.services import scanner, notify
    scanner._ALERTED.clear()
    row = {"symbol": "T.NS", "rating": "STRONG BUY", "composite": 82.0,
           "confidence": 71.0, "price": 100.0}
    sent = []
    with mock.patch.object(notify, "send", side_effect=lambda t: (sent.append(t), (True, None))[1]):
        scanner._alert_strong_buy(row)
        scanner._alert_strong_buy(row)          # same day → no second ping
    assert len(sent) == 1 and "STRONG BUY" in sent[0]
    scanner._ALERTED.clear()


def test_track_record_reports_auto_state(db_session):
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as cl:
        t = cl.get("/api/learn/track-record").json()
    assert "auto" in t and t["auto"]["enabled"] is False   # threads off in tests


def test_auto_learn_run_grading_updates_state():
    from app.engines import learning
    from app.services import auto_learn
    with mock.patch.object(learning, "evaluate_signals",
                           return_value={"evaluated": 3, "wins": 2, "losses": 1}):
        res = auto_learn.run_grading()
    assert res["evaluated"] == 3
    assert auto_learn.state["last_grade"] and auto_learn.state["last_grade_result"]["wins"] == 2
