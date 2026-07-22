"""Turn an OHLCV DataFrame into chart-ready series for the web dashboard.

Format matches TradingView lightweight-charts:
  line/candle points: {"time": "YYYY-MM-DD", ...values}
  whitespace points:  {"time": "YYYY-MM-DD"}   (draws a gap — used to split
                       the supertrend into green/red segments)
"""
import math

import pandas as pd

from app.engines import technical as ta

UP = "rgba(38,166,91,0.45)"
DOWN = "rgba(220,68,68,0.45)"


def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _times(df: pd.DataFrame, intraday: bool) -> list:
    if not intraday:
        return [d.strftime("%Y-%m-%d") for d in df.index]
    # Show everything in IST. lightweight-charts renders epoch seconds as UTC, so we
    # convert each timestamp to Asia/Kolkata and add the IST offset — that way a
    # crypto candle at 03:00 UTC displays as 08:30 (IST), matching the user's clock,
    # and NSE candles keep showing 09:15–15:30 IST.
    idx = df.index
    try:
        idx = idx.tz_localize("UTC") if idx.tz is None else idx
        idx = idx.tz_convert("Asia/Kolkata")
    except Exception:
        pass
    out = []
    for d in idx:
        off = d.utcoffset().total_seconds() if getattr(d, "tzinfo", None) else 19800
        out.append(int(d.timestamp() + off))
    return out


def build_chart_payload(df: pd.DataFrame, intraday: bool = False) -> dict:
    times = _times(df, intraday)
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    v = df["volume"].to_numpy()

    candles = [{"time": t, "open": round(float(o[i]), 2), "high": round(float(h[i]), 2),
                "low": round(float(l[i]), 2), "close": round(float(c[i]), 2)}
               for i, t in enumerate(times)]
    volume = [{"time": t, "value": float(v[i]), "color": UP if c[i] >= o[i] else DOWN}
              for i, t in enumerate(times)]

    def line(series: pd.Series, nd: int = 2) -> list[dict]:
        vals = series.to_numpy()
        return [{"time": t, "value": round(float(x), nd)}
                for t, x in zip(times, vals) if _ok(x)]

    close = df["close"]
    st_line, st_dir = ta.supertrend(df)
    stv, std = st_line.to_numpy(), st_dir.to_numpy()
    st_bull = [{"time": t, "value": round(float(x), 2)} if (_ok(x) and d == 1) else {"time": t}
               for t, x, d in zip(times, stv, std)]
    st_bear = [{"time": t, "value": round(float(x), 2)} if (_ok(x) and d == -1) else {"time": t}
               for t, x, d in zip(times, stv, std)]

    vwap = []
    if intraday:                                   # session-anchored VWAP (resets each day)
        tp = (df["high"] + df["low"] + df["close"]) / 3
        pv = (tp * df["volume"]).groupby(df.index.date).cumsum()
        vv = df["volume"].groupby(df.index.date).cumsum().replace(0, float("nan"))
        vwap = [{"time": t, "value": round(float(x), 2)}
                for t, x in zip(times, (pv / vv).to_numpy()) if _ok(x)]

    return {
        "bars": len(candles),
        "intraday": intraday,
        "vwap": vwap,
        "candles": candles,
        "volume": volume,
        "ema20": line(ta.ema(close, 20)),
        "ema50": line(ta.ema(close, 50)),
        "ema200": line(ta.ema(close, 200)),
        "rsi": line(ta.rsi(close), 1),
        "supertrend_bull": st_bull,
        "supertrend_bear": st_bear,
    }
