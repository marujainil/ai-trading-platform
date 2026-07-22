"""Precision Mode — an accuracy-target controller.

The user names a target hit-rate (e.g. 70%). This module searches the engine's
OWN graded history for the loosest setup class — (conviction band, minimum
composite) — whose measured hit-rate meets the target with enough samples. Buy
ratings outside that class are filtered to HOLD. Nothing is promised: the gate
exists only where the data proves it, tightens as evidence demands, and reports
its receipts. Fewer signals is the price of precision; that trade-off is shown,
never hidden.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from app.engines.learning import BUY_RATINGS, _is_win

log = logging.getLogger(__name__)

_FILE = Path(__file__).resolve().parents[2] / ".precision"
MIN_SAMPLES = 15
CUTS = [60, 65, 70, 75, 80]
# loosest → strictest; a looser gate passing the target is preferred (more signals)
BAND_STEPS = [None, "mid_or_high", "high"]


def get_settings() -> dict:
    try:
        if _FILE.exists():
            d = json.loads(_FILE.read_text(encoding="utf-8"))
            return {"enabled": bool(d.get("enabled")),
                    "target": int(d.get("target", 70))}
    except Exception:
        pass
    return {"enabled": False, "target": 70}


def set_settings(enabled: bool, target: int) -> dict:
    target = max(50, min(85, int(target)))
    try:
        _FILE.write_text(json.dumps({"enabled": bool(enabled), "target": target}),
                         encoding="utf-8")
    except Exception:
        pass
    return {"enabled": bool(enabled), "target": target}


def _graded_buy_rows(db):
    from app import models
    rows = (db.query(models.SignalOutcome)
              .filter(models.SignalOutcome.rating.in_(BUY_RATINGS)).all())
    out = []
    for o in rows:
        w = _is_win(o.rating, o.outcome, o.ret_pct)
        if w is not None and o.composite is not None:
            out.append((o, w))
    return out


def _band_ok(band: str | None, requirement: str | None) -> bool:
    if requirement is None:
        return True
    if requirement == "mid_or_high":
        return band in ("mid", "high")
    return band == "high"


def curve(db) -> list[dict]:
    """Strictness vs measured accuracy — where does the target actually live?"""
    graded = _graded_buy_rows(db)
    rows = []
    for req in BAND_STEPS:
        for cut in CUTS:
            cell = [w for o, w in graded if o.composite >= cut and _band_ok(o.conviction_band, req)]
            if not cell:
                continue
            rows.append({"conviction": req or "any", "min_composite": cut,
                         "samples": len(cell),
                         "hit_rate": round(100 * sum(cell) / len(cell), 1)})
    return rows


def recommendation(db, target: int) -> dict | None:
    """Loosest gate whose measured hit-rate ≥ target with ≥MIN_SAMPLES graded
    calls. If none qualifies, report the best-achievable class honestly."""
    graded = _graded_buy_rows(db)
    if not graded:
        return None
    best = None
    for req in BAND_STEPS:                      # loose → strict: prefer more signals
        for cut in CUTS:
            cell = [w for o, w in graded if o.composite >= cut and _band_ok(o.conviction_band, req)]
            if len(cell) < MIN_SAMPLES:
                continue
            hit = round(100 * sum(cell) / len(cell), 1)
            entry = {"min_composite": cut, "min_conviction": req or "any",
                     "samples": len(cell), "hit_rate": hit}
            if best is None or hit > best["hit_rate"]:
                best = entry
            if hit >= target:
                return {**entry, "met": True}
    return {**best, "met": False} if best else None


def signal_band(conviction: dict | None) -> str | None:
    if not conviction or conviction.get("passed") is None:
        return None
    ratio = conviction["passed"] / max(conviction.get("total") or 1, 1)
    return "high" if ratio >= 0.75 else "mid" if ratio >= 0.5 else "low"


def apply_gate(db, result: dict) -> None:
    """Mutates the analysis in place when Precision Mode is on. Filters buy-side
    ratings whose class hasn't measurably earned the target."""
    cfg = get_settings()
    if not cfg["enabled"]:
        return
    target = cfg["target"]
    info: dict = {"enabled": True, "target": target}
    rec = recommendation(db, target)
    if rec is None:
        info.update(gate=None, note="armed — needs graded history (analyze daily, "
                                     "grade after ~5 days; the gate activates itself)")
        result["precision"] = info
        result["reasoning"].append(f"Precision mode ({target}%): armed, waiting for "
                                   f"≥{MIN_SAMPLES} graded signals per class before filtering")
        return
    info["gate"] = rec
    if result.get("rating") not in BUY_RATINGS:
        result["precision"] = info
        return
    band = signal_band(result.get("conviction"))
    passes = (rec["met"]
              and result["composite_score"] >= rec["min_composite"]
              and _band_ok(band, None if rec["min_conviction"] == "any" else rec["min_conviction"]))
    info["passed"] = bool(passes)
    result["precision"] = info
    if passes:
        result["reasoning"].append(
            f"Precision gate PASSED: this setup class measured {rec['hit_rate']}% over "
            f"{rec['samples']} graded calls (target {target}%)")
    else:
        old = result["rating"]
        result["rating"] = "HOLD"
        why = (f"no class has measured ≥{target}% yet (best: {rec['hit_rate']}% at "
               f"composite ≥{rec['min_composite']})" if not rec["met"] else
               f"needs composite ≥{rec['min_composite']} and {rec['min_conviction']} "
               f"conviction — the only class that measured {rec['hit_rate']}%")
        result["reasoning"].append(
            f"Precision mode ({target}%): {old} filtered to HOLD — {why}. "
            f"Fewer signals is the price of the accuracy you asked for.")
