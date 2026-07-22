"""Central configuration. Everything has a safe default so the app runs with zero setup."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI Trading Platform"
    env: str = "development"

    # SQLite by default; docker-compose overrides this with Postgres.
    database_url: str = "sqlite:///./trading.db"

    # Optional Redis cache; falls back to in-memory cache when unset/unreachable.
    redis_url: str | None = None

    # Optional LLM-powered news sentiment (keyword fallback used when absent).
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-sonnet-4-6"

    # Optional Telegram notifications from the Autopilot (see README).
    background_jobs_enabled: bool = True
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    # Optional Groww API — real NSE+BSE data (and later, live orders). See README.
    groww_api_token: str | None = None
    # How many symbols the "scan entire market" universe may hold per cycle.
    # The full NSE+BSE list is ~7000; scanning all of them takes many minutes and
    # thousands of API calls, so we cap and rotate through it in slices.
    universe_scan_slice: int = 250

    # Paper account
    starting_cash: float = 1_000_000.0
    base_currency: str = "INR"

    # Broad-market context (^NSEI = NIFTY 50, ^GSPC = S&P 500, ...)
    market_index_symbol: str = "^NSEI"

    # --- Default risk limits (editable at runtime via /api/risk/limits) ---
    max_risk_per_trade_pct: float = 1.0      # % of equity risked between entry and stop
    max_daily_loss_pct: float = 3.0          # halt new trades after this realized daily loss
    max_exposure_pct: float = 60.0           # max % of equity deployed at once
    max_open_positions: int = 10
    block_high_volatility: bool = True
    volatility_atr_pct_threshold: float = 6.0  # block entries when ATR% of price exceeds this


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
