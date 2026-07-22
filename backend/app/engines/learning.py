"""Learning Engine.

Every completed trade is stored with the *signal snapshot at entry* (scores,
trend, RSI, ADX, confidence, market regime). This module slices that history to
answer: which entry conditions actually made money, which lost, and what to try
changing next.

It does NOT claim the system "learns perfectly" — it produces evidence with
sample sizes attached so a human (or a tuning script) can adjust thresholds.
"""
from __future__ import annotations

from collections import defaultdict

MIN_TRADES_FOR_INSIGHT = 5


def _bucket_defs(snapshot: dict) -> dict[str, str]:
    """Map an entry snapshot to categorical buckets."""
    s = snapshot or {}
    rsi = s.get("rsi14")
    adx_v = s.get("adx14")
    conf = s.get("confidence")
    return {
        "trend_at_entry": s.get("trend_label", "unknown"),
        "market_regime": s.get("market_label", "unknown"),
        "rsi_zone": ("unknown" if rsi is None else "rsi<40" if rsi < 40 else "rsi 40-60" if rsi <= 60 else "rsi>60"),
        "adx_regime": ("unknown" if adx_v is None else "trending (ADX>25)" if adx_v > 25 else "choppy (ADX<=25)"),
        "confidence_band": ("unknown" if conf is None else "high (>=70)" if conf >= 70 else "medium (50-70)" if conf >= 50 else "low (<50)"),
        "supertrend": {1: "bullish", -1: "bearish"}.get(s.get("supertrend_dir"), "unknown"),
    }


def analyze_trades(closed_trades: list[dict]) -> dict:
    """closed_trades: [{pnl, pnl_pct, entry_snapshot, holding_days, symbol}, ...]"""
    n = len(closed_trades)
    if n == 0:
        return {"summary": {"total_trades": 0, "win_rate": 0.0, "total_pnl": 0.0,
                            "note": "No completed trades yet — trade in paper mode to build the dataset."},
                "total_trades": 0, "buckets": {}, "suggestions": [], "caveats": []}

    wins = [t for t in closed_trades if t["pnl"] > 0]
    total_pnl = sum(t["pnl"] for t in closed_trades)
    overall = {
        "total_trades": n,
        "win_rate": round(100 * len(wins) / n, 1),
        "total_pnl": round(total_pnl, 2),
        "avg_pnl_pct": round(sum(t["pnl_pct"] for t in closed_trades) / n, 2),
        "avg_holding_days": round(sum(t.get("holding_days", 0) for t in closed_trades) / n, 1),
    }

    # Bucketed performance
    stats: dict[str, dict[str, dict]] = defaultdict(dict)
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in closed_trades:
        for dim, val in _bucket_defs(t.get("entry_snapshot") or {}).items():
            grouped[(dim, val)].append(t)

    for (dim, val), ts in grouped.items():
        w = [t for t in ts if t["pnl"] > 0]
        stats[dim][val] = {
            "trades": len(ts),
            "win_rate": round(100 * len(w) / len(ts), 1),
            "avg_pnl_pct": round(sum(t["pnl_pct"] for t in ts) / len(ts), 2),
            "total_pnl": round(sum(t["pnl"] for t in ts), 2),
        }

    # Turn the biggest contrasts into suggestions
    suggestions: list[str] = []
    for dim, values in stats.items():
        sized = {v: s for v, s in values.items() if s["trades"] >= MIN_TRADES_FOR_INSIGHT and v != "unknown"}
        if len(sized) < 2:
            continue
        best = max(sized.items(), key=lambda kv: kv[1]["avg_pnl_pct"])
        worst = min(sized.items(), key=lambda kv: kv[1]["avg_pnl_pct"])
        gap = best[1]["avg_pnl_pct"] - worst[1]["avg_pnl_pct"]
        if gap >= 1.0:
            suggestions.append(
                f"{dim}: '{best[0]}' averaged {best[1]['avg_pnl_pct']:+.2f}%/trade over {best[1]['trades']} trades "
                f"vs '{worst[0]}' at {worst[1]['avg_pnl_pct']:+.2f}% ({worst[1]['trades']} trades). "
                f"Consider filtering out '{worst[0]}' entries or down-weighting them."
            )

    if n < 20:
        suggestions.append(f"Only {n} completed trades — treat every pattern above as tentative until 20+ samples.")
    if not suggestions:
        suggestions.append("No bucket shows a decisive edge yet; keep collecting trades before changing thresholds.")

    return {"summary": overall, "buckets": stats, "suggestions": suggestions,
            "caveat": "Correlation over a small sample is weak evidence. Validate any rule change in backtests first."}


# ==================== Signal grading & calibration (learning loop) ==================== #

BUY_RATINGS = {"STRONG BUY", "BUY", "ACCUMULATE"}
SELL_RATINGS = {"STRONG SELL", "SELL", "REDUCE"}


def _is_win(rating: str, outcome: str, ret_pct: float) -> bool | None:
    """Grade: for buy-side calls a win = target hit or clearly up; sell-side inverted.
    Returns None for pushes (small moves that prove nothing)."""
    if rating in BUY_RATINGS:
        if outcome == "target_hit" or (outcome == "expired" and ret_pct >= 1.0):
            return True
        if outcome == "stop_hit" or (outcome == "expired" and ret_pct <= -1.0):
            return False
    elif rating in SELL_RATINGS:
        if outcome == "target_hit" or (outcome == "expired" and ret_pct <= -1.0):
            return True
        if outcome == "stop_hit" or (outcome == "expired" and ret_pct >= 1.0):
            return False
    return None


def evaluate_signals(db, min_age_days: int = 5, horizon_days: int = 10,
                     max_symbols: int = 40) -> dict:
    """Walk ungraded signals old enough to judge; fetch what price actually did;
    write a SignalOutcome for each. This is how the engine learns from being wrong."""
    from datetime import datetime, timedelta, timezone

    from app import models
    from app.data import market_data

    cutoff = datetime.now(timezone.utc) - timedelta(days=min_age_days)
    graded_ids = {sid for (sid,) in db.query(models.SignalOutcome.signal_id).all()}
    rows = (db.query(models.Signal)
              .filter(models.Signal.created_at < cutoff)
              .order_by(models.Signal.created_at.desc()).limit(400).all())
    pending: dict[str, list] = {}
    for s in rows:
        rating = ((s.payload or {}).get("rating")) or s.action
        if s.id in graded_ids or rating == "HOLD" or not (s.entry and s.stop_loss and s.target_1):
            continue
        pending.setdefault(s.symbol, []).append((s, rating))

    evaluated = wins = losses = 0
    for symbol, sigs in list(pending.items())[:max_symbols]:
        try:
            df = market_data.get_ohlcv(symbol, period="6mo", min_bars=10)
        except Exception:
            continue
        idx = df.index
        for s, rating in sigs:
            created = s.created_at if s.created_at.tzinfo else s.created_at.replace(tzinfo=timezone.utc)
            after = df[idx > created]
            if len(after) < 1:
                continue
            window = after.head(horizon_days)
            buyside = rating in BUY_RATINGS
            outcome, days = "expired", len(window)
            for i, (_, bar) in enumerate(window.iterrows(), start=1):
                hi, lo = float(bar["high"]), float(bar["low"])
                hit_t = hi >= s.target_1 if buyside else lo <= s.target_1
                hit_s = lo <= s.stop_loss if buyside else hi >= s.stop_loss
                if hit_s:                       # conservative: stop counts first if both hit in one bar
                    outcome, days = "stop_hit", i
                    break
                if hit_t:
                    outcome, days = "target_hit", i
                    break
            ref_close = float(window["close"].iloc[min(days, len(window)) - 1])
            ret_pct = round((ref_close - s.entry) / s.entry * 100, 2)
            regime = (((s.payload or {}).get("market") or {}).get("label"))
            cv = ((s.payload or {}).get("conviction") or {}).get("passed")
            band = None if cv is None else ("high" if cv >= 10 else "mid" if cv >= 7 else "low")
            db.add(models.SignalOutcome(signal_id=s.id, symbol=symbol, rating=rating,
                                        regime_label=regime, conviction_band=band,
                                        outcome=outcome, ret_pct=ret_pct, days_held=days))
            evaluated += 1
            w = _is_win(rating, outcome, ret_pct)
            wins += 1 if w else 0
            losses += 1 if w is False else 0
    db.commit()
    return {"evaluated": evaluated, "wins": wins, "losses": losses}


def track_record(db) -> dict:
    """Honest scoreboard of past calls, by rating, plus plain-English lessons."""
    from app import models
    rows = db.query(models.SignalOutcome).all()
    by_rating: dict[str, dict] = {}
    regime_buckets = {"up": [0, 0], "down": [0, 0]}          # [wins, graded]
    for o in rows:
        w = _is_win(o.rating, o.outcome, o.ret_pct)
        b = by_rating.setdefault(o.rating, {"samples": 0, "graded": 0, "wins": 0, "rets": []})
        b["samples"] += 1
        b["rets"].append(o.ret_pct)
        if w is not None:
            b["graded"] += 1
            b["wins"] += 1 if w else 0
            if o.rating in BUY_RATINGS and o.regime_label:
                key = "up" if "up" in o.regime_label else ("down" if "down" in o.regime_label else None)
                if key:
                    regime_buckets[key][1] += 1
                    regime_buckets[key][0] += 1 if w else 0
    table = []
    for rating, b in by_rating.items():
        hit = round(100 * b["wins"] / b["graded"], 1) if b["graded"] else None
        table.append({"rating": rating, "samples": b["samples"], "graded": b["graded"],
                      "hit_rate": hit,
                      "avg_move_pct": round(sum(b["rets"]) / len(b["rets"]), 2)})
    order = ["STRONG BUY", "BUY", "ACCUMULATE", "HOLD", "REDUCE", "SELL", "STRONG SELL"]
    table.sort(key=lambda r: order.index(r["rating"]) if r["rating"] in order else 99)

    bands: dict[str, list] = {"high": [0, 0], "mid": [0, 0], "low": [0, 0]}
    for o in rows:
        w = _is_win(o.rating, o.outcome, o.ret_pct)
        if w is not None and o.conviction_band in bands:
            bands[o.conviction_band][1] += 1
            bands[o.conviction_band][0] += 1 if w else 0
    by_conviction = [{"band": b, "graded": n_, "hit_rate": round(100 * w_ / n_, 1) if n_ else None}
                     for b, (w_, n_) in bands.items()]

    lessons = []
    hi_w, hi_n = bands["high"]
    lo_w, lo_n = bands["low"]
    if hi_n >= 8 and lo_n >= 8:
        hi_hit, lo_hit = 100 * hi_w / hi_n, 100 * lo_w / lo_n
        if hi_hit > lo_hit + 10:
            lessons.append(f"High-conviction calls (10-13 checks) hit {hi_hit:.0f}% vs {lo_hit:.0f}% for "
                           f"low-conviction — the checklist earns its keep; favour 7+/9 setups.")
        elif lo_hit >= hi_hit:
            lessons.append(f"Warning: high-conviction calls ({hi_hit:.0f}%) are NOT beating low-conviction "
                           f"({lo_hit:.0f}%) yet — treat all signals with equal caution for now.")
    up_w, up_n = regime_buckets["up"]
    dn_w, dn_n = regime_buckets["down"]
    if up_n >= 8 and dn_n >= 8:
        up_hit, dn_hit = 100 * up_w / up_n, 100 * dn_w / dn_n
        if dn_hit + 12 < up_hit:
            lessons.append(f"Buy calls in DOWN markets hit only {dn_hit:.0f}% vs {up_hit:.0f}% in up markets "
                           f"— the market-regime filter matters; be extra selective in downtrends.")
    for r in table:
        if r["graded"] >= 10 and r["hit_rate"] is not None:
            lessons.append(f"{r['rating']}: {r['hit_rate']}% hit rate over {r['graded']} graded calls "
                           f"(avg move {r['avg_move_pct']:+.1f}%).")
    if not rows:
        lessons.append("No graded history yet — signals need ~5+ days of age before they can be judged.")
    return {"by_rating": table, "by_conviction": by_conviction,
            "lessons": lessons, "total_outcomes": len(rows)}


def calibrate(db, rating: str, regime_label: str | None = None) -> dict | None:
    """Historical hit-rate for THIS kind of call (same rating; same regime if enough data).
    None until ≥10 graded samples — no pretending with tiny samples."""
    from app import models
    q = db.query(models.SignalOutcome).filter(models.SignalOutcome.rating == rating)
    rows = q.all()
    scoped = [o for o in rows if regime_label and o.regime_label == regime_label]
    use, scope = (scoped, f"{rating} in {regime_label} markets") if len(
        [o for o in scoped if _is_win(o.rating, o.outcome, o.ret_pct) is not None]) >= 10 \
        else (rows, f"{rating} signals")
    graded = [(o, _is_win(o.rating, o.outcome, o.ret_pct)) for o in use]
    graded = [(o, w) for o, w in graded if w is not None]
    if len(graded) < 10:
        return None
    hit = round(100 * sum(1 for _, w in graded if w) / len(graded), 1)
    return {"samples": len(graded), "hit_rate": hit, "scope": scope}


RATING_STEP_DOWN = {"STRONG BUY": "BUY", "BUY": "ACCUMULATE", "ACCUMULATE": "HOLD"}


def regime_guardrail(db, rating: str, regime_label: str | None) -> dict | None:
    """If THIS rating has measurably failed in THIS market regime (≥10 graded,
    hit-rate <45%), return the evidence so the caller can step the rating down."""
    if rating not in BUY_RATINGS or not regime_label:
        return None
    from app import models
    rows = (db.query(models.SignalOutcome)
              .filter(models.SignalOutcome.rating == rating,
                      models.SignalOutcome.regime_label == regime_label).all())
    graded = [w for w in (_is_win(o.rating, o.outcome, o.ret_pct) for o in rows) if w is not None]
    if len(graded) < 10:
        return None
    hit = round(100 * sum(graded) / len(graded), 1)
    if hit >= 45.0:
        return None
    return {"hit_rate": hit, "samples": len(graded), "regime": regime_label}
