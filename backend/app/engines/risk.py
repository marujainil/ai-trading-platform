"""Risk Manager — the veto layer between signals and orders.

Enforces (all user-configurable, persisted in `risk_limits`):
  * max % of equity risked per trade (entry→stop distance × quantity)
  * max realized daily loss before new entries are halted
  * max total exposure as % of equity
  * max number of open positions
  * volatility circuit-breaker (block entries when ATR% exceeds a threshold)

Pure functions over a `RiskContext` snapshot → trivial to unit-test and reuse
in both live paper trading and backtests.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Limits:
    max_risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_exposure_pct: float = 60.0
    max_open_positions: int = 10
    block_high_volatility: bool = True
    volatility_atr_pct_threshold: float = 6.0


@dataclass
class RiskContext:
    equity: float                      # cash + mark-to-market positions
    cash: float
    exposure: float                    # Σ |qty × last_price|
    open_positions: int
    todays_realized_pnl: float = 0.0
    limits: Limits = field(default_factory=Limits)


def position_size(ctx: RiskContext, entry: float, stop: float) -> dict:
    """Quantity such that (entry − stop) × qty ≈ equity × max_risk_per_trade_pct,
    additionally capped by available cash and the exposure headroom."""
    per_share_risk = abs(entry - stop)
    if per_share_risk <= 0 or entry <= 0:
        return {"quantity": 0, "reason": "Invalid entry/stop (zero risk per share)"}

    risk_amount = ctx.equity * ctx.limits.max_risk_per_trade_pct / 100.0
    qty_by_risk = math.floor(risk_amount / per_share_risk)
    qty_by_cash = math.floor(ctx.cash / entry)
    exposure_headroom = max(0.0, ctx.equity * ctx.limits.max_exposure_pct / 100.0 - ctx.exposure)
    qty_by_exposure = math.floor(exposure_headroom / entry)

    qty = max(0, min(qty_by_risk, qty_by_cash, qty_by_exposure))
    return {
        "quantity": qty,
        "risk_amount": round(risk_amount, 2),
        "per_share_risk": round(per_share_risk, 2),
        "caps": {"by_risk": qty_by_risk, "by_cash": qty_by_cash, "by_exposure": qty_by_exposure},
    }


def validate_entry(ctx: RiskContext, entry: float, stop: float, quantity: int,
                   atr_pct: float | None = None) -> dict:
    """Returns {"allowed": bool, "violations": [...], "checks": [...]}."""
    violations: list[str] = []
    checks: list[str] = []
    lim = ctx.limits

    if quantity <= 0:
        violations.append("Quantity must be positive")

    # Per-trade risk
    trade_risk = abs(entry - stop) * quantity
    max_risk = ctx.equity * lim.max_risk_per_trade_pct / 100.0
    if trade_risk > max_risk * 1.001:
        violations.append(f"Trade risk ₹{trade_risk:,.0f} exceeds per-trade limit ₹{max_risk:,.0f} "
                          f"({lim.max_risk_per_trade_pct}% of equity)")
    else:
        checks.append(f"Per-trade risk OK (₹{trade_risk:,.0f} ≤ ₹{max_risk:,.0f})")

    # Daily loss halt
    max_daily_loss = ctx.equity * lim.max_daily_loss_pct / 100.0
    if ctx.todays_realized_pnl <= -max_daily_loss:
        violations.append(f"Daily loss limit hit (realized {ctx.todays_realized_pnl:,.0f} "
                          f"≤ -₹{max_daily_loss:,.0f}) — new entries halted for today")
    else:
        checks.append("Daily loss limit OK")

    # Exposure cap
    new_exposure = ctx.exposure + entry * quantity
    max_exposure = ctx.equity * lim.max_exposure_pct / 100.0
    if new_exposure > max_exposure * 1.001:
        violations.append(f"Exposure after trade ₹{new_exposure:,.0f} exceeds cap ₹{max_exposure:,.0f} "
                          f"({lim.max_exposure_pct}% of equity)")
    else:
        checks.append(f"Exposure OK ({new_exposure / max(ctx.equity, 1):.0%} of equity after fill)")

    # Position count
    if ctx.open_positions + 1 > lim.max_open_positions:
        violations.append(f"Max open positions ({lim.max_open_positions}) reached")
    else:
        checks.append("Position count OK")

    # Cash
    if entry * quantity > ctx.cash + 1e-6:
        violations.append(f"Insufficient cash (need ₹{entry * quantity:,.0f}, have ₹{ctx.cash:,.0f})")
    else:
        checks.append("Cash OK")

    # Volatility circuit-breaker
    if lim.block_high_volatility and atr_pct is not None and atr_pct > lim.volatility_atr_pct_threshold:
        violations.append(f"Volatility block: ATR {atr_pct:.1f}% > threshold "
                          f"{lim.volatility_atr_pct_threshold:.1f}%")
    else:
        checks.append("Volatility OK")

    return {"allowed": not violations, "violations": violations, "checks": checks}
