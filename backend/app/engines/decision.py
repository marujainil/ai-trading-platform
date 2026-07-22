"""AI Decision Engine.

Blend:  technical 40% · fundamentals 25% · news 15% · social 5% · market regime 15%
(fundamental weight is redistributed to technical when coverage is poor).

Output per symbol: action (BUY/SELL/HOLD), confidence, risk score (1-10),
entry / stop-loss / target 1 / target 2 / risk-reward, and human-readable reasoning.

These are research signals, not execution decisions — execution goes through the
Risk Manager and (paper) broker, which can independently veto any signal.
"""
from __future__ import annotations

from app.config import settings
from app.data import market_data
from app.engines import patterns as pat
from app.engines.fundamental import fundamental_score
from app.engines.sentiment import news_sentiment, social_sentiment
from app.engines import technical as tech_mod
from app.engines.technical import full_technical_analysis

WEIGHTS = {"technical": 0.40, "fundamental": 0.25, "news": 0.15, "social": 0.05, "market": 0.15}

BUY_THRESHOLD = 65.0
SELL_THRESHOLD = 40.0
ATR_STOP_MULT = 1.5
ATR_T1_MULT = 2.25   # 1.5R
ATR_T2_MULT = 4.5    # 3.0R


def _risk_score(atr_pct: float, avg_turnover: float, beta: float | None, market_label: str) -> tuple[int, list[str]]:
    notes: list[str] = []
    score = 3.0
    if atr_pct > 5:
        score += 3; notes.append(f"High volatility (ATR {atr_pct:.1f}% of price)")
    elif atr_pct > 3:
        score += 1.5; notes.append(f"Moderate volatility (ATR {atr_pct:.1f}%)")
    else:
        notes.append(f"Low volatility (ATR {atr_pct:.1f}%)")

    if avg_turnover < 5e7:  # < ₹5 crore/day average turnover
        score += 2; notes.append("Thin liquidity — slippage risk")
    if beta is not None and beta > 1.3:
        score += 1; notes.append(f"High beta ({beta:.2f})")
    if market_label in ("downtrend", "strong_downtrend"):
        score += 1; notes.append("Broad market in a downtrend")
    return int(min(10, max(1, round(score)))), notes


def build_trade_plan(entry: float, atr_v: float, sr: dict, direction: str = "long") -> dict:
    """Structure-aware plan. ATR multiples are the skeleton; when real support/
    resistance sits nearby, the stop tucks under support and targets snap to
    resistance — so Risk:Reward reflects the actual chart, not a constant."""
    notes: list[str] = []
    if direction == "short":                       # long-side mirror for SELL framing
        stop = round(entry + ATR_STOP_MULT * atr_v, 2)
        t1 = round(entry - ATR_T1_MULT * atr_v, 2)
        t2 = round(entry - ATR_T2_MULT * atr_v, 2)
        rr = round(abs(t1 - entry) / max(abs(entry - stop), 1e-9), 2)
        return {"stop": stop, "t1": t1, "t2": t2, "rr": rr, "notes": notes}

    stop = entry - ATR_STOP_MULT * atr_v
    t1 = entry + ATR_T1_MULT * atr_v
    t2 = entry + ATR_T2_MULT * atr_v

    supports = [s["level"] for s in (sr or {}).get("support", []) if s.get("level")]
    resistances = sorted(r["level"] for r in (sr or {}).get("resistance", [])
                         if r.get("level") and r["level"] > entry)

    # stop: tuck just below the nearest support if it sits within 2.5×ATR of entry
    near_sup = [s for s in supports if entry - 2.5 * atr_v <= s < entry]
    if near_sup:
        stop = max(near_sup) * 0.995
        notes.append(f"Stop tucked under support at {max(near_sup):,.2f}")

    risk_ps = max(entry - stop, 1e-9)

    # T1: snap to the first resistance that still pays at least ~0.8R
    t1_candidates = [r for r in resistances if entry + 0.8 * risk_ps <= r <= entry + 3.5 * risk_ps]
    if t1_candidates:
        t1 = t1_candidates[0] * 0.998
        notes.append(f"Target 1 set at resistance {t1_candidates[0]:,.2f}")
    # T2: the next resistance beyond T1, else keep the ATR extension
    t2_candidates = [r for r in resistances if r > t1 * 1.01]
    if t2_candidates:
        t2 = max(t2_candidates[0] * 0.998, t1 + 0.8 * risk_ps)
        notes.append(f"Target 2 at next resistance {t2_candidates[0]:,.2f}")
    t2 = max(t2, t1 + 0.5 * risk_ps)

    rr = round((t1 - entry) / risk_ps, 2)
    if not notes:
        notes.append("No clean support/resistance nearby — ATR-based plan (RR 1.5 default)")
    return {"stop": round(stop, 2), "t1": round(t1, 2), "t2": round(t2, 2),
            "rr": rr, "notes": notes}


def analyze_symbol(symbol: str, period: str = "1y", include_news: bool = True) -> dict:
    df = market_data.get_ohlcv(symbol, period=period)   # already ₹ for USD assets

    # rate used by the data layer — reported for transparency & live-stream scaling
    fx_rate = market_data.get_usd_inr() if market_data.is_usd_asset(symbol) else None

    candles = pat.detect_candlestick_patterns(df)
    structure = pat.detect_chart_structure(df)
    # benchmark for relative strength: NIFTY for stocks, Bitcoin for crypto
    bench = None
    try:
        su = symbol.strip().upper()
        bench_sym = "BTC-USD" if ("-" in su and not su.endswith((".NS", ".BO")))             else settings.market_index_symbol
        if bench_sym != su:
            bench = market_data.get_ohlcv(bench_sym, period=period)
    except Exception:
        bench = None
    tech = full_technical_analysis(df, patterns=candles + structure, bench_df=bench)

    info = market_data.get_fundamentals(symbol)
    fund = fundamental_score(info)
    if include_news:
        news = news_sentiment(symbol)
        social = social_sentiment(symbol)
    else:
        news = {"score": 0.0, "label": "neutral", "method": "skipped", "summary": "Skipped for fast scan",
                "headlines": [], "score_0_100": 50.0}
        social = {"score": 0.0, "label": "neutral", "method": "skipped", "score_0_100": 50.0,
                  "summary": "Skipped for fast scan"}
    market = market_data.get_market_regime()

    # Redistribute fundamental weight when data coverage is poor.
    w = dict(WEIGHTS)
    if fund["coverage"] < 0.4:
        shift = w["fundamental"] * (1 - fund["coverage"] / 0.4)
        w["fundamental"] -= shift
        w["technical"] += shift

    composite = (
        w["technical"] * tech["score"]
        + w["fundamental"] * fund["score"]
        + w["news"] * news["score_0_100"]
        + w["social"] * social["score_0_100"]
        + w["market"] * market["score"]
    )
    composite = round(composite, 1)

    trend_ok_for_buy = tech["indicators"]["supertrend_dir"] == 1 or tech["trend"]["label"] in ("uptrend", "strong_uptrend")
    if composite >= BUY_THRESHOLD and trend_ok_for_buy:
        action = "BUY"
    elif composite <= SELL_THRESHOLD:
        action = "SELL"
    else:
        action = "HOLD"

    # --- Confidence: distance from neutral + cross-engine agreement − data gaps ---
    components = [tech["score"], fund["score"], news["score_0_100"]]
    side = 1 if composite >= 50 else -1
    agreement = sum(1 for c in components if (c - 50) * side > 0)
    confidence = min(95.0, abs(composite - 50) * 2 + (agreement - 1) * 5)
    if fund["coverage"] < 0.4:
        confidence -= 10
    if news["method"] in ("no_news", "not_configured"):
        confidence -= 5
    wk = tech["indicators"].get("weekly", {})
    if wk.get("available"):
        if wk.get("trend") == "up" and wk.get("supertrend_dir") == 1:
            confidence += 5           # higher timeframe agrees
        elif wk.get("trend") == "down":
            confidence -= 8           # fighting the weekly trend
    confidence = round(max(5.0, min(95.0, confidence)), 1)

    # --- Trade plan: structure-aware (support/resistance) with ATR skeleton ---
    entry = tech["last_close"]
    atr_v = tech["atr"]
    sr_levels = tech.get("levels", {}).get("support_resistance", {})
    plan = build_trade_plan(entry, atr_v, sr_levels,
                            direction="short" if action == "SELL" else "long")
    stop, t1, t2, rr = plan["stop"], plan["t1"], plan["t2"], plan["rr"]
    plan_notes = plan["notes"]

    risk, risk_notes = _risk_score(tech["atr_pct"], tech["avg_turnover"], fund["ratios"].get("beta"), market["label"])

    reasoning = (
        [f"Composite {composite}/100 → {action} "
         f"(tech {tech['score']}, fundamentals {fund['score']}, news {news['score_0_100']}, "
         f"social {social['score_0_100']}, market {market['score']})"]
        + tech["breakdown"][:12]
        + plan_notes
        + fund["notes"][:4]
        + [f"News: {news['label']} — {news.get('summary', '')} [{news['method']}]"]
        + [f"Market regime ({market['index']}): {market['label']}"]
        + risk_notes
    )
    if action == "HOLD" and composite >= BUY_THRESHOLD and not trend_ok_for_buy:
        reasoning.append("Score qualifies for BUY but trend filter (Supertrend/EMA) is not confirming — held back")

    # --- India context the chart cannot show ---------------------------------
    sector_rs = None
    sector_idx = market_data.sector_index_for(fund.get("sector"))
    if sector_idx:
        try:
            sec_df = market_data.get_ohlcv(sector_idx, period=period)
            sector_rs = tech_mod.relative_strength(df, sec_df)
        except Exception:
            sector_rs = None
    if sector_rs is not None:
        if sector_rs >= 4:
            confidence += 3
            reasoning.append(f"Sector leadership: outperforming its own sector index "
                             f"({sector_idx}) by {sector_rs:+.1f}% over 3 months")
        elif sector_rs <= -4:
            confidence -= 4
            reasoning.append(f"Sector laggard: trailing its own sector ({sector_idx}) by "
                             f"{sector_rs:+.1f}% over 3 months — better names exist in this space")

    delivery = {"available": False}
    if symbol.strip().upper().endswith(".NS"):
        try:
            from app.data import india_flows
            delivery = india_flows.delivery_stats(symbol)
        except Exception:
            delivery = {"available": False}
    if delivery.get("available"):
        if delivery["label"] == "strong_accumulation":
            confidence += 4
            reasoning.append(f"Delivery {delivery['latest_pct']}% vs {delivery['avg_pct']}% average — "
                             f"real buyers taking stock into demat, not intraday churn")
        elif delivery["label"] == "churn":
            confidence -= 5
            reasoning.append(f"Delivery only {delivery['latest_pct']}% — this move is intraday churn, "
                             f"not accumulation; treat the breakout with suspicion")

    earnings_in = market_data.earnings_days_away(fund.get("raw") or {})
    earnings_blackout = earnings_in is not None and 0 <= earnings_in <= 2

    # --- Conviction checklist: independent chart-based confirmations ---------
    ind = tech["indicators"]
    wk_ind = ind.get("weekly", {})
    checks = [
        ("Daily trend up (EMA structure)", "uptrend" in tech["trend"]["label"]),
        ("Supertrend bullish", ind.get("supertrend_dir") == 1),
        ("Weekly timeframe agrees", bool(wk_ind.get("available")) and wk_ind.get("trend") == "up"),
        ("Outperforming the market (3m RS)", (ind.get("rs_3m_vs_bench") or 0) > 0),
        ("Volume shows accumulation (OBV)", (ind.get("obv_slope") or 0) > 0),
        ("Buyers dominate volume (U/D ≥ 1.1)", (ind.get("updown_vol_ratio") or 0) >= 1.1),
        ("Overall market supportive", market["score"] >= 50),
        ("Momentum healthy, not overextended", 0 < (ind.get("roc60_pct") or 0) <= 60),
        ("No bearish momentum divergence", (ind.get("rsi_divergence") or {}).get("type") != "bearish"),
        ("Above the volume value area (POC support)",
         (ind.get("volume_profile") or {}).get("zone") == "above_value"),
        ("Above anchored VWAP from 52w low",
         bool((ind.get("anchored_vwap") or {}).get("above"))),
        ("Structure: higher highs & higher lows",
         (ind.get("structure") or {}).get("bias") == "bullish"),
        ("Monthly (primary) trend up",
         (ind.get("monthly") or {}).get("trend") == "up"),
        ("Ichimoku system bullish (above cloud)",
         (ind.get("ichimoku") or {}).get("verdict") == "bullish"),
        ("No multi-oscillator bearish divergence",
         (ind.get("divergence_confluence") or {}).get("verdict") != "bearish"),
    ]
    conviction_passed = sum(1 for _, ok in checks if ok)
    conviction = {"passed": conviction_passed, "total": len(checks),
                  "checks": [{"name": nm, "ok": ok} for nm, ok in checks]}

    if earnings_blackout and composite >= BUY_THRESHOLD:
        rating = "HOLD"                # results due: outcome is news, not chart
        reasoning.append(f"Earnings in {earnings_in} day(s) — result-day gaps ignore technicals. "
                         f"Rating capped at HOLD until the numbers are out.")
    elif composite >= 78 and trend_ok_for_buy and conviction_passed >= 12:
        rating = "STRONG BUY"          # near-unanimous chart agreement required
    elif action == "BUY":
        rating = "BUY"
    elif composite <= 30:
        rating = "STRONG SELL"
    elif action == "SELL":
        rating = "SELL"
    elif composite >= 58 and trend_ok_for_buy:
        rating = "ACCUMULATE"      # leaning bullish, not yet a full signal
    elif composite <= 45:
        rating = "REDUCE"          # leaning bearish
    else:
        rating = "HOLD"
    # --- live news momentum: is the story driving price right now? ---
    try:
        from app.data import newsfeeds
        nm = newsfeeds.momentum_for(symbol)
        if nm.get("available"):
            news["momentum"] = nm
            if nm.get("surge"):
                reasoning.append(
                    f"📰 News flow SURGING: {nm['count_24h']} headlines in 24h vs a "
                    f"{nm['baseline_per_day']}/day baseline (latest {nm['latest_age_hours']}h ago) — "
                    f"news is driving this move; expect wider swings than the chart alone implies")
            elif nm.get("latest_age_hours") is not None and nm["latest_age_hours"] <= 12:
                reasoning.append(f"📰 Fresh news {nm['latest_age_hours']}h ago "
                                 f"({nm['count_24h']} in 24h) — story is live")
            elif nm.get("count_7d", 0) == 0:
                reasoning.append("📰 Quiet news tape — chart signals dominate here")
    except Exception:
        pass

    reasoning.append(f"Conviction: {conviction_passed}/{len(checks)} independent chart checks passed")

    # --- Edge score: a CONTINUOUS quality measure -----------------------------
    # The composite clamps at 100, so dozens of strong names collapse onto the
    # same number and their ranking becomes arbitrary. This score never saturates:
    # it blends the graded strength of each confirmation, so near-identical
    # composites still rank in a meaningful order.
    import math as _math

    def _sq(x, scale):                      # smooth 0..1, never saturating hard
        return 0.5 * (1.0 + _math.tanh(x / scale))

    wk_e = ind.get("weekly") or {}
    mo_e = ind.get("monthly") or {}
    tq_e = ind.get("trend_quality") or {}
    parts = {
        "conviction": conviction_passed / max(len(checks), 1),
        "trend_quality": min(max(tq_e.get("r2") or 0.0, 0.0), 1.0),
        "rel_strength": _sq(ind.get("rs_3m_vs_bench") or 0.0, 12.0),
        "volume_flow": _sq(ind.get("obv_slope") or 0.0, 0.25),
        "buy_pressure": _sq(((ind.get("updown_vol_ratio") or 1.0) - 1.0), 0.6),
        "htf_agree": (0.5 * (wk_e.get("trend") == "up") + 0.5 * (mo_e.get("trend") == "up")),
        "momentum_health": 1.0 - _sq(abs((ind.get("roc60_pct") or 0.0) - 20.0), 45.0),
        "not_extended": 1.0 - _sq((ind.get("ema200_stretch_pct") or 0.0) - 15.0, 20.0),
    }
    weights = {"conviction": 0.24, "trend_quality": 0.16, "rel_strength": 0.16,
               "volume_flow": 0.12, "buy_pressure": 0.10, "htf_agree": 0.10,
               "momentum_health": 0.06, "not_extended": 0.06}
    edge_score = round(100.0 * sum(parts[k] * w for k, w in weights.items()), 2)
    edge_parts = {k: round(v, 3) for k, v in parts.items()}

    # --- Entry timing: not just WHAT to buy, but WHEN ---
    e20 = ind.get("ema20") or entry
    dist_atr = (entry - e20) / atr_v if atr_v else 0
    if rating in ("STRONG BUY", "BUY", "ACCUMULATE"):
        if dist_atr > 1.2:
            entry_timing = (f"Extended {dist_atr:.1f}×ATR above the 20-EMA — chasing here is poor "
                            f"risk. Patient entry: wait for a pullback toward ₹{e20:,.2f} (EMA20).")
        elif dist_atr < 0:
            entry_timing = (f"Pullback entry: price at/below the 20-EMA (₹{e20:,.2f}) with the trend "
                            f"intact — favorable risk point if it holds.")
        else:
            entry_timing = "Timing OK: price riding the trend near the 20-EMA — standard entry zone."
    elif rating in ("SELL", "STRONG SELL", "REDUCE"):
        entry_timing = "No new buying. If holding, respect the stop — bounces into the falling 20-EMA are exit chances."
    else:
        entry_timing = "No edge right now — wait for the checklist to align before committing money."
    reasoning.append("Timing: " + entry_timing)

    trend_label = tech["trend"]["label"].replace("_", " ")
    conf_word = "high" if confidence >= 66 else "moderate" if confidence >= 40 else "low"
    verb = {"BUY": "Consider buying", "SELL": "Consider selling / avoiding",
            "HOLD": "Hold / wait"}[action]
    summary = (f"{verb} — {trend_label}, news {news['label']}, "
               f"in a {market['label'].replace('_', ' ')} market. "
               f"{conf_word.capitalize()} confidence ({confidence}%), risk {risk}/10.")

    su_cur = symbol.strip().upper()
    currency = "INR" if (su_cur.endswith((".NS", ".BO")) or fx_rate) else "USD"

    return {
        "symbol": symbol,
        "currency": currency,
        "fx_rate": fx_rate,
        "action": action,
        "rating": rating,
        "conviction": conviction,
        "edge_score": edge_score,
        "edge_parts": edge_parts,
        "sector_rs": sector_rs,
        "sector_index": sector_idx,
        "delivery": delivery,
        "earnings_in_days": earnings_in,
        "entry_timing": entry_timing,
        "currency_note": (f"Prices in ₹ — converted from USD at ≈₹{fx_rate:.2f}/$"
                          if fx_rate else None),
        "summary": summary,
        "composite_score": composite,
        "confidence": confidence,
        "risk_score": risk,
        "entry": entry,
        "stop_loss": stop,
        "target_1": t1,
        "target_2": t2,
        "risk_reward": rr,
        "reasoning": reasoning,
        "scores": {
            "technical": tech["score"],
            "fundamental": fund["score"],
            "news": news["score_0_100"],
            "social": social["score_0_100"],
            "market": market["score"],
            "weights_used": {k: round(v, 3) for k, v in w.items()},
        },
        "technical": tech,
        "fundamental": fund,
        "news": {k: news[k] for k in ("label", "score_0_100", "method", "summary", "headlines") if k in news},
        "market_regime": market,
        "disclaimer": "Research signal only — not investment advice. Markets involve risk of loss.",
    }
