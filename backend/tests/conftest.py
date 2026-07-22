import numpy as np
import pandas as pd
import pytest


def make_ohlcv(n: int = 400, drift: float = 0.0006, vol: float = 0.015, seed: int = 42,
               start_price: float = 100.0) -> pd.DataFrame:
    """Deterministic synthetic daily OHLCV (geometric random walk + mild cycle)."""
    rng = np.random.default_rng(seed)
    rets = drift + vol * rng.standard_normal(n) + 0.002 * np.sin(np.arange(n) / 15)
    close = start_price * np.exp(np.cumsum(rets))
    spread = np.abs(vol * close * rng.standard_normal(n)) + close * 0.002
    high = close + spread * rng.uniform(0.3, 1.0, n)
    low = close - spread * rng.uniform(0.3, 1.0, n)
    open_ = low + (high - low) * rng.uniform(0.2, 0.8, n)
    volume = rng.integers(200_000, 2_000_000, n).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume},
                        index=idx)


@pytest.fixture
def df_up() -> pd.DataFrame:
    return make_ohlcv(drift=0.0012, seed=7)     # trending up


@pytest.fixture
def df_flat() -> pd.DataFrame:
    return make_ohlcv(drift=0.0, seed=11)


@pytest.fixture
def df_down() -> pd.DataFrame:
    return make_ohlcv(drift=-0.0012, seed=13)


@pytest.fixture(autouse=True)
def _fix_fx_rate():
    """Pin USD→INR at 84.0 so INR conversion is deterministic and offline."""
    import time as _t
    from app.config import settings as _st
    from app.data import market_data as _md
    _st.background_jobs_enabled = False
    _md._FX.update(rate=84.0, ts=_t.time() + 10**9)
    yield


@pytest.fixture(autouse=True)
def _fresh_db_and_state():
    """Isolate every test: wipe the SQLite tables and the autopilot's in-memory timers."""
    from app.database import Base, engine
    from app.services import autopilot as _ap
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    _ap._state.update({"last_run": 0.0, "running": False, "last_error": None,
                       "last_overnight": 0.0})
    yield


@pytest.fixture
def db_session():
    """A real session against the (fresh) test database."""
    from app.database import SessionLocal, engine
    from app import models
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
