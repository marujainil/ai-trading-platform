"""Candlestick + chart-structure pattern detection (recent bars).

Each detection returns: {"name", "bias" (bullish/bearish/neutral), "bars_ago"}.
Deliberately conservative — patterns nudge the technical score, they don't drive it.
"""
from __future__ import annotations

import pandas as pd


def _bar(df: pd.DataFrame, i: int) -> dict:
    row = df.iloc[i]
    o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
    body = abs(c - o)
    rng = max(h - l, 1e-9)
    return {
        "o": o, "h": h, "l": l, "c": c,
        "body": body, "range": rng,
        "upper_wick": h - max(o, c),
        "lower_wick": min(o, c) - l,
        "bull": c > o, "bear": c < o,
    }


def detect_candlestick_patterns(df: pd.DataFrame, lookback: int = 5) -> list[dict]:
    out: list[dict] = []
    n = len(df)
    start = max(2, n - lookback)

    for i in range(start, n):
        b = _bar(df, i)
        p = _bar(df, i - 1)
        bars_ago = n - 1 - i

        if b["body"] <= 0.1 * b["range"]:
            out.append({"name": "doji", "bias": "neutral", "bars_ago": bars_ago})

        if b["lower_wick"] >= 2 * b["body"] and b["upper_wick"] <= 0.6 * b["body"] + 1e-9 and b["body"] > 0:
            out.append({"name": "hammer", "bias": "bullish", "bars_ago": bars_ago})

        if b["upper_wick"] >= 2 * b["body"] and b["lower_wick"] <= 0.6 * b["body"] + 1e-9 and b["body"] > 0:
            out.append({"name": "shooting_star", "bias": "bearish", "bars_ago": bars_ago})

        if b["bull"] and p["bear"] and b["c"] >= p["o"] and b["o"] <= p["c"] and b["body"] > p["body"]:
            out.append({"name": "bullish_engulfing", "bias": "bullish", "bars_ago": bars_ago})

        if b["bear"] and p["bull"] and b["o"] >= p["c"] and b["c"] <= p["o"] and b["body"] > p["body"]:
            out.append({"name": "bearish_engulfing", "bias": "bearish", "bars_ago": bars_ago})

        if i >= 2:
            pp = _bar(df, i - 2)
            small_mid = p["body"] <= 0.4 * pp["body"] + 1e-9
            if pp["bear"] and small_mid and b["bull"] and b["c"] > (pp["o"] + pp["c"]) / 2:
                out.append({"name": "morning_star", "bias": "bullish", "bars_ago": bars_ago})
            if pp["bull"] and small_mid and b["bear"] and b["c"] < (pp["o"] + pp["c"]) / 2:
                out.append({"name": "evening_star", "bias": "bearish", "bars_ago": bars_ago})

    # Most recent first; cap noise.
    out.sort(key=lambda x: x["bars_ago"])
    return out[:5]


def detect_chart_structure(df: pd.DataFrame, order: int = 7) -> list[dict]:
    """Higher-high/higher-low (and inverse) structure from pivot points."""
    from app.engines.technical import _pivot_points  # local import avoids cycle at module load

    out: list[dict] = []
    highs = _pivot_points(df["high"].tail(180), order, "high").tail(3).tolist()
    lows = _pivot_points(df["low"].tail(180), order, "low").tail(3).tolist()

    if len(highs) == 3 and len(lows) == 3:
        if highs[0] < highs[1] < highs[2] and lows[0] < lows[1] < lows[2]:
            out.append({"name": "higher_highs_higher_lows", "bias": "bullish", "bars_ago": 0})
        elif highs[0] > highs[1] > highs[2] and lows[0] > lows[1] > lows[2]:
            out.append({"name": "lower_highs_lower_lows", "bias": "bearish", "bars_ago": 0})
    return out
