from pydantic import BaseModel, Field


class OrderIn(BaseModel):
    symbol: str = Field(..., examples=["RELIANCE.NS"])
    side: str = Field(..., pattern="^(?i)(buy|sell)$", examples=["BUY"])
    quantity: int | None = Field(None, ge=1,
                                 description="Omit on BUY to auto-size from the signal via the Risk Manager")
    use_signal: bool = Field(True, description="Attach a fresh AI signal (entry/stop context) to the order")


class BacktestIn(BaseModel):
    symbol: str = Field(..., examples=["TCS.NS"])
    period: str = Field("5y", examples=["5y", "3y", "1y", "max"])
    params: dict = Field(default_factory=dict,
                         description="Overrides: entry_threshold, exit_threshold, risk_per_trade_pct, "
                                     "atr_stop_mult, atr_t1_mult, atr_t2_mult, starting_equity, cost_bps")


class RiskLimitsIn(BaseModel):
    max_risk_per_trade_pct: float | None = Field(None, gt=0, le=10)
    max_daily_loss_pct: float | None = Field(None, gt=0, le=25)
    max_exposure_pct: float | None = Field(None, gt=0, le=100)
    max_open_positions: int | None = Field(None, ge=1, le=100)
    block_high_volatility: bool | None = None
    volatility_atr_pct_threshold: float | None = Field(None, gt=0, le=50)


class AutopilotConfigIn(BaseModel):
    enabled: bool | None = None
    watchlist: list[str] | None = Field(None, max_length=40)
    scan_interval_sec: int | None = Field(None, ge=60, le=3600)
    min_composite: float | None = Field(None, ge=50, le=90)
    min_confidence: float | None = Field(None, ge=0, le=100)
    max_risk_score: int | None = Field(None, ge=1, le=10)
    max_new_positions_per_cycle: int | None = Field(None, ge=0, le=5)
    auto_manage_exits: bool | None = None
    universe_mode: str | None = Field(None, pattern="^(watchlist|entire_market)$")
