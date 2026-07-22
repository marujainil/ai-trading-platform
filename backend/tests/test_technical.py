import numpy as np

from app.engines import technical as ta
from app.engines.patterns import detect_candlestick_patterns


def test_sma_matches_rolling_mean(df_flat):
    close = df_flat["close"]
    assert np.allclose(ta.sma(close, 20).iloc[-1], close.tail(20).mean())


def test_ema_matches_pandas(df_flat):
    close = df_flat["close"]
    assert np.isclose(ta.ema(close, 21).iloc[-1], close.ewm(span=21, adjust=False).mean().iloc[-1])


def test_rsi_bounds(df_up, df_down, df_flat):
    for df in (df_up, df_down, df_flat):
        r = ta.rsi(df["close"]).dropna()
        assert r.between(0, 100).all()
    assert ta.rsi(df_up["close"]).iloc[-50:].mean() > ta.rsi(df_down["close"]).iloc[-50:].mean()


def test_atr_positive(df_flat):
    assert (ta.atr(df_flat).iloc[20:] > 0).all()


def test_bollinger_ordering(df_flat):
    up, mid, low = ta.bollinger(df_flat["close"])
    tail = slice(-50, None)
    assert (up.iloc[tail] >= mid.iloc[tail]).all() and (mid.iloc[tail] >= low.iloc[tail]).all()


def test_supertrend_direction_values_and_bias(df_up, df_down):
    _, dir_up = ta.supertrend(df_up)
    _, dir_dn = ta.supertrend(df_down)
    assert set(np.unique(dir_up)) <= {1, -1}
    assert dir_up.iloc[-60:].mean() > dir_dn.iloc[-60:].mean()


def test_adx_di_nonnegative(df_up):
    adx_v, pdi, mdi = ta.adx(df_up)
    assert (adx_v.iloc[30:] >= 0).all() and (pdi.iloc[30:] >= 0).all() and (mdi.iloc[30:] >= 0).all()


def test_score_vector_range_and_bias(df_up, df_down):
    s_up, _ = ta.technical_score_vector(df_up)
    s_dn, _ = ta.technical_score_vector(df_down)
    assert s_up.between(0, 100).all() and s_dn.between(0, 100).all()
    assert s_up.iloc[-60:].mean() > s_dn.iloc[-60:].mean()


def test_full_analysis_shape(df_up):
    out = ta.full_technical_analysis(df_up, patterns=detect_candlestick_patterns(df_up))
    for key in ("last_close", "score", "breakdown", "trend", "breakout", "atr",
                "atr_pct", "indicators", "levels"):
        assert key in out
    assert 0 <= out["score"] <= 100
    ind = out["indicators"]
    assert ind["bollinger"]["upper"] >= ind["bollinger"]["lower"]
    fib = out["levels"]["fibonacci"]          # fib_position: real prices + zone
    assert fib["available"] and fib["swing_low"] <= fib["level_618"] <= fib["swing_high"]


def test_support_resistance_sides(df_flat):
    sr = ta.support_resistance(df_flat)
    last = float(df_flat["close"].iloc[-1])
    assert all(s["level"] < last for s in sr["support"])
    assert all(r["level"] > last for r in sr["resistance"])
