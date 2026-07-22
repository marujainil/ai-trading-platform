"""Autopilot — the autonomous paper-trader.

Every `scan_interval_sec` (default 15 min) while enabled it:
  1. Manages open positions: sells on stop-loss hit, Target-2 hit, or when the
     AI signal turns bearish; moves the stop to breakeven once Target 1 is hit.
  2. Scans the watchlist with the full decision engine and BUYs the strongest
     fresh signals that clear the configured thresholds — sized and vetted by
     the Risk Manager like any other order.
  3. Writes every decision (and the reason) to the activity feed, and pushes it
     to Telegram if configured.

Equities (.NS / .BO) trade only during NSE hours (Mon–Fri 09:15–15:30 IST);
crypto pairs (BTC-USD, ETH-INR, ...) trade around the clock.
Paper money only — this is research automation, not investment advice.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app import models
from app.config import settings
from app.data import market_data
from app.database import SessionLocal
from app.engines.decision import analyze_symbol
from app.services import portfolio as pf
from app.services.notify import telegram_send

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
NSE_OPEN, NSE_CLOSE = dtime(9, 15), dtime(15, 30)
US_EASTERN = ZoneInfo("America/New_York")
US_OPEN, US_CLOSE = dtime(9, 30), dtime(16, 0)

DEFAULT_WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "SBIN.NS", "LT.NS", "ITC.NS", "BHARTIARTL.NS", "TATAMOTORS.NS",
    "BTC-USD", "ETH-USD",
]

# in-memory loop state (reset on restart; config itself lives in the DB)
_state = {"last_run": 0.0, "running": False, "last_error": None, "last_overnight": 0.0,
          "universe_offset": 0}


def _resolve_symbols(cfg: models.AutopilotConfig) -> tuple[list[str], str]:
    """Return (symbols_to_scan_this_cycle, human_note).

    watchlist mode: exactly the configured symbols.
    entire_market mode: all NSE+BSE (Groww) + top crypto (Binance), scanned in
    rotating slices so the full lists are covered over several cycles. Crypto is
    always live (24×7); stocks are researched anytime and traded when the exchange
    is open.
    """
    watchlist = [s.strip().upper() for s in (cfg.watchlist or []) if s and s.strip()]
    if cfg.universe_mode != "entire_market":
        return watchlist, "watchlist"

    from app.data import binance, groww

    stocks: list[str] = []
    if groww.is_enabled():
        try:
            stocks = groww.full_universe(equity_only=True)
        except Exception as exc:
            log.warning("Groww universe failed (%s)", exc)

    crypto_all: list[str] = []
    try:
        crypto_all = binance.universe(top=None)         # ALL pairs, keyless
    except Exception as exc:
        log.warning("Binance universe failed (%s)", exc)

    # keep any crypto the user explicitly added, up front
    user_crypto = [s for s in watchlist if asset_class(s) == "crypto"]

    if not stocks and not crypto_all:
        return watchlist, "entire_market requested but no data source available — using watchlist"

    slice_n = max(50, settings.universe_scan_slice)
    off = _state.get("universe_offset", 0)

    stock_slice = _rotate(stocks, off, slice_n) if stocks else []
    crypto_slice = _rotate(crypto_all, off, max(20, slice_n // 5)) if crypto_all else []
    _state["universe_offset"] = off + slice_n

    # de-dupe while preserving order: user crypto, then rotating crypto, then stocks
    seen, symbols = set(), []
    for s in user_crypto + crypto_slice + stock_slice:
        if s not in seen:
            seen.add(s)
            symbols.append(s)

    note = (f"entire_market: {len(stocks)} NSE+BSE (Groww{'' if stocks else ' off'}) + "
            f"{len(crypto_all)} crypto (Binance) — this cycle {len(stock_slice)} stocks + "
            f"{len(crypto_slice) + len(user_crypto)} crypto")
    return symbols, note


def _rotate(items: list[str], offset: int, n: int) -> list[str]:
    if not items:
        return []
    off = offset % len(items)
    window = items[off:off + n]
    if len(window) < n and len(items) > n:              # wrap around
        window += items[: n - len(window)]
    return window


# ------------------------------------------------------------------ helpers

def asset_class(symbol: str) -> str:
    """'india' (.NS/.BO), 'crypto' (BTC-USD, ...), else 'us' (AAPL, MSFT, ...)."""
    su = symbol.upper()
    if su.endswith((".NS", ".BO")):
        return "india"
    if "-" in su:
        return "crypto"
    return "us"


def is_crypto(symbol: str) -> bool:
    return asset_class(symbol) == "crypto"


def market_open_ist(now: datetime | None = None) -> bool:
    now = now.astimezone(IST) if now else datetime.now(IST)
    return now.weekday() < 5 and NSE_OPEN <= now.time() <= NSE_CLOSE


def market_open_us(now: datetime | None = None) -> bool:
    now = now.astimezone(US_EASTERN) if now else datetime.now(US_EASTERN)
    return now.weekday() < 5 and US_OPEN <= now.time() <= US_CLOSE


def market_open_for(symbol: str) -> bool:
    cls = asset_class(symbol)
    if cls == "crypto":
        return True
    return market_open_ist() if cls == "india" else market_open_us()


def get_config(db: Session) -> models.AutopilotConfig:
    cfg = db.query(models.AutopilotConfig).first()
    if not cfg:
        cfg = models.AutopilotConfig(watchlist=DEFAULT_WATCHLIST)
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
    return cfg


def log_event(db: Session, kind: str, message: str, symbol: str | None = None,
              payload: dict | None = None, notify: bool = False) -> None:
    db.add(models.AutopilotEvent(kind=kind, symbol=symbol, message=message, payload=payload))
    db.commit()
    log.info("[autopilot:%s] %s", kind, message)
    if notify:
        telegram_send(f"🤖 {message}")


def _groww_connected() -> bool:
    try:
        from app.data import groww
        return groww.is_enabled()
    except Exception:
        return False


def _binance_connected() -> bool:
    try:
        from app.data import binance
        return binance.ping_ok()
    except Exception:
        return False


def status(db: Session) -> dict:
    cfg = get_config(db)
    nxt = max(0, int(cfg.scan_interval_sec - (time.time() - _state["last_run"]))) if cfg.enabled else None
    return {
        "enabled": cfg.enabled,
        "running_now": _state["running"],
        "market_open_nse": market_open_ist(),
        "last_scan_ago_sec": int(time.time() - _state["last_run"]) if _state["last_run"] else None,
        "next_scan_in_sec": nxt,
        "last_error": _state["last_error"],
        "groww_connected": _groww_connected(),
        "binance_connected": _binance_connected(),
    }


# ------------------------------------------------------------- trading logic

def _manage_exits(db: Session, cfg: models.AutopilotConfig) -> int:
    """Apply stop / target / signal-flip rules to open positions. Returns exits made."""
    exits = 0
    for pos in db.query(models.Position).all():
        sym = pos.symbol
        if not market_open_for(sym):
            continue
        try:
            last = market_data.last_price(sym)
        except market_data.DataError as exc:
            log_event(db, "ERROR", f"{sym}: price check failed ({exc})", sym)
            continue

        reason = None
        if pos.stop_loss and last <= pos.stop_loss:
            reason = f"Stop-loss hit at ₹{last:,.2f} (stop ₹{pos.stop_loss:,.2f})"
        elif pos.target_2 and last >= pos.target_2:
            reason = f"Target 2 reached at ₹{last:,.2f} 🎯"
        elif pos.target_1 and last >= pos.target_1 and (pos.stop_loss or 0) < pos.avg_price:
            pos.stop_loss = round(pos.avg_price, 2)
            db.commit()
            log_event(db, "INFO", f"{sym}: Target 1 reached — stop moved to breakeven "
                                  f"(₹{pos.avg_price:,.2f}). Letting the rest run to Target 2.",
                      sym, notify=True)
            continue
        else:
            try:  # signal flip check (fast, no news)
                sig = analyze_symbol(sym, period="1y", include_news=False)
                if sig["action"] == "SELL":
                    reason = (f"AI signal turned bearish (composite "
                              f"{sig['composite_score']}/100) — exiting")
            except Exception:
                pass

        if reason:
            res = pf.execute_order(db, sym, "SELL", pos.quantity, signal=None)
            if res.get("status") == "FILLED":
                exits += 1
                pnl = res.get("realized_pnl", 0.0)
                emoji = "🟢" if pnl >= 0 else "🔴"
                log_event(db, "EXIT",
                          f"{emoji} SOLD {res['quantity']} {sym} @ ₹{res['price']:,.2f} — {reason}. "
                          f"P&L ₹{pnl:,.2f}.", sym, payload=res, notify=True)
            else:
                log_event(db, "ERROR", f"{sym}: exit failed — {res.get('reason')}", sym)
    return exits


COOLDOWN_HOURS = 24              # after exiting a symbol, don't re-buy it for this long
OVERNIGHT_RESEARCH_EVERY_SEC = 6 * 3600   # after-hours equity research cadence


def _passes_entry(r: dict, cfg: models.AutopilotConfig) -> bool:
    """Autopilot's own entry gate — driven by YOUR settings, not the engine's
    fixed 65 BUY threshold. Requires a bullish trend confirmation plus your
    minimum composite/confidence and maximum risk."""
    ind = r["technical"]["indicators"]
    bullish = ind.get("supertrend_dir") == 1 or "uptrend" in r["technical"]["trend"]["label"]
    return (bullish
            and r["composite_score"] >= cfg.min_composite
            and r["confidence"] >= cfg.min_confidence
            and r["risk_score"] <= cfg.max_risk_score)


def _recent_exits(db: Session) -> set[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=COOLDOWN_HOURS)
    rows = (db.query(models.AutopilotEvent)
            .filter(models.AutopilotEvent.kind == "EXIT",
                    models.AutopilotEvent.created_at >= cutoff).all())
    return {r.symbol for r in rows if r.symbol}


def _enter_new(db: Session, cfg: models.AutopilotConfig, symbols: list[str]) -> int:
    held = {p.symbol for p in db.query(models.Position).all()}
    cooling = _recent_exits(db)
    candidates, diag = [], []
    for sym in symbols:
        if sym in held or sym in cooling:      # cooldown stops instant re-entry after a stop-out
            continue
        try:
            r = analyze_symbol(sym, period="1y", include_news=False)
        except Exception as exc:
            log_event(db, "ERROR", f"{sym}: analysis failed ({exc})", sym)
            continue
        ind = r["technical"]["indicators"]
        diag.append((sym, r["composite_score"], r["confidence"],
                     ind.get("supertrend_dir") == 1 or "uptrend" in r["technical"]["trend"]["label"]))
        if _passes_entry(r, cfg):
            candidates.append(r)

    candidates.sort(key=lambda r: r["composite_score"], reverse=True)
    buys = 0
    for r in candidates[: max(0, cfg.max_new_positions_per_cycle)]:
        sym = r["symbol"]
        try:  # confirm with the full analysis (news included) before committing
            full = analyze_symbol(sym, period="1y", include_news=True)
        except Exception:
            full = r
        if not _passes_entry(full, cfg):
            log_event(db, "SKIP", f"{sym}: dropped after the full news check "
                                  f"(composite {full['composite_score']}/100, "
                                  f"confidence {full['confidence']}%).", sym)
            continue
        res = pf.execute_order(db, sym, "BUY", None, signal=full)
        if res.get("status") == "FILLED":
            buys += 1
            why = full["reasoning"][1] if len(full["reasoning"]) > 1 else full["reasoning"][0]
            log_event(db, "BUY",
                      f"🟢 BOUGHT {res['quantity']} {sym} @ ₹{res['price']:,.2f} | "
                      f"composite {full['composite_score']}/100, confidence {full['confidence']}%. "
                      f"Stop ₹{full['stop_loss']:,.2f}, T1 ₹{full['target_1']:,.2f}, "
                      f"T2 ₹{full['target_2']:,.2f}. Why: {why}",
                      sym, payload={"order": res, "scores": full["scores"]}, notify=True)
        else:
            log_event(db, "SKIP", f"{sym}: BUY blocked by Risk Manager — {res.get('reason')}.", sym)
    return buys, diag


def run_cycle(db: Session, force: bool = False) -> dict:
    cfg = get_config(db)
    if not cfg.enabled and not force:
        return {"skipped": "autopilot disabled"}

    resolved, universe_note = _resolve_symbols(cfg)
    india = [s for s in resolved if asset_class(s) == "india"]
    us = [s for s in resolved if asset_class(s) == "us"]
    crypto = [s for s in resolved if asset_class(s) == "crypto"]
    nse_open, us_open = market_open_ist(), market_open_us()
    symbols = crypto + (india if nse_open else []) + (us if us_open else [])

    out = {"scanned": 0, "buys": 0, "exits": 0, "universe": universe_note}

    # After hours the AI keeps working: research Indian stocks, queue them for the open.
    if india and not nse_open:
        due = time.time() - _state.get("last_overnight", 0) >= OVERNIGHT_RESEARCH_EVERY_SEC
        if force or due:
            _state["last_overnight"] = time.time()
            out["after_hours_candidates"] = _after_hours_research(db, cfg, india[:80])

    if not symbols:
        return {**out, "skipped": "All relevant markets closed — after-hours research mode "
                                  "(add crypto like BTC-USD for round-the-clock trading)"}

    exits = _manage_exits(db, cfg) if cfg.auto_manage_exits else 0
    buys, diag = _enter_new(db, cfg, symbols)

    diag.sort(key=lambda d: d[1], reverse=True)
    scores = ", ".join(f"{sym} {comp:.0f}{'↑' if bull else '↔'}" for sym, comp, _, bull in diag[:8])
    gate = (f"gate: composite ≥{cfg.min_composite:.0f}, confidence ≥{cfg.min_confidence:.0f}%, "
            f"risk ≤{cfg.max_risk_score}, bullish trend (↑)")
    markets = " + ".join((["NSE/BSE"] if nse_open and india else [])
                         + (["US"] if us_open and us else [])
                         + (["crypto"] if crypto else [])) or "—"
    mode_tag = "entire market" if cfg.universe_mode == "entire_market" else "watchlist"
    summary = pf.portfolio_summary(db)
    log_event(db, "SCAN",
              f"Scan done ({markets}, {mode_tag}): {len(symbols)} checked · {buys} bought · "
              f"{exits} exited · equity ₹{summary['equity']:,.0f} "
              f"({summary['total_return_pct']:+.2f}%). Scores: {scores or 'all held/cooling'} | {gate}.")
    return {**out, "scanned": len(symbols), "buys": buys, "exits": exits,
            "equity": summary["equity"]}


def _after_hours_research(db: Session, cfg: models.AutopilotConfig, equities: list[str]) -> int:
    """Overnight/weekend pass: rank the stock watchlist so buys queue for the open."""
    held = {p.symbol for p in db.query(models.Position).all()}
    cooling = _recent_exits(db)
    picks = []
    for sym in equities:
        if sym in held or sym in cooling:
            continue
        try:
            r = analyze_symbol(sym, period="1y", include_news=False)
        except Exception:
            continue
        if _passes_entry(r, cfg):
            picks.append(r)
    picks.sort(key=lambda r: r["composite_score"], reverse=True)
    if picks:
        names = ", ".join(f"{r['symbol']} ({r['composite_score']:.0f}/100)" for r in picks[:5])
        log_event(db, "INFO",
                  f"🌙 After-hours research: {len(picks)} BUY candidate(s) for the next open — "
                  f"{names}. They'll be bought automatically at 09:15 IST if the signals hold.",
                  notify=True)
    else:
        log_event(db, "INFO", "🌙 After-hours research: no watchlist stock clears the "
                              "thresholds right now. Will re-check before the open.")
    return len(picks)


# ------------------------------------------------------------ background loop

async def autopilot_loop() -> None:
    await asyncio.sleep(3)                                  # let the app finish booting
    log.info("Autopilot loop started (idle until enabled).")
    while True:
        try:
            with SessionLocal() as db:
                cfg = get_config(db)
                due = time.time() - _state["last_run"] >= cfg.scan_interval_sec
                if cfg.enabled and due and not _state["running"]:
                    _state["running"] = True
                    try:
                        await asyncio.to_thread(_cycle_in_fresh_session)
                        _state["last_error"] = None
                    finally:
                        _state["last_run"] = time.time()
                        _state["running"] = False
        except asyncio.CancelledError:
            raise
        except Exception as exc:                            # keep the loop alive no matter what
            _state["last_error"] = str(exc)
            log.exception("Autopilot loop error")
        await asyncio.sleep(5)


def _cycle_in_fresh_session() -> None:
    with SessionLocal() as db:
        run_cycle(db)
