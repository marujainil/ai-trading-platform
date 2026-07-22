"""Portfolio Manager — order lifecycle and account analytics.

Order path:  signal → RiskContext built from live account state → Risk Manager
validation → broker fill → ledger updates (cash, position, trade log, closed-trade
record for the Learning Engine, equity snapshot).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models
from app.brokers.base import OrderRequest
from app.brokers.paper import PaperBroker
from app.config import settings
from app.data import market_data
from app.engines import risk as riskmod

log = logging.getLogger(__name__)
_paper = PaperBroker()


def get_broker(name: str = "paper"):
    if name == "paper":
        return _paper
    raise ValueError(f"Broker '{name}' not enabled. Only 'paper' is active in Module 1 "
                     "(see app/brokers/zerodha.py to add live adapters).")


# --------------------------------------------------------------------------- #
# Bootstrap / state helpers
# --------------------------------------------------------------------------- #

def ensure_seed(db: Session) -> None:
    if not db.query(models.Account).first():
        db.add(models.Account(cash=settings.starting_cash, currency=settings.base_currency))
    if not db.query(models.RiskLimits).first():
        db.add(models.RiskLimits(
            max_risk_per_trade_pct=settings.max_risk_per_trade_pct,
            max_daily_loss_pct=settings.max_daily_loss_pct,
            max_exposure_pct=settings.max_exposure_pct,
            max_open_positions=settings.max_open_positions,
            block_high_volatility=settings.block_high_volatility,
            volatility_atr_pct_threshold=settings.volatility_atr_pct_threshold,
        ))
    db.commit()


def get_limits(db: Session) -> models.RiskLimits:
    ensure_seed(db)
    return db.query(models.RiskLimits).first()


def _todays_realized_pnl(db: Session) -> float:
    today = datetime.now(timezone.utc).date()
    val = (db.query(func.coalesce(func.sum(models.Trade.realized_pnl), 0.0))
             .filter(models.Trade.side == "SELL",
                     func.date(models.Trade.created_at) == today.isoformat())
             .scalar())
    return float(val or 0.0)


def _positions_with_prices(db: Session) -> list[dict]:
    out = []
    for pos in db.query(models.Position).all():
        try:
            px = market_data.last_price(pos.symbol)
        except market_data.DataError:
            px = pos.avg_price
        mv = px * pos.quantity
        out.append({
            "symbol": pos.symbol, "quantity": pos.quantity,
            "avg_price": round(pos.avg_price, 2), "last_price": round(px, 2),
            "market_value": round(mv, 2),
            "unrealized_pnl": round((px - pos.avg_price) * pos.quantity, 2),
            "unrealized_pnl_pct": round(100 * (px / pos.avg_price - 1), 2) if pos.avg_price else 0.0,
            "sector": pos.sector, "stop_loss": pos.stop_loss,
            "target_1": pos.target_1, "target_2": pos.target_2,
            "opened_at": pos.opened_at.isoformat() if pos.opened_at else None,
        })
    return out


def build_risk_context(db: Session) -> riskmod.RiskContext:
    ensure_seed(db)
    account = db.query(models.Account).first()
    lim = get_limits(db)
    positions = _positions_with_prices(db)
    exposure = sum(p["market_value"] for p in positions)
    return riskmod.RiskContext(
        equity=account.cash + exposure,
        cash=account.cash,
        exposure=exposure,
        open_positions=len(positions),
        todays_realized_pnl=_todays_realized_pnl(db),
        limits=riskmod.Limits(
            max_risk_per_trade_pct=lim.max_risk_per_trade_pct,
            max_daily_loss_pct=lim.max_daily_loss_pct,
            max_exposure_pct=lim.max_exposure_pct,
            max_open_positions=lim.max_open_positions,
            block_high_volatility=lim.block_high_volatility,
            volatility_atr_pct_threshold=lim.volatility_atr_pct_threshold,
        ),
    )


# --------------------------------------------------------------------------- #
# Order execution (paper)
# --------------------------------------------------------------------------- #

def execute_order(db: Session, symbol: str, side: str, quantity: int | None,
                  signal: dict | None = None, broker_name: str = "paper") -> dict:
    """Risk-checked order execution. For BUY with quantity=None, size from the
    signal's entry/stop via the Risk Manager."""
    ensure_seed(db)
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")

    broker = get_broker(broker_name)
    ctx = build_risk_context(db)
    account = db.query(models.Account).first()

    if side == "BUY":
        entry = signal["entry"] if signal else broker.get_quote(symbol)
        stop = signal["stop_loss"] if signal else entry * 0.97
        atr_pct = (signal or {}).get("technical", {}).get("atr_pct")

        if quantity is None:
            sizing = riskmod.position_size(ctx, entry, stop)
            quantity = sizing["quantity"]
            if quantity <= 0:
                return {"status": "REJECTED", "reason": "Position size resolved to 0 "
                        f"(caps: {sizing.get('caps')})", "risk": sizing}

        verdict = riskmod.validate_entry(ctx, entry, stop, quantity, atr_pct=atr_pct)
        if not verdict["allowed"]:
            return {"status": "REJECTED", "reason": "; ".join(verdict["violations"]),
                    "risk": verdict}

        result = broker.place_order(OrderRequest(symbol=symbol, side="BUY", quantity=quantity))
        if not result.ok:
            return {"status": "REJECTED", "reason": result.message}
        px = result.fill_price

        pos = db.query(models.Position).filter_by(symbol=symbol).first()
        if pos:
            total_cost = pos.avg_price * pos.quantity + px * quantity
            pos.quantity += quantity
            pos.avg_price = total_cost / pos.quantity
        else:
            snap = _entry_snapshot(signal)
            sector = (signal or {}).get("fundamental", {}).get("sector")
            pos = models.Position(symbol=symbol, quantity=quantity, avg_price=px, sector=sector,
                                  stop_loss=(signal or {}).get("stop_loss"),
                                  target_1=(signal or {}).get("target_1"),
                                  target_2=(signal or {}).get("target_2"),
                                  entry_snapshot=snap)
            db.add(pos)

        account.cash -= px * quantity
        trade = models.Trade(symbol=symbol, side="BUY", quantity=quantity, price=px,
                             broker=broker.name, note=(signal or {}).get("action"))
        db.add(trade)
        db.commit()
        _snapshot_equity(db)
        return {"status": "FILLED", "side": "BUY", "symbol": symbol, "quantity": quantity,
                "price": round(px, 2), "cash_after": round(account.cash, 2),
                "risk": verdict}

    # ---- SELL ----
    pos = db.query(models.Position).filter_by(symbol=symbol).first()
    if not pos or pos.quantity <= 0:
        return {"status": "REJECTED", "reason": f"No open position in {symbol}"}
    quantity = quantity or pos.quantity
    if quantity > pos.quantity:
        return {"status": "REJECTED", "reason": f"Sell qty {quantity} exceeds position {pos.quantity}"}

    result = broker.place_order(OrderRequest(symbol=symbol, side="SELL", quantity=quantity))
    if not result.ok:
        return {"status": "REJECTED", "reason": result.message}
    px = result.fill_price

    realized = (px - pos.avg_price) * quantity
    holding_days = max(0.0, (datetime.now(timezone.utc) - _aware(pos.opened_at)).total_seconds() / 86400)

    db.add(models.ClosedTrade(symbol=symbol, quantity=quantity, entry_price=pos.avg_price,
                              exit_price=px, pnl=realized,
                              pnl_pct=round(100 * (px / pos.avg_price - 1), 2) if pos.avg_price else 0.0,
                              holding_days=round(holding_days, 2),
                              entry_snapshot=pos.entry_snapshot, opened_at=pos.opened_at))
    db.add(models.Trade(symbol=symbol, side="SELL", quantity=quantity, price=px,
                        broker=broker.name, realized_pnl=realized))
    account.cash += px * quantity
    pos.quantity -= quantity
    if pos.quantity == 0:
        db.delete(pos)
    db.commit()
    _snapshot_equity(db)
    return {"status": "FILLED", "side": "SELL", "symbol": symbol, "quantity": quantity,
            "price": round(px, 2), "realized_pnl": round(realized, 2),
            "cash_after": round(account.cash, 2)}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _entry_snapshot(signal: dict | None) -> dict | None:
    if not signal:
        return None
    tech = signal.get("technical", {})
    ind = tech.get("indicators", {})
    return {
        "composite": signal.get("composite_score"),
        "confidence": signal.get("confidence"),
        "risk_score": signal.get("risk_score"),
        "trend_label": tech.get("trend", {}).get("label"),
        "market_label": signal.get("market_regime", {}).get("label"),
        "rsi14": ind.get("rsi14"),
        "adx14": ind.get("adx14"),
        "supertrend_dir": ind.get("supertrend_dir"),
        "atr_pct": tech.get("atr_pct"),
        "news_label": signal.get("news", {}).get("label"),
    }


# --------------------------------------------------------------------------- #
# Portfolio view
# --------------------------------------------------------------------------- #

def _snapshot_equity(db: Session) -> None:
    account = db.query(models.Account).first()
    positions = _positions_with_prices(db)
    exposure = sum(p["market_value"] for p in positions)
    db.add(models.EquitySnapshot(equity=account.cash + exposure, cash=account.cash, exposure=exposure))
    db.commit()


def portfolio_summary(db: Session) -> dict:
    ensure_seed(db)
    account = db.query(models.Account).first()
    positions = _positions_with_prices(db)
    exposure = sum(p["market_value"] for p in positions)
    equity = account.cash + exposure
    unrealized = sum(p["unrealized_pnl"] for p in positions)

    sectors: dict[str, float] = {}
    for p in positions:
        sectors[p["sector"] or "Unknown"] = sectors.get(p["sector"] or "Unknown", 0.0) + p["market_value"]
    sector_alloc = {k: round(100 * v / exposure, 1) for k, v in sectors.items()} if exposure else {}

    snaps = [s.equity for s in db.query(models.EquitySnapshot)
             .order_by(models.EquitySnapshot.created_at).all()] or [equity]
    peak, max_dd = snaps[0], 0.0
    for e in snaps + [equity]:
        peak = max(peak, e)
        if peak > 0:
            max_dd = min(max_dd, e / peak - 1)

    realized_total = float(db.query(func.coalesce(func.sum(models.Trade.realized_pnl), 0.0)).scalar() or 0.0)

    return {
        "currency": account.currency,
        "cash": round(account.cash, 2),
        "exposure": round(exposure, 2),
        "equity": round(equity, 2),
        "exposure_pct_of_equity": round(100 * exposure / equity, 1) if equity else 0.0,
        "unrealized_pnl": round(unrealized, 2),
        "realized_pnl_total": round(realized_total, 2),
        "realized_pnl_today": round(_todays_realized_pnl(db), 2),
        "total_return_pct": round(100 * (equity / settings.starting_cash - 1), 2),
        "max_drawdown_pct": round(100 * max_dd, 2),
        "open_positions": len(positions),
        "sector_allocation_pct": sector_alloc,
        "positions": positions,
    }
