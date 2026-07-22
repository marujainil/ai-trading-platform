from app.engines.risk import Limits, RiskContext, position_size, validate_entry


def ctx(**kw) -> RiskContext:
    base = dict(equity=1_000_000, cash=1_000_000, exposure=0, open_positions=0,
                todays_realized_pnl=0.0, limits=Limits())
    base.update(kw)
    return RiskContext(**base)


def test_position_size_risk_math():
    c = ctx()
    out = position_size(c, entry=100.0, stop=95.0)   # ₹5 risk/share, 1% of 1M = ₹10,000
    assert out["quantity"] == 2000


def test_position_size_capped_by_cash():
    c = ctx(cash=50_000, equity=1_000_000)
    out = position_size(c, entry=100.0, stop=95.0)
    assert out["quantity"] == 500                    # cash cap: 50,000 / 100


def test_validate_blocks_oversized_risk():
    v = validate_entry(ctx(), entry=100, stop=95, quantity=5000)  # ₹25k risk > ₹10k limit
    assert not v["allowed"]
    assert any("per-trade" in msg.lower() for msg in v["violations"])


def test_validate_blocks_after_daily_loss():
    v = validate_entry(ctx(todays_realized_pnl=-40_000), entry=100, stop=95, quantity=100)
    assert not v["allowed"]
    assert any("daily loss" in msg.lower() for msg in v["violations"])


def test_validate_blocks_exposure_cap():
    c = ctx(exposure=590_000, cash=410_000)          # cap 60% of 1M = 600k
    v = validate_entry(c, entry=100, stop=99, quantity=500)  # +50k exposure
    assert not v["allowed"]
    assert any("exposure" in msg.lower() for msg in v["violations"])


def test_validate_blocks_high_volatility():
    v = validate_entry(ctx(), entry=100, stop=95, quantity=100, atr_pct=8.0)
    assert not v["allowed"]
    assert any("volatility" in msg.lower() for msg in v["violations"])


def test_validate_allows_clean_trade():
    v = validate_entry(ctx(), entry=100, stop=95, quantity=1000, atr_pct=2.0)
    assert v["allowed"] and not v["violations"]
