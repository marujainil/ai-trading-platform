from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app import models
from app.data import market_data
from app.database import get_db
from app.engines import learning
from app.engines.backtest import run_backtest
from app.engines.decision import analyze_symbol
from app.engines.risk import position_size
from app.schemas import AutopilotConfigIn, BacktestIn, OrderIn, RiskLimitsIn
from app.services import autopilot as ap
from app.services import portfolio as pf

router = APIRouter()

DEFAULT_WATCHLIST = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "LT.NS", "TATAMOTORS.NS",
]


@router.get("/health", tags=["system"])
def health():
    return {"status": "ok", "mode": "paper", "docs": "/docs"}


# ------------------------------- Research --------------------------------- #

def _clean_period(period: str) -> str:
    return period.strip().strip("\\/'\" ") or "1y"


@router.get("/analyze/{symbol}", tags=["research"],
            summary="Full AI analysis of one symbol (technical + fundamental + news + market)")
def analyze(symbol: str, period: str = Query("1y"), db: Session = Depends(get_db)):
    try:
        result = analyze_symbol(symbol.strip().upper(), period=_clean_period(period))
    except market_data.DataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    # Learning loop: blend model confidence with the MEASURED hit-rate of similar past calls
    try:
        from app.engines import learning as learn_engine
        cal = learn_engine.calibrate(db, result["rating"],
                                     (result.get("market_regime") or {}).get("label"))
        if cal:
            raw = result["confidence"]
            blended = round(0.6 * raw + 0.4 * cal["hit_rate"], 1)
            result["confidence"] = max(raw - 15.0, min(raw + 15.0, blended))
            result["track_record"] = cal
            result["reasoning"].append(
                f"Track record: past {cal['scope']} hit {cal['hit_rate']}% over "
                f"{cal['samples']} graded calls — confidence calibrated to real results")
        guard = learn_engine.regime_guardrail(db, result["rating"],
                                              (result.get("market_regime") or {}).get("label"))
        if guard:
            old_rating = result["rating"]
            result["rating"] = learn_engine.RATING_STEP_DOWN.get(old_rating, old_rating)
            result["guardrail"] = {**guard, "from": old_rating, "to": result["rating"]}
            result["reasoning"].append(
                f"Learning guardrail: past {old_rating} calls in {guard['regime']} markets hit only "
                f"{guard['hit_rate']}% ({guard['samples']} graded) — rating stepped down to {result['rating']}")
        from app.engines import precision
        precision.apply_gate(db, result)
    except Exception:
        pass

    db.add(models.Signal(symbol=symbol, action=result["action"],
                         composite_score=result["composite_score"], confidence=result["confidence"],
                         risk_score=result["risk_score"], entry=result["entry"],
                         stop_loss=result["stop_loss"], target_1=result["target_1"],
                         target_2=result["target_2"],
                         payload={"scores": result["scores"], "reasoning": result["reasoning"]}))
    db.commit()
    return result


@router.get("/screener", tags=["research"],
            summary="Scan a watchlist (fast mode: technicals + cached fundamentals, news skipped)")
def screener(symbols: str | None = Query(None, description="Comma-separated; default NIFTY-10 sample"),
             period: str = Query("1y")):
    syms = [s.strip() for s in (symbols.split(",") if symbols else DEFAULT_WATCHLIST) if s.strip()][:30]
    results, errors = [], {}
    for s in syms:
        try:
            r = analyze_symbol(s, period=period, include_news=False)
            results.append({
                "symbol": s, "action": r["action"], "composite_score": r["composite_score"],
                "confidence": r["confidence"], "risk_score": r["risk_score"],
                "last_close": r["entry"], "trend": r["technical"]["trend"]["label"],
                "rsi14": r["technical"]["indicators"]["rsi14"],
                "sector": r["fundamental"].get("sector"),
                "technical_score": r["scores"]["technical"],
                "fundamental_score": r["scores"]["fundamental"],
            })
        except Exception as exc:  # keep scanning; report per-symbol failures
            errors[s] = str(exc)

    results.sort(key=lambda x: x["composite_score"], reverse=True)

    sector_scores: dict[str, list[float]] = {}
    for r in results:
        sector_scores.setdefault(r["sector"] or "Unknown", []).append(r["technical_score"])
    sector_strength = {k: round(sum(v) / len(v), 1) for k, v in sector_scores.items()}

    return {"count": len(results), "results": results,
            "sector_strength": dict(sorted(sector_strength.items(), key=lambda kv: kv[1], reverse=True)),
            "errors": errors,
            "note": "Fast scan skips per-symbol news; run /analyze/{symbol} for the full picture."}


# Chart timeframes: button → (Yahoo period, candle interval)
TIMEFRAMES = {
    "5m":  ("1d", "5m"),    # today's session, 5-minute candles
    "10m": ("5d", "10m"),   # built by resampling 5-minute data
    "15m": ("5d", "15m"),
    "1h":  ("3mo", "1h"),
    "1d":  ("2y", "1d"),    # ~2 years of daily candles
    "1w":  ("5y", "1wk"),   # ~5 years of weekly candles
    "1mo": ("1mo", "1d"),   # one month of daily candles
    "6mo": ("6mo", "1d"),
    "1y":  ("1y", "1d"),
}


@router.get("/chart/{symbol}", tags=["research"],
            summary="Chart series (candles, volume, EMAs, RSI, supertrend) for the dashboard")
def chart(symbol: str, tf: str = Query("1y", description=f"One of: {', '.join(TIMEFRAMES)}")):
    from app.services.charting import build_chart_payload
    tf = tf.strip().lower()
    if tf not in TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"tf must be one of: {', '.join(TIMEFRAMES)}")
    period, interval = TIMEFRAMES[tf]
    intraday = interval not in ("1d", "1wk")
    try:
        df = market_data.get_ohlcv(symbol.strip().upper(), period=period, interval=interval,
                                   min_bars=10)
    except market_data.DataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    su = symbol.strip().upper()
    payload = build_chart_payload(df, intraday=intraday)   # df already ₹ for USD assets
    payload["symbol"] = su
    payload["tf"] = tf
    payload["fx_rate"] = market_data.get_usd_inr() if market_data.is_usd_asset(su) else None
    payload["currency"] = "INR"

    # Institutional overlays: volume-profile levels + anchored-VWAP line
    try:
        from app.engines import technical as ta
        vp = ta.volume_profile(df)
        av = ta.anchored_vwap(df)
        fb = ta.fib_position(df)
        lv = {"poc": vp.get("poc"), "vah": vp.get("vah"), "val": vp.get("val")} \
            if vp.get("available") else {}
        if fb.get("available"):
            lv.update(fib_500=fb.get("level_500"), fib_618=fb.get("level_618"),
                      fib_zone=fb.get("zone"), swing_high=fb.get("swing_high"),
                      swing_low=fb.get("swing_low"))
        payload["levels"] = lv or None
        if av.get("available"):
            anchor_i = int(df.index.get_indexer([df.tail(252)["low"].idxmin()])[0])
            seg = df.iloc[anchor_i:]
            tp = (seg["high"] + seg["low"] + seg["close"]) / 3
            v = seg["volume"].astype(float).replace(0, 1.0)
            series = ((tp * v).cumsum() / v.cumsum()).round(2).tolist()
            times = [c["time"] for c in payload["candles"][anchor_i:]]
            payload["avwap"] = [{"time": t, "value": val}
                                for t, val in zip(times, series) if val == val]
            payload["avwap_anchor"] = av["anchor_date"]
        else:
            payload["avwap"] = []
    except Exception:
        payload["levels"] = None
        payload["avwap"] = []
    return payload


@router.get("/market/regime", tags=["research"])
def market_regime():
    return market_data.get_market_regime()


@router.get("/universe", tags=["research"],
            summary="Browsable symbol lists for the Advisor (stocks + crypto)")
def universe():
    from app.data import reference
    stocks = [{"symbol": s, "name": n, "kind": "stock"} for s, n in reference.POPULAR_STOCKS]
    crypto = [{"symbol": s, "name": n, "kind": "crypto"} for s, n in reference.POPULAR_CRYPTO]

    groww_all = 0
    try:
        from app.data import groww
        if groww.is_enabled():
            full = groww.full_universe(equity_only=True)
            groww_all = len(full)
            have = {s["symbol"] for s in stocks}
            for sym in full:
                if sym not in have:
                    stocks.append({"symbol": sym, "name": sym[:-3], "kind": "stock"})
    except Exception:
        pass

    binance_live = False
    try:
        from app.data import binance
        extra = binance.universe(top=None)          # ALL tradable USDT pairs
        binance_live = bool(extra)
        have = {c["symbol"] for c in crypto}
        for sym in extra:
            if sym not in have:
                crypto.append({"symbol": sym, "name": sym[:-4], "kind": "crypto"})
    except Exception:
        pass

    return {"stocks": stocks, "crypto": crypto,
            "counts": {"stocks": len(stocks), "crypto": len(crypto)},
            "sources": {"groww_stocks": groww_all, "binance_live": binance_live}}


@router.get("/signals", tags=["research"], summary="Recent AI recommendations (research log)")
def signals(limit: int = Query(50, le=500), db: Session = Depends(get_db)):
    rows = db.query(models.Signal).order_by(models.Signal.created_at.desc()).limit(limit).all()
    return [{"symbol": r.symbol, "action": r.action, "composite": r.composite_score,
             "confidence": r.confidence, "risk_score": r.risk_score, "entry": r.entry,
             "stop_loss": r.stop_loss, "target_1": r.target_1, "target_2": r.target_2,
             "at": r.created_at.isoformat()} for r in rows]


# ------------------------------ Backtesting -------------------------------- #

@router.post("/backtest", tags=["backtesting"])
def backtest(body: BacktestIn, db: Session = Depends(get_db)):
    try:
        df = market_data.get_ohlcv(body.symbol, period=body.period)
    except market_data.DataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    result = run_backtest(df, body.params)
    result["symbol"] = body.symbol
    result["period"] = body.period
    db.add(models.BacktestRun(symbol=body.symbol,
                              params={"period": body.period, **result["params"]},
                              metrics=result["metrics"]))
    db.commit()
    return result


# ------------------------- Trading (paper) & risk -------------------------- #

@router.post("/orders", tags=["trading"], summary="Place a risk-checked paper order")
def place_order(body: OrderIn, db: Session = Depends(get_db)):
    signal = None
    if body.use_signal:
        try:
            signal = analyze_symbol(body.symbol, include_news=False)
        except market_data.DataError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
    try:
        result = pf.execute_order(db, body.symbol.upper(), body.side, body.quantity, signal=signal)
    except market_data.DataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    if signal:
        result["signal_used"] = {"action": signal["action"], "entry": signal["entry"],
                                 "stop_loss": signal["stop_loss"], "confidence": signal["confidence"]}
    return result


@router.get("/orders/preview", tags=["trading"],
            summary="Preview Risk-Manager position sizing for an entry/stop")
def preview_size(entry: float, stop: float, db: Session = Depends(get_db)):
    ctx = pf.build_risk_context(db)
    return {"context": {"equity": round(ctx.equity, 2), "cash": round(ctx.cash, 2),
                        "exposure": round(ctx.exposure, 2), "open_positions": ctx.open_positions},
            "sizing": position_size(ctx, entry, stop)}


@router.get("/portfolio", tags=["portfolio"])
def portfolio(db: Session = Depends(get_db)):
    return pf.portfolio_summary(db)


@router.get("/trades", tags=["portfolio"], summary="Executed order log")
def trades(limit: int = Query(100, le=1000), db: Session = Depends(get_db)):
    rows = db.query(models.Trade).order_by(models.Trade.created_at.desc()).limit(limit).all()
    return [{"id": r.id, "symbol": r.symbol, "side": r.side, "qty": r.quantity,
             "price": r.price, "realized_pnl": r.realized_pnl, "broker": r.broker,
             "at": r.created_at.isoformat()} for r in rows]


@router.get("/risk/limits", tags=["risk"])
def get_limits(db: Session = Depends(get_db)):
    lim = pf.get_limits(db)
    return {c.name: getattr(lim, c.name) for c in lim.__table__.columns if c.name != "id"}


@router.put("/risk/limits", tags=["risk"])
def update_limits(body: RiskLimitsIn, db: Session = Depends(get_db)):
    lim = pf.get_limits(db)
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(lim, field, value)
    db.commit()
    return {c.name: getattr(lim, c.name) for c in lim.__table__.columns if c.name != "id"}


# ------------------------------- Learning ---------------------------------- #

@router.get("/learning/insights", tags=["learning"],
            summary="What worked / what failed, from completed paper trades")
def insights(db: Session = Depends(get_db)):
    rows = db.query(models.ClosedTrade).all()
    data = [{"symbol": r.symbol, "pnl": r.pnl, "pnl_pct": r.pnl_pct,
             "holding_days": r.holding_days, "entry_snapshot": r.entry_snapshot} for r in rows]
    return learning.analyze_trades(data)


# ------------------------------- Autopilot --------------------------------- #

@router.get("/autopilot", tags=["autopilot"], summary="Autopilot status + settings")
def autopilot_get(db: Session = Depends(get_db)):
    cfg = ap.get_config(db)
    return {"status": ap.status(db),
            "config": {c.name: getattr(cfg, c.name) for c in cfg.__table__.columns
                       if c.name not in ("id", "updated_at")}}


@router.put("/autopilot/config", tags=["autopilot"])
def autopilot_update(body: AutopilotConfigIn, db: Session = Depends(get_db)):
    cfg = ap.get_config(db)
    data = body.model_dump(exclude_none=True)
    if "watchlist" in data:
        data["watchlist"] = [s.strip().upper() for s in data["watchlist"] if s and s.strip()]
    for field, value in data.items():
        setattr(cfg, field, value)
    db.commit()
    return autopilot_get(db)


@router.post("/autopilot/start", tags=["autopilot"])
def autopilot_start(db: Session = Depends(get_db)):
    cfg = ap.get_config(db)
    if not cfg.enabled:
        cfg.enabled = True
        db.commit()
        ap.log_event(db, "INFO", "Autopilot STARTED — scanning the watchlist every "
                                 f"{cfg.scan_interval_sec // 60} min (paper money).", notify=True)
    return autopilot_get(db)


@router.post("/autopilot/stop", tags=["autopilot"])
def autopilot_stop(db: Session = Depends(get_db)):
    cfg = ap.get_config(db)
    if cfg.enabled:
        cfg.enabled = False
        db.commit()
        ap.log_event(db, "INFO", "Autopilot STOPPED. Open paper positions are kept as-is.",
                     notify=True)
    return autopilot_get(db)


@router.post("/autopilot/run-once", tags=["autopilot"],
             summary="Run one scan/trade cycle right now (works even while stopped)")
def autopilot_run_once(db: Session = Depends(get_db)):
    return ap.run_cycle(db, force=True)


@router.get("/autopilot/events", tags=["autopilot"], summary="Activity feed (newest first)")
def autopilot_events(limit: int = Query(80, le=500), db: Session = Depends(get_db)):
    rows = (db.query(models.AutopilotEvent)
            .order_by(models.AutopilotEvent.created_at.desc()).limit(limit).all())
    return [{"at": r.created_at.isoformat(), "kind": r.kind, "symbol": r.symbol,
             "message": r.message} for r in rows]


# ------------------------- Groww token (in-app) ---------------------------- #

@router.post("/settings/groww-token", tags=["autopilot"],
             summary="Paste today's Groww access token (no server restart needed)")
def set_groww_token(body: dict):
    from app.data import groww
    token = (body or {}).get("token", "")
    result = groww.set_token(token)
    return result


# ------------------------------- Live price -------------------------------- #

@router.get("/price/{symbol}", tags=["research"],
            summary="Latest price for the live ticker (fast, lightly cached)")
def price(symbol: str):
    try:
        px = market_data.last_price(symbol.strip().upper())
    except market_data.DataError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return {"symbol": symbol.strip().upper(), "price": round(float(px), 2)}


# ------------------------ Market-wide Buy Scanner --------------------------- #

@router.post("/scan/start", tags=["research"],
             summary="Scan EVERY Groww stock + Binance coin for buy candidates")
def scan_start():
    from app.services import scanner
    started = scanner.start_background()
    return {"started": started, **scanner.status()}


@router.post("/scan/stop", tags=["research"])
def scan_stop():
    from app.services import scanner
    scanner.stop()
    return scanner.status()


@router.get("/scan/results", tags=["research"],
            summary="Browse EVERY scanned symbol with filters (crypto/stocks, score, sort)")
def scan_results(kind: str | None = None, min_score: float = 0.0, only_buy: bool = True,
                 ratings: str | None = None, sort: str = "score",
                 limit: int = 100, offset: int = 0):
    from app.services import scanner
    return scanner.results(kind=kind, min_score=min_score, only_buy=only_buy,
                           ratings=ratings, sort=sort, limit=limit, offset=offset)


@router.get("/scan/status", tags=["research"], summary="Scanner progress + ranked buy list")
def scan_status():
    from app.services import scanner
    return scanner.status()


@router.post("/backtest/batch", tags=["backtesting"],
             summary="Strategy report card: backtest many symbols, aggregate the TRUE historical stats")
def backtest_batch(body: dict | None = None):
    symbols = (body or {}).get("symbols") or DEFAULT_WATCHLIST
    period = (body or {}).get("period", "5y")
    per, agg_trades, wins, gross_p, gross_l, total_pnl = [], 0, 0, 0.0, 0.0, 0.0
    for sym in symbols[:15]:
        try:
            df = market_data.get_ohlcv(sym, period=period)
            r = run_backtest(df, {})
            m = r["metrics"]
            per.append({"symbol": sym, "trades": m["total_trades"], "win_rate": m["win_rate"],
                        "total_return_pct": m["total_return_pct"],
                        "profit_factor": m["profit_factor"],
                        "expectancy": m["expectancy_per_trade"]})
            agg_trades += m["total_trades"]
            wins += round(m["total_trades"] * m["win_rate"] / 100)
            total_pnl += m["final_equity"] - r["params"]["starting_equity"]
            if m["avg_win"] and m["total_trades"]:
                gross_p += m["avg_win"] * round(m["total_trades"] * m["win_rate"] / 100)
            if m["avg_loss"] and m["total_trades"]:
                gross_l += abs(m["avg_loss"]) * (m["total_trades"] - round(m["total_trades"] * m["win_rate"] / 100))
        except Exception as exc:
            per.append({"symbol": sym, "error": str(exc)[:90]})
    win_rate = round(100 * wins / agg_trades, 1) if agg_trades else None
    pf = round(gross_p / gross_l, 2) if gross_l > 0 else None
    return {"period": period, "symbols_tested": len(per), "per_symbol": per,
            "aggregate": {"total_trades": agg_trades, "win_rate_pct": win_rate,
                          "profit_factor": pf,
                          "avg_pnl_per_trade": round(total_pnl / agg_trades, 2) if agg_trades else None},
            "honest_note": ("This is the strategy's MEASURED history on these symbols — the only "
                            "honest way to talk about accuracy. Typical robust systems win 45–60% "
                            "and make money via risk:reward, not high win rates. Past results "
                            "never guarantee the future; no 99% system exists anywhere.")}


# ------------------------- Learning loop endpoints -------------------------- #

@router.post("/learn/evaluate", tags=["learning"],
             summary="Grade past signals against what price actually did (the learning step)")
def learn_evaluate(db: Session = Depends(get_db)):
    from app.engines import learning as learn_engine
    return learn_engine.evaluate_signals(db)


@router.get("/learn/track-record", tags=["learning"],
            summary="Honest scoreboard: hit rate of past calls by rating, plus lessons")
def learn_track_record(db: Session = Depends(get_db)):
    from app.engines import learning as learn_engine
    from app.services import auto_learn
    out = learn_engine.track_record(db)
    from app.engines import precision as _prec
    out["precision_curve"] = _prec.curve(db)
    out["precision_settings"] = _prec.get_settings()
    out["auto"] = {"enabled": auto_learn.state["started"],
                   "last_grade_at": auto_learn.state["last_grade"],
                   "last_result": auto_learn.state["last_grade_result"]}
    return out


# ------------------------------- Watchlist --------------------------------- #

@router.get("/watchlist", tags=["watchlist"])
def watchlist_get(db: Session = Depends(get_db)):
    from app.services import watch
    return {"items": watch.list_items(db)}


@router.post("/watchlist/refresh", tags=["watchlist"],
             summary="Re-analyze every watched symbol; alert on rating changes")
def watchlist_refresh(db: Session = Depends(get_db)):
    from app.services import watch
    return watch.refresh(db)


@router.post("/watchlist/{symbol}", tags=["watchlist"])
def watchlist_add(symbol: str, db: Session = Depends(get_db)):
    from app.services import watch
    return {"items": watch.add(db, symbol)}


@router.delete("/watchlist/{symbol}", tags=["watchlist"])
def watchlist_remove(symbol: str, db: Session = Depends(get_db)):
    from app.services import watch
    return {"items": watch.remove(db, symbol)}


@router.post("/settings/telegram", tags=["settings"],
             summary="Connect Telegram alerts (sends a test message to prove it works)")
def set_telegram(body: dict):
    from app.services import notify
    return notify.set_config((body or {}).get("bot_token"), (body or {}).get("chat_id"))



# ------------------------- Precision mode (accuracy target) ----------------- #

@router.get("/settings/precision", tags=["settings"],
            summary="Precision-mode settings + the currently measured gate")
def precision_get(db: Session = Depends(get_db)):
    from app.engines import precision
    cfg = precision.get_settings()
    return {**cfg, "gate": precision.recommendation(db, cfg["target"])}


@router.post("/settings/precision", tags=["settings"])
def precision_set(body: dict, db: Session = Depends(get_db)):
    from app.engines import precision
    cfg = precision.set_settings(bool((body or {}).get("enabled")),
                                 int((body or {}).get("target", 70)))
    return {**cfg, "gate": precision.recommendation(db, cfg["target"])}
