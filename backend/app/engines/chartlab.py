"""Chart Lab — the deep chart-analysis layer.

Five professional tools, all computed from price/volume only:

1. historical_analogs  — the honest accuracy engine. Finds bars in THIS chart's own
   past whose technical fingerprint resembles today, then measures what price
   actually did next. Produces a base rate ("18 similar setups: higher 10 days
   later 67% of the time, median +2.4%") instead of a claimed accuracy.
2. swing_structure     — higher-highs / higher-lows market structure with
   break-of-structure (BOS) and change-of-character (CHoCH) detection.
3. anchored_vwap       — VWAP anchored at the last major swing low/high, the
   institutional average-cost benchmark.
4. value_area          — volume-profile Point of Control plus the 70% value area,
   and where price sits inside it.
5. mtf_alignment       — daily + weekly + monthly trend agreement score.

Everything degrades gracefully: short histories return {"available": False}
rather than inventing numbers.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.engines.technical import adx, atr, bollinger, ema, macd, rsi

# --------------------------------------------------------------------------- #
# 1. Historical analogs — empirical base rates from this chart's own history
# --------------------------------------------------------------------------- #

HORIZONS = (5, 10, 20)
MIN_BARS_FOR_ANALOGS = 260
K_NEIGHBOURS = 25


def _feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Normalised technical fingerprint per bar — scale-free so it compares
    across price levels and eras."""
    close = df["close"]
    atr_v = atr(df).replace(0, np.nan)
    macd_line, macd_sig, macd_hist = macd(close)
    upper, mid, lower = bollinger(close)
    width = (upper - lower).replace(0, np.nan)
    adx_v, pdi, mdi = adx(df)
    vol_ma = df["volume"].rolling(20).mean().replace(0, np.nan)

    feats = pd.DataFrame({
        "rsi": rsi(close) / 100.0,
        "macd_atr": macd_hist / atr_v,
        "vs_ema20": close / ema(close, 20) - 1.0,
        "vs_ema50": close / ema(close, 50) - 1.0,
        "vs_ema200": close / ema(close, 200) - 1.0,
        "adx": adx_v / 100.0,
        "di_spread": (pdi - mdi) / 100.0,
        "bb_pos": (close - lower) / width,
        "vol_ratio": (df["volume"] / vol_ma).clip(0, 5),
        "roc20": close.pct_change(20),
        "atr_pct": atr_v / close,
    }, index=df.index)
    return feats.replace([np.inf, -np.inf], np.nan)


def historical_analogs(df: pd.DataFrame, k: int = K_NEIGHBOURS) -> dict:
    """Nearest-neighbour search over this symbol's own history.

    Look-ahead safe: candidates must be old enough that their full forward
    window already closed, and the most recent 10 bars are excluded so an
    'analog' is never just yesterday.
    """
    max_h = max(HORIZONS)
    if len(df) < MIN_BARS_FOR_ANALOGS:
        return {"available": False,
                "reason": f"needs {MIN_BARS_FOR_ANALOGS}+ bars of history, has {len(df)}"}

    feats = _feature_matrix(df)
    valid = feats.dropna()
    if len(valid) < 200:
        return {"available": False, "reason": "not enough clean indicator history"}

    today = valid.iloc[-1]
    # z-score using history only (today included is fine — it's one row of many)
    mu, sd = valid.mean(), valid.std().replace(0, np.nan)
    z = ((valid - mu) / sd).dropna(axis=1, how="all").fillna(0.0)
    today_z = z.iloc[-1].to_numpy(dtype=float)

    # candidate bars: old enough for a closed forward window, not the recent tail
    cand_z = z.iloc[:-(max_h + 10)]
    if len(cand_z) < 60:
        return {"available": False, "reason": "history too short after look-ahead guard"}

    dist = np.linalg.norm(cand_z.to_numpy(dtype=float) - today_z, axis=1)
    order = np.argsort(dist)

    close = df["close"]
    pos_of = {ts: i for i, ts in enumerate(df.index)}
    picked, used_positions = [], []
    for idx in order:
        ts = cand_z.index[idx]
        p = pos_of.get(ts)
        if p is None or p + max_h >= len(close):
            continue
        # de-cluster: analogs at least 5 bars apart, else they're the same event
        if any(abs(p - q) < 5 for q in used_positions):
            continue
        used_positions.append(p)
        entry = float(close.iloc[p])
        if entry <= 0:
            continue
        fwd = {f"r{h}": round((float(close.iloc[p + h]) / entry - 1) * 100, 2) for h in HORIZONS}
        window = close.iloc[p + 1: p + max_h + 1]
        fwd["max_up"] = round((float(window.max()) / entry - 1) * 100, 2)
        fwd["max_dn"] = round((float(window.min()) / entry - 1) * 100, 2)
        fwd["date"] = str(ts)[:10]
        fwd["distance"] = round(float(dist[idx]), 3)
        picked.append(fwd)
        if len(picked) >= k:
            break

    if len(picked) < 8:
        return {"available": False, "reason": f"only {len(picked)} comparable setups found"}

    out_h = {}
    for h in HORIZONS:
        rets = np.array([p[f"r{h}"] for p in picked], dtype=float)
        out_h[f"{h}d"] = {
            "win_rate": round(float((rets > 0).mean() * 100), 1),
            "median_pct": round(float(np.median(rets)), 2),
            "avg_pct": round(float(rets.mean()), 2),
            "best_pct": round(float(rets.max()), 2),
            "worst_pct": round(float(rets.min()), 2),
        }
    ups = np.array([p["max_up"] for p in picked], dtype=float)
    dns = np.array([p["max_dn"] for p in picked], dtype=float)
    edge = out_h["10d"]["win_rate"] - 50.0

    return {
        "available": True,
        "samples": len(picked),
        "horizons": out_h,
        "avg_max_favourable_pct": round(float(ups.mean()), 2),
        "avg_max_adverse_pct": round(float(dns.mean()), 2),
        "reward_risk_ratio": (round(float(abs(ups.mean() / dns.mean())), 2)
                              if dns.mean() < 0 else None),
        "edge_vs_coinflip": round(float(edge), 1),
        "closest_dates": [p["date"] for p in picked[:5]],
        "note": ("Base rate measured on this symbol's own history — what actually "
                 "happened after similar setups. Not a prediction."),
    }


# --------------------------------------------------------------------------- #
# 2. Swing market structure (HH/HL/LH/LL, BOS, CHoCH)
# --------------------------------------------------------------------------- #

def _swings(df: pd.DataFrame, order: int = 5) -> tuple[list, list]:
    highs, lows = df["high"], df["low"]
    hi_roll = highs.rolling(2 * order + 1, center=True).max()
    lo_roll = lows.rolling(2 * order + 1, center=True).min()
    sh = [(i, float(highs.iloc[i])) for i in range(len(df))
          if not np.isnan(hi_roll.iloc[i]) and highs.iloc[i] == hi_roll.iloc[i]]
    sl = [(i, float(lows.iloc[i])) for i in range(len(df))
          if not np.isnan(lo_roll.iloc[i]) and lows.iloc[i] == lo_roll.iloc[i]]
    return sh, sl


def swing_structure(df: pd.DataFrame, order: int = 5) -> dict:
    if len(df) < 60:
        return {"available": False}
    sh, sl = _swings(df, order)
    if len(sh) < 2 or len(sl) < 2:
        return {"available": False}

    (h_prev_i, h_prev), (h_last_i, h_last) = sh[-2], sh[-1]
    (l_prev_i, l_prev), (l_last_i, l_last) = sl[-2], sl[-1]
    high_label = "HH" if h_last > h_prev else "LH"
    low_label = "HL" if l_last > l_prev else "LL"

    if high_label == "HH" and low_label == "HL":
        structure, bias = "uptrend", "bullish"
    elif high_label == "LH" and low_label == "LL":
        structure, bias = "downtrend", "bearish"
    else:
        structure, bias = "transition", "neutral"

    last = float(df["close"].iloc[-1])
    bos = None
    if last > h_last and h_last_i < len(df) - 1:
        bos = "bullish_BOS"          # broke the last swing high
    elif last < l_last and l_last_i < len(df) - 1:
        bos = "bearish_BOS"
    choch = None
    if structure == "downtrend" and bos == "bullish_BOS":
        choch = "bullish_CHoCH"      # first crack in a downtrend
    elif structure == "uptrend" and bos == "bearish_BOS":
        choch = "bearish_CHoCH"

    return {"available": True, "structure": structure, "bias": bias,
            "last_swing_high": round(h_last, 2), "last_swing_low": round(l_last, 2),
            "high_label": high_label, "low_label": low_label,
            "bos": bos, "choch": choch,
            "swing_high_ago": len(df) - 1 - h_last_i,
            "swing_low_ago": len(df) - 1 - l_last_i}


# --------------------------------------------------------------------------- #
# 3. Anchored VWAP (from the last major swing low / high)
# --------------------------------------------------------------------------- #

def anchored_vwap(df: pd.DataFrame, lookback: int = 180) -> dict:
    if len(df) < 40 or "volume" not in df:
        return {"available": False}
    win = df.tail(min(lookback, len(df)))
    anchor_pos = int(np.argmin(win["low"].to_numpy()))
    seg = win.iloc[anchor_pos:]
    if len(seg) < 5:
        return {"available": False}
    typical = (seg["high"] + seg["low"] + seg["close"]) / 3.0
    vol = seg["volume"].replace(0, np.nan).fillna(1.0)
    av = float((typical * vol).cumsum().iloc[-1] / vol.cumsum().iloc[-1])
    last = float(df["close"].iloc[-1])
    return {"available": True, "anchor_date": str(seg.index[0])[:10],
            "anchor_low": round(float(seg["low"].iloc[0]), 2),
            "avwap": round(av, 2),
            "price_vs_avwap_pct": round((last / av - 1) * 100, 2),
            "above": bool(last > av),
            "bars_since_anchor": len(seg)}


# --------------------------------------------------------------------------- #
# 4. Volume profile value area
# --------------------------------------------------------------------------- #

def value_area(df: pd.DataFrame, lookback: int = 160, bins: int = 30,
               coverage: float = 0.70) -> dict:
    if len(df) < 40:
        return {"available": False}
    win = df.tail(min(lookback, len(df)))
    hist, edges = np.histogram(win["close"], bins=bins, weights=win["volume"])
    if hist.sum() <= 0:
        return {"available": False}
    centers = (edges[:-1] + edges[1:]) / 2
    poc_i = int(np.argmax(hist))
    target = hist.sum() * coverage
    lo = hi = poc_i
    acc = hist[poc_i]
    while acc < target and (lo > 0 or hi < len(hist) - 1):
        take_low = (hist[lo - 1] if lo > 0 else -1) >= (hist[hi + 1] if hi < len(hist) - 1 else -1)
        if take_low and lo > 0:
            lo -= 1
            acc += hist[lo]
        elif hi < len(hist) - 1:
            hi += 1
            acc += hist[hi]
        else:
            break
    val, vah, poc = float(centers[lo]), float(centers[hi]), float(centers[poc_i])
    last = float(df["close"].iloc[-1])
    where = "above_value" if last > vah else "below_value" if last < val else "inside_value"
    return {"available": True, "poc": round(poc, 2), "val": round(val, 2), "vah": round(vah, 2),
            "price_position": where, "price_vs_poc_pct": round((last / poc - 1) * 100, 2)}


# --------------------------------------------------------------------------- #
# 5. Multi-timeframe alignment (daily + weekly + monthly)
# --------------------------------------------------------------------------- #

def mtf_alignment(df: pd.DataFrame) -> dict:
    out = {"daily": None, "weekly": None, "monthly": None}
    close = df["close"]
    try:
        out["daily"] = "up" if (close.iloc[-1] > ema(close, 50).iloc[-1]
                                and ema(close, 20).iloc[-1] > ema(close, 50).iloc[-1]) else "down"
    except Exception:
        pass
    for key, rule, span_fast, span_slow in (("weekly", "W-FRI", 10, 30), ("monthly", "ME", 6, 12)):
        try:
            r = df.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                       "close": "last", "volume": "sum"}).dropna()
            if len(r) >= span_slow + 5:
                fast, slow = ema(r["close"], span_fast).iloc[-1], ema(r["close"], span_slow).iloc[-1]
                out[key] = "up" if (r["close"].iloc[-1] > slow and fast > slow) else "down"
        except Exception:
            pass
    votes = [v for v in out.values() if v]
    ups = sum(1 for v in votes if v == "up")
    score = round(100 * ups / len(votes), 1) if votes else None
    label = ("all timeframes bullish" if votes and ups == len(votes)
             else "all timeframes bearish" if votes and ups == 0
             else "timeframes disagree")
    return {"available": bool(votes), **out, "timeframes_up": ups,
            "timeframes_counted": len(votes), "alignment_pct": score, "label": label}


# --------------------------------------------------------------------------- #
# Assembler
# --------------------------------------------------------------------------- #

def build_chart_lab(df: pd.DataFrame) -> dict:
    """Run every Chart Lab tool and return findings plus plain-English notes."""
    lab = {
        "analogs": historical_analogs(df),
        "structure": swing_structure(df),
        "avwap": anchored_vwap(df),
        "value_area": value_area(df),
        "mtf": mtf_alignment(df),
    }
    notes: list[str] = []

    a = lab["analogs"]
    if a.get("available"):
        h10 = a["horizons"]["10d"]
        notes.append(
            f"Historical base rate: {a['samples']} similar setups on this chart — 10 days later "
            f"price was higher {h10['win_rate']}% of the time (median {h10['median_pct']:+.2f}%, "
            f"worst {h10['worst_pct']:+.2f}%)")
        if a["reward_risk_ratio"]:
            notes.append(
                f"Typical path after such setups: +{a['avg_max_favourable_pct']:.1f}% best vs "
                f"{a['avg_max_adverse_pct']:.1f}% worst (reward:risk {a['reward_risk_ratio']}:1)")
    else:
        notes.append(f"Historical base rate unavailable — {a.get('reason', 'insufficient history')}")

    s = lab["structure"]
    if s.get("available"):
        line = f"Market structure: {s['structure']} ({s['high_label']} + {s['low_label']})"
        if s.get("choch"):
            line += f" — {s['choch'].replace('_', ' ')}, trend may be flipping"
        elif s.get("bos"):
            line += f" — {s['bos'].replace('_', ' ')} confirmed"
        notes.append(line)

    v = lab["avwap"]
    if v.get("available"):
        notes.append(
            f"Anchored VWAP from the {v['anchor_date']} swing low sits at ₹{v['avwap']:,.2f} — price is "
            f"{abs(v['price_vs_avwap_pct']):.1f}% {'above' if v['above'] else 'below'} the average "
            f"buyer's cost since then")

    va = lab["value_area"]
    if va.get("available"):
        pos = {"above_value": "above the value area (buyers in control, but extended)",
               "below_value": "below the value area (sellers in control)",
               "inside_value": "inside the value area (fair-value balance)"}[va["price_position"]]
        notes.append(f"Volume profile: heaviest trade at ₹{va['poc']:,.2f}; price is {pos}")

    m = lab["mtf"]
    if m.get("available"):
        notes.append(f"Timeframe alignment: {m['timeframes_up']}/{m['timeframes_counted']} bullish "
                     f"(daily {m['daily']}, weekly {m['weekly']}, monthly {m['monthly']}) — {m['label']}")

    lab["notes"] = notes
    return lab
