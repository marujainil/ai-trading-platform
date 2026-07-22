"""Technical Analysis Engine.

Implements: SMA, EMA, VWAP, MACD, RSI, ADX(+DI/-DI), ATR, Supertrend, Bollinger
Bands, Ichimoku, Fibonacci retracements, Volume Profile, Support/Resistance,
trend detection, breakout detection — plus a 0-100 technical score.

`technical_score_vector` is fully vectorized so the Backtesting Engine scores
every historical bar with exactly the same logic the live engine uses.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Core indicators
# --------------------------------------------------------------------------- #

def sma(series: pd.Series, n: int) -> pd.Series:
    return series.rolling(n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """VWAP anchored to the start of the provided window (rolling-anchored)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].replace(0, np.nan)
    return (tp * vol).cumsum() / vol.cumsum()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(close, fast) - ema(close, slow)
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / n, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / n, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    return out.fillna(100.0).clip(0, 100)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14):
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr_ = atr(df, n).replace(0, np.nan)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr_
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean().fillna(0), plus_di.fillna(0), minus_di.fillna(0)


def bollinger(close: pd.Series, n: int = 20, k: float = 2.0):
    mid = sma(close, n)
    sd = close.rolling(n).std()
    return mid + k * sd, mid, mid - k * sd


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0):
    """Returns (supertrend_line, direction) with direction 1=bullish, -1=bearish."""
    atr_ = atr(df, period).to_numpy()
    hl2 = ((df["high"] + df["low"]) / 2.0).to_numpy()
    close = df["close"].to_numpy()
    n = len(df)

    upper = hl2 + multiplier * atr_
    lower = hl2 - multiplier * atr_
    f_upper = upper.copy()
    f_lower = lower.copy()
    st = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)

    st[0] = upper[0]
    direction[0] = -1
    for i in range(1, n):
        f_upper[i] = upper[i] if (upper[i] < f_upper[i - 1] or close[i - 1] > f_upper[i - 1]) else f_upper[i - 1]
        f_lower[i] = lower[i] if (lower[i] > f_lower[i - 1] or close[i - 1] < f_lower[i - 1]) else f_lower[i - 1]
        if st[i - 1] == f_upper[i - 1]:
            st[i], direction[i] = (f_upper[i], -1) if close[i] <= f_upper[i] else (f_lower[i], 1)
        else:
            st[i], direction[i] = (f_lower[i], 1) if close[i] >= f_lower[i] else (f_upper[i], -1)

    idx = df.index
    return pd.Series(st, index=idx), pd.Series(direction, index=idx)


# --------------------------------------------------------------------------- #
# Support / resistance via pivot clustering
# --------------------------------------------------------------------------- #

def _pivot_points(series: pd.Series, order: int, kind: str) -> pd.Series:
    roll = series.rolling(2 * order + 1, center=True)
    extreme = roll.max() if kind == "high" else roll.min()
    mask = (series == extreme).fillna(False)
    return series[mask]


def _cluster(levels: list[float], ref_price: float, tol: float) -> list[dict]:
    if not levels:
        return []
    levels = sorted(levels)
    clusters: list[list[float]] = [[levels[0]]]
    for lv in levels[1:]:
        if abs(lv - clusters[-1][-1]) <= tol * ref_price:
            clusters[-1].append(lv)
        else:
            clusters.append([lv])
    return [{"level": round(float(np.mean(c)), 2), "touches": len(c)} for c in clusters]


def support_resistance(df: pd.DataFrame, lookback: int = 180, order: int = 7, tol: float = 0.012) -> dict:
    win = df.tail(lookback)
    last = float(win["close"].iloc[-1])
    highs = _pivot_points(win["high"], order, "high").tolist()
    lows = _pivot_points(win["low"], order, "low").tolist()
    clusters = _cluster(highs + lows, last, tol)
    support = sorted([c for c in clusters if c["level"] < last], key=lambda c: last - c["level"])[:3]
    resistance = sorted([c for c in clusters if c["level"] > last], key=lambda c: c["level"] - last)[:3]
    return {"support": support, "resistance": resistance}


# --------------------------------------------------------------------------- #
# Trend / breakout detection
# --------------------------------------------------------------------------- #

def detect_trend(df: pd.DataFrame) -> dict:
    close = df["close"]
    e20, e50, e200 = ema(close, 20).iloc[-1], ema(close, 50).iloc[-1], ema(close, 200).iloc[-1]
    adx_v = float(adx(df)[0].iloc[-1])
    last = float(close.iloc[-1])

    if last > e50 > e200 and e20 > e50:
        label = "strong_uptrend" if adx_v > 25 else "uptrend"
    elif last < e50 < e200 and e20 < e50:
        label = "strong_downtrend" if adx_v > 25 else "downtrend"
    else:
        label = "sideways"
    return {"label": label, "adx": round(adx_v, 1),
            "ema20": round(float(e20), 2), "ema50": round(float(e50), 2), "ema200": round(float(e200), 2)}


def detect_breakout(df: pd.DataFrame, lookback: int = 20) -> dict:
    if len(df) < lookback + 2:
        return {"breakout": False}
    prior_high = float(df["high"].iloc[-(lookback + 1):-1].max())
    prior_low = float(df["low"].iloc[-(lookback + 1):-1].min())
    last_close = float(df["close"].iloc[-1])
    vol = float(df["volume"].iloc[-1])
    avg_vol = float(df["volume"].iloc[-(lookback + 1):-1].mean() or 0)
    vol_surge = bool(avg_vol > 0 and vol > 1.5 * avg_vol)

    win = df["close"].tail(15)
    consolidating = bool((win.max() - win.min()) / max(win.mean(), 1e-9) < 0.045)

    hi_52w = float(df["high"].tail(252).max())
    return {
        "breakout": bool(last_close > prior_high),
        "breakdown": bool(last_close < prior_low),
        "volume_surge": vol_surge,
        "consolidation_base": consolidating,
        "range_high": round(prior_high, 2),
        "range_low": round(prior_low, 2),
        "near_52w_high": bool(last_close >= 0.95 * hi_52w),
    }


# --------------------------------------------------------------------------- #
# Scoring — shared by the live Decision Engine and the Backtesting Engine
# --------------------------------------------------------------------------- #

def technical_score_vector(df: pd.DataFrame) -> tuple[pd.Series, dict]:
    """0-100 score per bar + the auxiliary series a backtest needs."""
    close = df["close"]
    e20, e50, e200 = ema(close, 20), ema(close, 50), ema(close, 200)
    _, _, macd_hist = macd(close)
    rsi_v = rsi(close)
    adx_v, pdi, mdi = adx(df)
    _, st_dir = supertrend(df)
    atr_v = atr(df)

    score = pd.Series(50.0, index=df.index)
    score += np.where(close > e200, 8, -8)
    score += np.where(e20 > e50, 7, -7)
    score += np.where(macd_hist > 0, 6, -6)
    score += np.where(st_dir == 1, 8, -8)
    score += np.where((adx_v > 25) & (pdi > mdi), 6, np.where((adx_v > 25) & (mdi > pdi), -6, 0))
    score += np.where(rsi_v >= 75, -6,
             np.where(rsi_v >= 55, 5,
             np.where(rsi_v >= 45, 0,
             np.where(rsi_v >= 30, -3, -5))))
    score = score.clip(0, 100)

    aux = {"atr": atr_v, "st_dir": st_dir, "rsi": rsi_v, "adx": adx_v,
           "ema20": e20, "ema50": e50, "ema200": e200, "macd_hist": macd_hist}
    return score, aux


def full_technical_analysis(df: pd.DataFrame, patterns: list[dict] | None = None,
                            bench_df: pd.DataFrame | None = None) -> dict:
    """Latest-bar analysis: score with reasoning breakdown, indicators, and key levels.
    Advanced layer: weekly-timeframe confluence, relative strength vs benchmark,
    OBV/volume accumulation, momentum quality (ROC), Bollinger squeeze."""
    patterns = patterns or []
    close = df["close"]
    last = float(close.iloc[-1])

    base_score, aux = technical_score_vector(df)
    score = float(base_score.iloc[-1])
    breakdown: list[str] = []

    e20, e50, e200 = aux["ema20"].iloc[-1], aux["ema50"].iloc[-1], aux["ema200"].iloc[-1]
    breakdown.append(f"Price {'above' if last > e200 else 'below'} EMA200 ({e200:.2f})")
    breakdown.append(f"EMA20 {'>' if e20 > e50 else '<'} EMA50 — {'bullish' if e20 > e50 else 'bearish'} short-term structure")
    breakdown.append(f"MACD histogram {'positive' if aux['macd_hist'].iloc[-1] > 0 else 'negative'}")
    breakdown.append(f"Supertrend {'bullish' if aux['st_dir'].iloc[-1] == 1 else 'bearish'}")
    breakdown.append(f"RSI {aux['rsi'].iloc[-1]:.1f}, ADX {aux['adx'].iloc[-1]:.1f}")

    vwap_v = float(vwap(df).iloc[-1])
    score += 3 if last > vwap_v else -3
    breakdown.append(f"Price {'above' if last > vwap_v else 'below'} anchored VWAP ({vwap_v:.2f})")

    brk = detect_breakout(df)
    if brk.get("breakout"):
        score += 6
        breakdown.append(f"Breakout above {brk['range_high']} (20-bar range)"
                         + (" on surging volume" if brk.get("volume_surge") else ""))
    if brk.get("breakdown"):
        score -= 6
        breakdown.append(f"Breakdown below {brk['range_low']} (20-bar range)")
    if brk.get("consolidation_base") and not brk.get("breakout"):
        breakdown.append("Tight consolidation base forming (watch for breakout)")

    for p in patterns[:3]:
        score += 4 if p["bias"] == "bullish" else -4 if p["bias"] == "bearish" else 0
        breakdown.append(f"Candlestick: {p['name']} ({p['bias']})")

    sr = support_resistance(df)
    if sr["support"] and (last - sr["support"][0]["level"]) / last < 0.02:
        score += 3
        breakdown.append(f"Trading near support {sr['support'][0]['level']}")
    if sr["resistance"] and (sr["resistance"][0]["level"] - last) / last < 0.02:
        score -= 3
        breakdown.append(f"Just below resistance {sr['resistance'][0]['level']}")

    # ---------- advanced layer ----------
    wk = weekly_context(df)
    if wk.get("available"):
        if wk["trend"] == "up" and wk["supertrend_dir"] == 1:
            score += 6
            breakdown.append("Weekly timeframe agrees: higher-timeframe uptrend (multi-timeframe confluence)")
        elif wk["trend"] == "down" and wk["supertrend_dir"] == -1:
            score -= 6
            breakdown.append("Weekly timeframe is bearish — daily signals face higher-timeframe headwind")
        else:
            breakdown.append("Weekly timeframe mixed — no higher-timeframe confirmation yet")

    rs = relative_strength(df, bench_df)
    if rs is not None:
        if rs >= 5:
            score += 5
            breakdown.append(f"Relative strength: outperforming the benchmark by {rs:+.1f}% over 3 months")
        elif rs <= -5:
            score -= 5
            breakdown.append(f"Relative weakness: lagging the benchmark by {rs:+.1f}% over 3 months")

    obv_s = obv_slope(df)
    udr = updown_volume_ratio(df)
    if obv_s > 0.05 and udr >= 1.2:
        score += 4
        breakdown.append(f"Volume confirms: OBV rising, up/down volume {udr}× (accumulation)")
    elif obv_s < -0.05 and udr <= 0.8:
        score -= 4
        breakdown.append(f"Volume warns: OBV falling, up/down volume {udr}× (distribution)")

    roc60 = roc(close, 60)
    if 8 <= roc60 <= 45:
        score += 4
        breakdown.append(f"Healthy momentum: +{roc60}% over 60 bars (strong but not overextended)")
    elif roc60 > 70:
        score -= 5
        breakdown.append(f"Overextended: +{roc60}% in 60 bars — elevated pullback risk")

    bwp = bb_bandwidth_pct(close)
    if bwp <= 20 and brk.get("breakout"):
        score += 4
        breakdown.append("Volatility squeeze resolving upward — expansion moves often follow")
    elif bwp <= 15:
        breakdown.append("Bollinger squeeze: volatility compressed — a large move is brewing")

    p52 = pos_52w_pct(df)

    div = detect_rsi_divergence(df)
    if div["type"] == "bearish":
        score -= 5
        breakdown.append(f"⚠ Bearish RSI divergence — {div['detail']} (rallies on fading momentum often stall)")
    elif div["type"] == "bullish":
        score += 4
        breakdown.append(f"Bullish RSI divergence — {div['detail']} (selling pressure exhausting)")

    adx_sl = adx_trend_slope(df)
    if adx_sl >= 3:
        score += 2
        breakdown.append(f"Trend strengthening: ADX rising {adx_sl:+.1f} over 10 bars")
    elif adx_sl <= -5:
        breakdown.append(f"Trend cooling: ADX falling {adx_sl:+.1f} over 10 bars")

    stretch = ema200_stretch_pct(df)
    if stretch > 25:
        score -= 4
        breakdown.append(f"Stretched {stretch:.0f}% above EMA200 — mean-reversion risk elevated")

    # ---------- institutional chart layer ----------
    ichi = ichimoku(df)
    if ichi.get("available"):
        if ichi["verdict"] == "bullish":
            score += 6
            breakdown.append(f"Ichimoku: price above a {'bullish' if ichi['cloud_bullish'] else 'thin'} "
                             f"cloud with Tenkan over Kijun — full trend system agrees")
        elif ichi["verdict"] == "bearish":
            score -= 6
            breakdown.append("Ichimoku: price below the cloud with Tenkan under Kijun — trend system bearish")
        elif ichi["position"] == "in_cloud":
            breakdown.append("Ichimoku: price inside the cloud — trendless/indecisive zone, edge is poor here")
        if ichi.get("tk_cross") == "bullish":
            score += 3
            breakdown.append("Fresh bullish Tenkan/Kijun cross (momentum turning up)")
        elif ichi.get("tk_cross") == "bearish":
            score -= 3
            breakdown.append("Fresh bearish Tenkan/Kijun cross (momentum turning down)")

    dvg = divergence_confluence(df)
    if dvg.get("available") and dvg["verdict"] == "bearish":
        score -= 7
        breakdown.append(f"Multi-oscillator bearish divergence on {', '.join(dvg['oscillators'])} "
                         f"— price highs are not backed by momentum or volume flow")
    elif dvg.get("available") and dvg["verdict"] == "bullish":
        score += 6
        breakdown.append(f"Multi-oscillator bullish divergence on {', '.join(dvg['oscillators'])} "
                         f"— selling is exhausting into the lows")

    vp = volume_profile(df)
    if vp.get("available"):
        if vp["zone"] == "above_value":
            score += 5
            breakdown.append(f"Trading ABOVE the value area (POC {vp['poc']:,.2f}) — buyers control "
                             f"the auction; heaviest traded volume now sits below as support")
        elif vp["zone"] == "below_value":
            score -= 5
            breakdown.append(f"Trading BELOW the value area (POC {vp['poc']:,.2f}) — sellers control; "
                             f"the volume shelf overhead is resistance")
        else:
            breakdown.append(f"Inside the value area ({vp['val']:,.2f}–{vp['vah']:,.2f}) — balanced/rotational")

    av = anchored_vwap(df)
    if av.get("available"):
        if av["above"]:
            score += 5
            breakdown.append(f"Above anchored VWAP from the 52-week low ({av['value']:,.2f}) — "
                             f"the average buyer since the bottom is in profit")
        else:
            score -= 5
            breakdown.append(f"Below anchored VWAP from the 52-week low ({av['value']:,.2f}) — "
                             f"buyers since the bottom are underwater, supply overhead")

    ms = market_structure(df)
    if ms.get("available"):
        if ms["bias"] == "bullish":
            score += 6
            breakdown.append(f"Market structure bullish: {ms['label']} (swing low {ms['last_swing_low']:,.2f})")
        elif ms["bias"] == "bearish":
            score -= 6
            breakdown.append(f"Market structure bearish: {ms['label']}")
        if ms.get("bos") == "bullish":
            score += 4
            breakdown.append(f"Break of structure UP — closed above the last swing high {ms['last_swing_high']:,.2f}")
        elif ms.get("bos") == "bearish":
            score -= 4
            breakdown.append(f"Break of structure DOWN — closed below the last swing low {ms['last_swing_low']:,.2f}")

    tq = trend_quality(df)
    if tq.get("available"):
        if tq["direction"] == "up" and tq["r2"] >= 0.6 and tq["efficiency"] >= 0.3:
            score += 5
            breakdown.append(f"High-quality trend: R² {tq['r2']}, efficiency {tq['efficiency']} — "
                             f"steady advance, not chop (clean trends persist)")
        elif tq["r2"] < 0.25 or tq["efficiency"] < 0.12:
            score -= 3
            breakdown.append(f"Choppy, low-quality price path (R² {tq['r2']}, efficiency "
                             f"{tq['efficiency']}) — signals are less reliable here")

    fib = fib_position(df)
    if fib.get("available") and fib["leg"] == "up" and 0.5 <= fib["retracement"] <= 0.618:
        score += 3
        breakdown.append(f"Pullback into the golden pocket ({fib['retracement']:.2f} retracement of "
                         f"{fib['swing_low']:,.2f}–{fib['swing_high']:,.2f}) — classic entry zone")

    mo = monthly_context(df)
    if mo.get("available"):
        if mo["trend"] == "up":
            score += 4
            breakdown.append("Monthly (primary) trend is up — all three timeframes can align")
        elif mo["trend"] == "down":
            score -= 4
            breakdown.append("Monthly (primary) trend is down — counter-trend risk on any long")

    score = round(min(100.0, max(0.0, score)), 1)

    atr_last = float(aux["atr"].iloc[-1])
    upper, mid, lower = bollinger(close)
    macd_line, macd_sig, macd_hist_s = macd(close)
    avg_turnover = float((df["close"] * df["volume"]).tail(20).mean())

    return {
        "last_close": round(last, 2),
        "score": score,
        "breakdown": breakdown,
        "trend": detect_trend(df),
        "breakout": brk,
        "patterns": patterns,
        "atr": round(atr_last, 2),
        "atr_pct": round(100 * atr_last / last, 2),
        "avg_turnover": round(avg_turnover, 0),
        "indicators": {
            "sma20": round(float(sma(close, 20).iloc[-1]), 2),
            "sma50": round(float(sma(close, 50).iloc[-1]), 2),
            "ema20": round(float(e20), 2),
            "ema50": round(float(e50), 2),
            "ema200": round(float(e200), 2),
            "vwap": round(vwap_v, 2),
            "rsi14": round(float(aux["rsi"].iloc[-1]), 1),
            "adx14": round(float(aux["adx"].iloc[-1]), 1),
            "macd": round(float(macd_line.iloc[-1]), 3),
            "macd_signal": round(float(macd_sig.iloc[-1]), 3),
            "macd_hist": round(float(macd_hist_s.iloc[-1]), 3),
            "atr14": round(atr_last, 2),
            "supertrend_dir": int(aux["st_dir"].iloc[-1]),
            "weekly": wk,
            "rs_3m_vs_bench": rs,
            "obv_slope": obv_s,
            "updown_vol_ratio": udr,
            "roc60_pct": roc60,
            "bb_bandwidth_pct": bwp,
            "pos_52w_pct": p52,
            "ichimoku": ichi,
            "divergence_confluence": dvg,
            "volume_profile": vp,
            "anchored_vwap": av,
            "structure": ms,
            "trend_quality": tq,
            "fib": fib,
            "monthly": mo,
            "rsi_divergence": div,
            "adx_slope_10": adx_sl,
            "ema200_stretch_pct": stretch,
            "bollinger": {"upper": round(float(upper.iloc[-1]), 2),
                          "mid": round(float(mid.iloc[-1]), 2),
                          "lower": round(float(lower.iloc[-1]), 2)},
        },
        "levels": {
            "support_resistance": sr,
            "fibonacci": fib,
            "volume_profile": vp,
        },
    }


# ------------------------- advanced analysis add-ons ------------------------- #

def roc(series: pd.Series, n: int) -> float:
    """Rate of change over n bars, in % (momentum)."""
    if len(series) <= n:
        return 0.0
    prev = float(series.iloc[-n - 1])
    return 0.0 if prev == 0 else round((float(series.iloc[-1]) / prev - 1) * 100, 2)


def obv_slope(df: pd.DataFrame, n: int = 20) -> float:
    """Sign & strength of On-Balance-Volume trend over the last n bars.
    Positive = volume flowing in on up days (accumulation)."""
    direction = np.sign(df["close"].diff()).fillna(0)
    obv = (direction * df["volume"]).cumsum()
    tail = obv.tail(n)
    if len(tail) < n or tail.std() == 0:
        return 0.0
    x = np.arange(len(tail))
    slope = np.polyfit(x, (tail - tail.mean()) / (tail.std() + 1e-12), 1)[0]
    return round(float(slope), 3)


def updown_volume_ratio(df: pd.DataFrame, n: int = 20) -> float:
    """Volume on up days ÷ volume on down days over n bars (>1 = buyers dominate)."""
    tail = df.tail(n)
    up = tail.loc[tail["close"] >= tail["open"], "volume"].sum()
    dn = tail.loc[tail["close"] < tail["open"], "volume"].sum()
    return round(float(up / dn), 2) if dn > 0 else 9.99


def bb_bandwidth_pct(close: pd.Series, window: int = 20, lookback: int = 120) -> float:
    """Bollinger bandwidth percentile vs its own history (0 = tightest squeeze)."""
    upper, mid, lower = bollinger(close, window)
    bw = ((upper - lower) / mid).dropna()
    if len(bw) < 30:
        return 50.0
    hist = bw.tail(lookback)
    return round(float((hist < bw.iloc[-1]).mean() * 100), 1)


def pos_52w_pct(df: pd.DataFrame) -> float:
    """Where price sits in its 52-week range (0 = at low, 100 = at high)."""
    tail = df.tail(252)
    lo, hi = float(tail["low"].min()), float(tail["high"].max())
    last = float(df["close"].iloc[-1])
    return round(100 * (last - lo) / (hi - lo), 1) if hi > lo else 50.0


def weekly_context(df: pd.DataFrame) -> dict:
    """Higher-timeframe view: resample daily→weekly and read the big trend."""
    try:
        wk = df.resample("W-FRI").agg({"open": "first", "high": "max",
                                       "low": "min", "close": "last",
                                       "volume": "sum"}).dropna()
        if len(wk) < 30:
            return {"available": False}
        e10 = ema(wk["close"], 10).iloc[-1]
        e30 = ema(wk["close"], 30).iloc[-1]
        _, st_dir_w = supertrend(wk)
        bull = bool(wk["close"].iloc[-1] > e30 and e10 > e30)
        return {"available": True,
                "trend": "up" if bull else ("down" if wk["close"].iloc[-1] < e30 and e10 < e30 else "mixed"),
                "supertrend_dir": int(st_dir_w.iloc[-1]),
                "close_vs_ema30": round(float(wk["close"].iloc[-1] / e30 - 1) * 100, 1)}
    except Exception:
        return {"available": False}


def relative_strength(df: pd.DataFrame, bench: pd.DataFrame | None, n: int = 63) -> float | None:
    """3-month return minus the benchmark's (positive = outperforming the market)."""
    if bench is None or len(df) <= n or len(bench) <= n:
        return None
    r_own = float(df["close"].iloc[-1] / df["close"].iloc[-n - 1] - 1)
    r_b = float(bench["close"].iloc[-1] / bench["close"].iloc[-n - 1] - 1)
    return round((r_own - r_b) * 100, 1)


def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 70) -> dict:
    """Classic momentum divergence: price makes a new extreme but RSI doesn't.
    Bearish = higher price high on weaker RSI; bullish = lower low on stronger RSI."""
    if len(df) < lookback + 15:
        return {"type": None, "detail": ""}
    win = df.tail(lookback)
    r = rsi(df["close"]).tail(lookback)
    highs, lows = win["high"].to_numpy(), win["low"].to_numpy()
    rv = r.to_numpy()

    def pivots(arr, mode):
        idx = []
        for i in range(3, len(arr) - 3):
            seg = arr[i - 3:i + 4]
            if (mode == "hi" and arr[i] == seg.max()) or (mode == "lo" and arr[i] == seg.min()):
                idx.append(i)
        return idx[-2:] if len(idx) >= 2 else []

    hp = pivots(highs, "hi")
    if len(hp) == 2 and highs[hp[1]] > highs[hp[0]] and rv[hp[1]] < rv[hp[0]] - 2:
        return {"type": "bearish",
                "detail": f"price higher high but RSI weaker ({rv[hp[0]]:.0f}→{rv[hp[1]]:.0f})"}
    lp = pivots(lows, "lo")
    if len(lp) == 2 and lows[lp[1]] < lows[lp[0]] and rv[lp[1]] > rv[lp[0]] + 2:
        return {"type": "bullish",
                "detail": f"price lower low but RSI stronger ({rv[lp[0]]:.0f}→{rv[lp[1]]:.0f})"}
    return {"type": None, "detail": ""}


def adx_trend_slope(df: pd.DataFrame, n: int = 10) -> float:
    """ADX change over the last n bars: positive = the trend is strengthening."""
    adx_v, _, _ = adx(df)
    if len(adx_v.dropna()) < n + 5:
        return 0.0
    return round(float(adx_v.iloc[-1] - adx_v.iloc[-1 - n]), 1)


def ema200_stretch_pct(df: pd.DataFrame) -> float:
    """How far price sits above/below EMA200 in % (mean-reversion stretch)."""
    e = ema(df["close"], 200).iloc[-1]
    return round(float(df["close"].iloc[-1] / e - 1) * 100, 1) if e else 0.0


# ==================== Institutional chart layer (v3) ==================== #

def volume_profile(df: pd.DataFrame, bins: int = 24, lookback: int = 120) -> dict:
    """Volume-at-price: where trading actually happened.
    POC = price with the most traded volume (the market's fair-value magnet);
    Value Area = the band holding ~70% of volume. Trading above the value area
    with the POC as support is a structurally strong location."""
    tail = df.tail(lookback)
    lo, hi = float(tail["low"].min()), float(tail["high"].max())
    if hi <= lo or len(tail) < 20:
        return {"available": False}
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    vol = np.zeros(bins)
    for h, l, v in zip(tail["high"].values, tail["low"].values, tail["volume"].values):
        i_lo = max(0, min(bins - 1, int(np.searchsorted(edges, l, "right") - 1)))
        i_hi = max(0, min(bins - 1, int(np.searchsorted(edges, h, "left") - 1)))
        if i_hi < i_lo:
            i_hi = i_lo
        vol[i_lo:i_hi + 1] += float(v) / (i_hi - i_lo + 1)
    total = vol.sum()
    if total <= 0:
        return {"available": False}
    poc_i = int(vol.argmax())
    acc, i_lo, i_hi = vol[poc_i], poc_i, poc_i
    while acc < 0.7 * total and (i_lo > 0 or i_hi < bins - 1):
        left = vol[i_lo - 1] if i_lo > 0 else -1.0
        right = vol[i_hi + 1] if i_hi < bins - 1 else -1.0
        if right >= left:
            i_hi += 1
            acc += vol[i_hi]
        else:
            i_lo -= 1
            acc += vol[i_lo]
    last = float(df["close"].iloc[-1])
    poc = float(centers[poc_i])
    val, vah = float(edges[i_lo]), float(edges[i_hi + 1])
    zone = "above_value" if last > vah else "below_value" if last < val else "inside_value"
    return {"available": True, "poc": round(poc, 2), "vah": round(vah, 2),
            "val": round(val, 2), "zone": zone,
            "dist_from_poc_pct": round((last / poc - 1) * 100, 2)}


def anchored_vwap(df: pd.DataFrame, lookback: int = 252) -> dict:
    """VWAP anchored at the lowest low of the last year — the average price every
    buyer since the bottom has paid. Holding above it = buyers in profit and in control."""
    tail = df.tail(lookback)
    if len(tail) < 30:
        return {"available": False}
    anchor_idx = tail["low"].idxmin()
    seg = df.loc[anchor_idx:]
    if len(seg) < 5:
        return {"available": False}
    tp = (seg["high"] + seg["low"] + seg["close"]) / 3
    v = seg["volume"].astype(float).replace(0, np.nan).fillna(1.0)
    denom = float(v.cumsum().iloc[-1])
    if denom <= 0:
        return {"available": False}
    avwap = float((tp * v).cumsum().iloc[-1] / denom)
    last = float(df["close"].iloc[-1])
    return {"available": True, "value": round(avwap, 2),
            "anchor_date": str(anchor_idx)[:10],
            "above": bool(last > avwap),
            "dist_pct": round((last / avwap - 1) * 100, 2)}


def swing_points(df: pd.DataFrame, left: int = 3, right: int = 3,
                 lookback: int = 200) -> tuple[list, list]:
    """Fractal swing highs/lows: a pivot with `left` lower bars before and
    `right` lower bars after (the objective definition of a swing)."""
    tail = df.tail(lookback)
    highs, lows = [], []
    h, l = tail["high"].values, tail["low"].values
    idx = list(tail.index)
    for i in range(left, len(tail) - right):
        win_h = h[i - left:i + right + 1]
        win_l = l[i - left:i + right + 1]
        if h[i] == win_h.max() and (win_h.argmax() == left):
            highs.append((idx[i], float(h[i])))
        if l[i] == win_l.min() and (win_l.argmin() == left):
            lows.append((idx[i], float(l[i])))
    return highs, lows


def market_structure(df: pd.DataFrame) -> dict:
    """Price structure the way a discretionary trader reads it: higher highs +
    higher lows = uptrend structure; a close above the last swing high is a
    Break Of Structure (BOS), the earliest objective trend-continuation signal."""
    highs, lows = swing_points(df)
    if len(highs) < 2 or len(lows) < 2:
        return {"available": False}
    h1, h2 = highs[-2][1], highs[-1][1]
    l1, l2 = lows[-2][1], lows[-1][1]
    hh, hl = h2 > h1, l2 > l1
    lh, ll = h2 < h1, l2 < l1
    if hh and hl:
        label = "higher highs & higher lows"
        bias = "bullish"
    elif lh and ll:
        label = "lower highs & lower lows"
        bias = "bearish"
    else:
        label = "mixed / range"
        bias = "neutral"
    last = float(df["close"].iloc[-1])
    bos = None
    if last > h2:
        bos = "bullish"          # broke above the most recent swing high
    elif last < l2:
        bos = "bearish"
    return {"available": True, "bias": bias, "label": label, "bos": bos,
            "last_swing_high": round(h2, 2), "last_swing_low": round(l2, 2)}


def trend_quality(df: pd.DataFrame, n: int = 90) -> dict:
    """How CLEAN the trend is, not just its direction.
    r2   = fit of log-price to a straight line (1.0 = a perfectly steady trend)
    eff  = Kaufman efficiency ratio: net move ÷ total path travelled
           (1.0 = straight line, ~0 = choppy noise).
    Clean trends continue far more reliably than choppy ones."""
    close = df["close"].tail(n)
    if len(close) < 30:
        return {"available": False}
    y = np.log(close.values.astype(float))
    x = np.arange(len(y), dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid ** 2).sum()) / ss_tot if ss_tot > 0 else 0.0
    path = float(np.abs(np.diff(close.values.astype(float))).sum())
    net = abs(float(close.values[-1] - close.values[0]))
    eff = net / path if path > 0 else 0.0
    return {"available": True, "r2": round(max(0.0, r2), 3),
            "efficiency": round(eff, 3),
            "slope_pct_per_day": round(float(slope) * 100, 3),
            "direction": "up" if slope > 0 else "down"}


def fib_position(df: pd.DataFrame, lookback: int = 180) -> dict:
    """Where price sits inside the last major swing. The 0.5–0.618 retracement
    ('golden pocket') is the classic high-probability pullback-entry zone."""
    tail = df.tail(lookback)
    if len(tail) < 40:
        return {"available": False}
    hi = float(tail["high"].max())
    lo = float(tail["low"].min())
    if hi <= lo:
        return {"available": False}
    hi_at = tail["high"].idxmax()
    lo_at = tail["low"].idxmin()
    last = float(df["close"].iloc[-1])
    up_leg = lo_at < hi_at                     # low came first → up-leg, measure pullback
    retr = (hi - last) / (hi - lo) if up_leg else (last - lo) / (hi - lo)
    retr = max(0.0, min(1.0, retr))
    zone = ("golden pocket (0.5–0.618)" if 0.5 <= retr <= 0.618
            else "shallow (<0.382)" if retr < 0.382
            else "deep (>0.786)" if retr > 0.786 else "mid (0.382–0.5)")
    rng = hi - lo
    # actual price of each retracement level, measured from the swing that made the leg
    levels = ({f"level_{int(r * 1000)}": round(hi - rng * r, 2)
               for r in (0.382, 0.5, 0.618, 0.786)} if up_leg
              else {f"level_{int(r * 1000)}": round(lo + rng * r, 2)
                    for r in (0.382, 0.5, 0.618, 0.786)})
    return {"available": True, "leg": "up" if up_leg else "down",
            "retracement": round(retr, 3), "zone": zone,
            "swing_high": round(hi, 2), "swing_low": round(lo, 2), **levels}


def monthly_context(df: pd.DataFrame) -> dict:
    """The slowest, strongest timeframe — the primary trend."""
    try:
        try:
            mo = df.resample("ME").agg({"open": "first", "high": "max", "low": "min",
                                        "close": "last", "volume": "sum"}).dropna()
        except ValueError:
            mo = df.resample("M").agg({"open": "first", "high": "max", "low": "min",
                                       "close": "last", "volume": "sum"}).dropna()
        if len(mo) < 8:
            return {"available": False}
        e6 = ema(mo["close"], 6).iloc[-1]
        last = float(mo["close"].iloc[-1])
        rising = bool(mo["close"].iloc[-1] > mo["close"].iloc[-2])
        return {"available": True,
                "trend": "up" if last > e6 and rising else "down" if last < e6 and not rising else "mixed",
                "close_vs_ema6_pct": round((last / float(e6) - 1) * 100, 1)}
    except Exception:
        return {"available": False}


def ichimoku(df: pd.DataFrame) -> dict:
    """Ichimoku Kinko Hyo — a complete stand-alone trend system.

    Tenkan (9) / Kijun (26) are momentum midpoints; the Cloud (Senkou A/B, drawn
    26 bars ahead) is projected support/resistance; Chikou (close shifted back 26)
    confirms that price leads its own past. Price above a bullish cloud, with a
    TK cross up and a clear Chikou, is one of the strongest objective trend reads.
    """
    if len(df) < 78:
        return {"available": False}
    high, low, close = df["high"], df["low"], df["close"]
    conv = (high.rolling(9).max() + low.rolling(9).min()) / 2
    base = (high.rolling(26).max() + low.rolling(26).min()) / 2
    span_a = ((conv + base) / 2).shift(26)
    span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    last = float(close.iloc[-1])
    a, b = float(span_a.iloc[-1]), float(span_b.iloc[-1])
    if any(pd.isna(x) for x in (a, b)):
        return {"available": False}
    top, bottom = max(a, b), min(a, b)
    position = "above_cloud" if last > top else "below_cloud" if last < bottom else "in_cloud"
    tk_cross = None
    c_now, b_now = float(conv.iloc[-1]), float(base.iloc[-1])
    c_prev, b_prev = float(conv.iloc[-2]), float(base.iloc[-2])
    if c_prev <= b_prev and c_now > b_now:
        tk_cross = "bullish"
    elif c_prev >= b_prev and c_now < b_now:
        tk_cross = "bearish"
    chikou_clear = None
    if len(close) > 27:
        chikou_clear = bool(last > float(close.iloc[-27]))
    bull = position == "above_cloud" and c_now > b_now and (chikou_clear is not False)
    bear = position == "below_cloud" and c_now < b_now and (chikou_clear is not True)
    return {"available": True, "position": position,
            "tenkan": round(c_now, 2), "kijun": round(b_now, 2),
            "span_a": round(a, 2), "span_b": round(b, 2),
            "cloud_bullish": bool(a > b), "tk_cross": tk_cross,
            "chikou_clear": chikou_clear,
            "verdict": "bullish" if bull else "bearish" if bear else "neutral"}


def _swing_extremes(series: pd.Series, lookback: int, want: str) -> list[int]:
    """Indices of the two most recent notable extremes in the tail window."""
    tail = series.tail(lookback)
    if len(tail) < 30:
        return []
    half = len(tail) // 2
    first, second = tail.iloc[:half], tail.iloc[half:]
    pick = (lambda s: int(s.idxmax()) if want == "high" else int(s.idxmin()))
    try:
        i1 = series.index.get_loc(first.idxmax() if want == "high" else first.idxmin())
        i2 = series.index.get_loc(second.idxmax() if want == "high" else second.idxmin())
        return [i1, i2]
    except Exception:
        return []


def divergence_confluence(df: pd.DataFrame, lookback: int = 70) -> dict:
    """Divergence measured on THREE independent oscillators (RSI, MACD histogram,
    OBV). One divergence is noise; two or three agreeing is a real warning that
    price is making highs/lows its internal momentum no longer supports."""
    if len(df) < lookback + 10:
        return {"available": False}
    close = df["close"]
    rsi_v = rsi(close, 14)
    _, _, macd_h = macd(close)
    obv = (np.sign(close.diff()).fillna(0) * df["volume"]).cumsum()

    hi_idx = _swing_extremes(close, lookback, "high")
    lo_idx = _swing_extremes(close, lookback, "low")
    bearish, bullish = [], []
    for name, osc in (("RSI", rsi_v), ("MACD", macd_h), ("OBV", obv)):
        if len(hi_idx) == 2:
            p1, p2 = float(close.iloc[hi_idx[0]]), float(close.iloc[hi_idx[1]])
            o1, o2 = float(osc.iloc[hi_idx[0]]), float(osc.iloc[hi_idx[1]])
            if p2 > p1 * 1.005 and o2 < o1:
                bearish.append(name)
        if len(lo_idx) == 2:
            p1, p2 = float(close.iloc[lo_idx[0]]), float(close.iloc[lo_idx[1]])
            o1, o2 = float(osc.iloc[lo_idx[0]]), float(osc.iloc[lo_idx[1]])
            if p2 < p1 * 0.995 and o2 > o1:
                bullish.append(name)
    if len(bearish) >= 2:
        verdict, oscs = "bearish", bearish
    elif len(bullish) >= 2:
        verdict, oscs = "bullish", bullish
    else:
        verdict, oscs = "none", (bearish or bullish)
    return {"available": True, "verdict": verdict, "oscillators": oscs,
            "strength": len(oscs)}
