import math

from app.engines.backtest import run_backtest
from app.engines.learning import analyze_trades


def test_backtest_runs_and_metrics_sane(df_up):
    res = run_backtest(df_up, {"starting_equity": 1_000_000})
    m = res["metrics"]
    for key in ("total_trades", "win_rate", "sharpe_ratio", "sortino_ratio",
                "max_drawdown_pct", "cagr_pct", "expectancy_per_trade", "profit_factor",
                "avg_trade_pnl", "final_equity"):
        assert key in m
    assert m["max_drawdown_pct"] <= 0
    assert 0 <= m["win_rate"] <= 100
    assert math.isfinite(m["sharpe_ratio"])
    assert res["total_trades"] >= 1                       # uptrend should trigger entries
    assert res["equity_curve"][0]["equity"] == 1_000_000


def test_backtest_accounting_consistency(df_up):
    res = run_backtest(df_up)
    m = res["metrics"]
    # final equity should equal start + sum of all trade PnL (all positions closed)
    total_pnl_all = m["final_equity"] - res["params"]["starting_equity"]
    assert math.isclose(total_pnl_all, m["avg_trade_pnl"] * m["total_trades"], rel_tol=1e-6, abs_tol=1.0)


def test_backtest_protects_capital_in_downtrend(df_down):
    res = run_backtest(df_down)
    bh_return = (df_down["close"].iloc[-1] / df_down["close"].iloc[60] - 1) * 100
    assert bh_return < -30                                # sanity: it's a real downtrend
    # risk engine should keep losses far smaller than buy-and-hold
    assert res["metrics"]["total_return_pct"] > bh_return + 20
    assert res["metrics"]["total_return_pct"] > -15


def test_learning_insights_buckets():
    trades = []
    for i in range(12):  # trending entries win
        trades.append({"pnl": 1500, "pnl_pct": 2.5, "holding_days": 5,
                       "entry_snapshot": {"trend_label": "strong_uptrend", "rsi14": 58,
                                          "adx14": 30, "confidence": 75, "supertrend_dir": 1,
                                          "market_label": "uptrend"}})
    for i in range(8):   # choppy entries lose
        trades.append({"pnl": -900, "pnl_pct": -1.8, "holding_days": 3,
                       "entry_snapshot": {"trend_label": "sideways", "rsi14": 45,
                                          "adx14": 15, "confidence": 55, "supertrend_dir": 1,
                                          "market_label": "sideways"}})
    out = analyze_trades(trades)
    assert out["summary"]["total_trades"] == 20
    assert out["summary"]["win_rate"] == 60.0
    assert "trend_at_entry" in out["buckets"]
    assert any("sideways" in s for s in out["suggestions"])


def test_learning_empty():
    out = analyze_trades([])
    assert out["total_trades"] == 0


def test_api_boots():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json()["status"] == "ok"
        r = client.get("/api/risk/limits")
        assert r.status_code == 200 and "max_risk_per_trade_pct" in r.json()
