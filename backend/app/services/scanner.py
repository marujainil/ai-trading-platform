"""Market-wide Buy Scanner — research only, no trading.

Walks EVERY available symbol (all NSE+BSE via Groww when the token is set,
otherwise the curated liquid list; plus every Binance USDT pair) through the
full decision engine in fast mode, and keeps a live-ranked list of the best
buy candidates. Runs in a background thread with progress the UI can poll.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from app.engines.decision import analyze_symbol

log = logging.getLogger(__name__)

_ALERTED: set = set()          # (date, symbol) — one STRONG BUY ping per symbol per day


def _alert_strong_buy(row: dict) -> None:
    from datetime import date
    key = (date.today().isoformat(), row["symbol"])
    if key in _ALERTED:
        return
    if row.get("rating") == "STRONG BUY" or row.get("composite", 0) >= 78:
        from app.services import notify
        ok, _ = notify.send(f"🎯 Scanner STRONG BUY: {row['symbol']} — score "
                            f"{row['composite']}, conf {row['confidence']}%, ₹{row['price']:,.2f}")
        if ok:
            _ALERTED.add(key)

TOP_N = 30
_LOCK = threading.Lock()

state: dict = {
    "all": [],            # every symbol scanned, with its score (browsable in the UI)
    "running": False, "scanned": 0, "total": 0, "errors": 0,
    "started_at": None, "updated_at": None, "note": "",
    "top": [],            # ranked buy candidates
    "last_universe": 0,
    "breadth": None,      # live market internals from the scan itself
}


def _breadth(counts: dict) -> dict | None:
    """Market breadth = the market's true 'moment'. When only a small share of
    stocks are above their 50-EMA, a rising index is a narrow, fragile rally."""
    n = counts.get("n", 0)
    if n < 20:
        return None
    pct_ema50 = round(100 * counts["above_ema50"] / n, 1)
    pct_bull = round(100 * counts["bullish_st"] / n, 1)
    if pct_ema50 >= 60:
        mood = "broad participation — healthy, risk-on tape"
    elif pct_ema50 >= 40:
        mood = "mixed participation — selective, stock-picker's tape"
    else:
        mood = "narrow/weak participation — most names below trend, be defensive"
    return {"sampled": n, "pct_above_ema50": pct_ema50,
            "pct_bullish_supertrend": pct_bull,
            "advancers": counts["adv"], "decliners": counts["dec"], "mood": mood}


def _universe() -> tuple[list[str], str]:
    stocks: list[str] = []
    note_bits = []
    try:
        from app.data import groww
        if groww.is_enabled():
            stocks = groww.full_universe(equity_only=True)
            note_bits.append(f"{len(stocks)} NSE+BSE stocks (Groww)")
    except Exception as exc:
        log.warning("scanner: groww universe failed: %s", exc)
    if not stocks:
        from app.data import reference
        stocks = [s for s, _ in reference.POPULAR_STOCKS]
        note_bits.append(f"{len(stocks)} popular stocks (add Groww token for all ~7000)")

    crypto: list[str] = []
    try:
        from app.data import binance
        crypto = binance.universe(top=None) or []
        note_bits.append(f"{len(crypto)} Binance coins")
    except Exception as exc:
        log.warning("scanner: binance universe failed: %s", exc)
    if not crypto:
        from app.data import reference
        crypto = [s for s, _ in reference.POPULAR_CRYPTO]

    return crypto + stocks, " + ".join(note_bits)


def _row(r: dict, kind: str) -> dict:
    return {"symbol": r["symbol"], "kind": kind, "rating": r.get("rating", r["action"]),
            "edge": r.get("edge_score"),
            "action": r["action"], "composite": r["composite_score"],
            "confidence": r["confidence"], "risk": r["risk_score"],
            "price": r["entry"], "trend": r["technical"]["trend"]["label"],
            "sector": (r.get("fundamental") or {}).get("sector")}


def run_full_scan() -> None:
    with _LOCK:
        if state["running"]:
            return
        state["running"] = True
    try:
        symbols, note = _universe()
        state.update(total=len(symbols), scanned=0, errors=0, note=note,
                     started_at=datetime.now(timezone.utc).isoformat())
        found: list[dict] = []
        state["all"] = []
        counts = {"n": 0, "above_ema50": 0, "bullish_st": 0, "adv": 0, "dec": 0}
        for sym in symbols:
            if not state["running"]:            # allow stop
                break
            kind = "crypto" if ("-" in sym and not sym.endswith((".NS", ".BO"))) else "stock"
            try:
                r = analyze_symbol(sym, period="1y", include_news=False)
                ind = r["technical"]["indicators"]
                counts["n"] += 1
                if r["entry"] and ind.get("ema50") and r["entry"] > ind["ema50"]:
                    counts["above_ema50"] += 1
                    counts["adv"] += 1
                else:
                    counts["dec"] += 1
                if ind.get("supertrend_dir") == 1:
                    counts["bullish_st"] += 1
                if counts["n"] % 25 == 0:
                    state["breadth"] = _breadth(counts)
                bullish = ind.get("supertrend_dir") == 1 or "uptrend" in r["technical"]["trend"]["label"]
                row = _row(r, kind)
                row["bullish"] = bool(bullish)
                state["all"].append(row)        # keep everything so nothing is hidden
                if bullish and r["composite_score"] >= 55:
                    _alert_strong_buy(row)
                    found.append(row)
                    found.sort(key=lambda x: (x["composite"], x.get("edge") or 0), reverse=True)
                    del found[TOP_N:]
                    state["top"] = list(found)
            except Exception:
                state["errors"] += 1
            state["scanned"] += 1
            state["updated_at"] = datetime.now(timezone.utc).isoformat()
            time.sleep(0.05)                     # be gentle on the data APIs
        state["top"] = list(found)
        state["breadth"] = _breadth(counts) or state["breadth"]
    finally:
        state["running"] = False
        state["updated_at"] = datetime.now(timezone.utc).isoformat()


def start_background() -> bool:
    if state["running"]:
        return False
    threading.Thread(target=run_full_scan, daemon=True).start()
    return True


def stop() -> None:
    state["running"] = False


def concentration_warning(rows: list[dict]) -> str | None:
    """Ten buys in one sector is one bet wearing ten costumes. Warn when the top
    candidates cluster, because correlated positions fail together."""
    sectors = [r.get("sector") for r in rows[:10] if r.get("sector")]
    if len(sectors) < 5:
        return None
    top_sector, count = max(((sec, sectors.count(sec)) for sec in set(sectors)),
                            key=lambda x: x[1])
    if count >= max(4, len(sectors) * 0.6):
        return (f"Concentration risk: {count} of the top {len(sectors)} candidates are "
                f"{top_sector}. They will rise and fall together — treat them as ONE "
                f"position, not several, when sizing.")
    return None


BUY_SIDE = ("STRONG BUY", "BUY", "ACCUMULATE")


def results(kind: str | None = None, min_score: float = 0.0, only_buy: bool = True,
            ratings: str | None = None, sort: str = "score",
            limit: int = 100, offset: int = 0) -> dict:
    """Browse scanned symbols. Defaults to buy-side ratings only
    (STRONG BUY / BUY / ACCUMULATE); `ratings` selects an exact subset,
    and only_buy=false shows every rating including HOLD and SELL."""
    rows = list(state["all"])
    if kind in ("crypto", "stock"):
        rows = [r for r in rows if r["kind"] == kind]
    if min_score:
        rows = [r for r in rows if (r.get("composite") or 0) >= min_score]
    if ratings:
        wanted = {x.strip().upper() for x in ratings.split(",") if x.strip()}
        rows = [r for r in rows if (r.get("rating") or "").upper() in wanted]
    elif only_buy:
        rows = [r for r in rows if (r.get("rating") or "") in BUY_SIDE]
    key = {"score": lambda r: (r.get("composite") or 0, r.get("edge") or 0),
           "edge": lambda r: r.get("edge") or 0,
           "confidence": lambda r: r.get("confidence") or 0,
           "risk": lambda r: -(r.get("risk") or 0),
           "symbol": lambda r: r.get("symbol") or ""}.get(sort, lambda r: r.get("composite") or 0)
    rows.sort(key=key, reverse=(sort != "symbol"))
    total = len(rows)
    limit = max(1, min(int(limit), 500))
    page = rows[max(0, int(offset)): max(0, int(offset)) + limit]
    scope = [r for r in state["all"] if kind not in ("crypto", "stock") or r["kind"] == kind]
    counts = {"crypto": sum(1 for r in state["all"] if r["kind"] == "crypto"),
              "stock": sum(1 for r in state["all"] if r["kind"] == "stock"),
              "all": len(state["all"]),
              "by_rating": {rt: sum(1 for r in scope if r.get("rating") == rt)
                            for rt in BUY_SIDE},
              "buy_side": sum(1 for r in scope if r.get("rating") in BUY_SIDE)}
    return {"rows": page, "total_matching": total, "offset": offset,
            "limit": limit, "counts": counts, "running": state["running"]}


def status() -> dict:
    out = {k: state[k] for k in ("running", "scanned", "total", "errors",
                                 "started_at", "updated_at", "note", "breadth")}
    out["top"] = state["top"]
    out["concentration"] = concentration_warning(state["top"])
    out["result_counts"] = {"crypto": sum(1 for r in state["all"] if r["kind"] == "crypto"),
                            "stock": sum(1 for r in state["all"] if r["kind"] == "stock"),
                            "all": len(state["all"])}
    try:
        from app.data import groww, binance
        out["groww_connected"] = groww.is_enabled()
        out["binance_connected"] = binance.ping_ok()
        from app.services import notify
        out["telegram_connected"] = notify.is_configured()
    except Exception:
        out["groww_connected"] = out["binance_connected"] = False
    return out
