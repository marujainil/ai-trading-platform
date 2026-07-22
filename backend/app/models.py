"""ORM models — the platform's persistent state.

Tables
------
account            single-row cash ledger for the paper account
risk_limits        single-row, user-configurable risk parameters
positions          currently open (netted) positions, with the signal snapshot at entry
trades             every executed order (buy/sell), immutable audit log
closed_trades      one row per completed round-trip — the Learning Engine's dataset
signals            every AI recommendation generated (research log)
backtest_runs      parameters + metrics of each backtest
equity_snapshots   equity curve of the paper account (drawdown tracking)
"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Account(Base):
    __tablename__ = "account"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cash: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RiskLimits(Base):
    __tablename__ = "risk_limits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    max_risk_per_trade_pct: Mapped[float] = mapped_column(Float, default=1.0)
    max_daily_loss_pct: Mapped[float] = mapped_column(Float, default=3.0)
    max_exposure_pct: Mapped[float] = mapped_column(Float, default=60.0)
    max_open_positions: Mapped[int] = mapped_column(Integer, default=10)
    block_high_volatility: Mapped[bool] = mapped_column(Boolean, default=True)
    volatility_atr_pct_threshold: Mapped[float] = mapped_column(Float, default=6.0)


class Position(Base):
    __tablename__ = "positions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    entry_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # signal context at entry
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Trade(Base):
    __tablename__ = "trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    side: Mapped[str] = mapped_column(String(4))  # BUY / SELL
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    broker: Mapped[str] = mapped_column(String(24), default="paper")
    status: Mapped[str] = mapped_column(String(16), default="FILLED")
    realized_pnl: Mapped[float | None] = mapped_column(Float, nullable=True)  # set on SELL fills
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ClosedTrade(Base):
    """One completed round-trip (or partial close). The Learning Engine's raw material."""
    __tablename__ = "closed_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    quantity: Mapped[int] = mapped_column(Integer)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    pnl: Mapped[float] = mapped_column(Float)
    pnl_pct: Mapped[float] = mapped_column(Float)
    holding_days: Mapped[float] = mapped_column(Float, default=0.0)
    entry_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # features at entry
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Signal(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(8))  # BUY / SELL / HOLD
    composite_score: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    risk_score: Mapped[float] = mapped_column(Float)
    entry: Mapped[float | None] = mapped_column(Float, nullable=True)
    stop_loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_1: Mapped[float | None] = mapped_column(Float, nullable=True)
    target_2: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # full analysis blob
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    params: Mapped[dict] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EquitySnapshot(Base):
    __tablename__ = "equity_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    equity: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    exposure: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AutopilotConfig(Base):
    """Singleton row: how the autonomous trader behaves."""
    __tablename__ = "autopilot_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    watchlist: Mapped[dict] = mapped_column(JSON, default=list)          # list[str]
    scan_interval_sec: Mapped[int] = mapped_column(Integer, default=900)  # 15 min
    min_composite: Mapped[float] = mapped_column(Float, default=68.0)
    min_confidence: Mapped[float] = mapped_column(Float, default=55.0)
    max_risk_score: Mapped[int] = mapped_column(Integer, default=7)
    max_new_positions_per_cycle: Mapped[int] = mapped_column(Integer, default=2)
    auto_manage_exits: Mapped[bool] = mapped_column(Boolean, default=True)
    # "watchlist" (only the symbols below) or "entire_market" (all NSE+BSE via Groww,
    # scanned in rotating slices so no single cycle is overwhelmed).
    universe_mode: Mapped[str] = mapped_column(String(16), default="watchlist")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)


class AutopilotEvent(Base):
    """Activity feed: every decision the autopilot makes, in plain English."""
    __tablename__ = "autopilot_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(12), index=True)   # BUY/SELL/EXIT/INFO/SKIP/ERROR/SCAN
    symbol: Mapped[str | None] = mapped_column(String(32), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SignalOutcome(Base):
    """Grade of a past signal: what price ACTUALLY did afterwards. The learning loop."""
    __tablename__ = "signal_outcomes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_id: Mapped[int] = mapped_column(Integer, index=True, unique=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    rating: Mapped[str] = mapped_column(String(16), index=True)
    regime_label: Mapped[str | None] = mapped_column(String(24), nullable=True)
    conviction_band: Mapped[str | None] = mapped_column(String(8), nullable=True)  # high/mid/low
    composite: Mapped[float | None] = mapped_column(Float, nullable=True)
    outcome: Mapped[str] = mapped_column(String(16))     # target_hit / stop_hit / expired
    ret_pct: Mapped[float] = mapped_column(Float)        # % move from entry at grade time
    days_held: Mapped[int] = mapped_column(Integer)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WatchItem(Base):
    """A followed symbol; rating changes here trigger Telegram alerts."""
    __tablename__ = "watch_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    last_rating: Mapped[str | None] = mapped_column(String(16), nullable=True)
    last_composite: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    prev_rating: Mapped[str | None] = mapped_column(String(16), nullable=True)
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
