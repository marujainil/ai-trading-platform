"""Background housekeeping: the engine grades itself daily and re-checks the
watchlist every 30 minutes — no button-pressing required while the server runs."""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from app.config import settings

log = logging.getLogger(__name__)

WATCH_EVERY = 1800        # 30 min
GRADE_EVERY = 86400       # 24 h

state = {"last_grade": None, "last_grade_result": None,
         "last_watch": None, "started": False}


def run_grading() -> dict:
    from app.database import SessionLocal
    from app.engines import learning
    db = SessionLocal()
    try:
        res = learning.evaluate_signals(db)
        state["last_grade"] = datetime.now(timezone.utc).isoformat()
        state["last_grade_result"] = res
        if res.get("evaluated"):
            log.info("auto-learn: graded %s signals (%s right / %s wrong)",
                     res["evaluated"], res["wins"], res["losses"])
        return res
    finally:
        db.close()


def run_watch() -> dict:
    from app.database import SessionLocal
    from app.services import watch
    db = SessionLocal()
    try:
        res = watch.refresh(db, alert=True)
        state["last_watch"] = datetime.now(timezone.utc).isoformat()
        return res
    finally:
        db.close()


def _loop() -> None:
    time.sleep(60)                                # let the server settle first
    last_watch = last_grade = 0.0
    while True:
        now = time.time()
        try:
            if now - last_watch >= WATCH_EVERY:
                run_watch()
                last_watch = now
            if now - last_grade >= GRADE_EVERY:
                run_grading()
                last_grade = now
        except Exception as exc:
            log.warning("auto-learn loop error: %s", exc)
        time.sleep(60)


def start_background() -> bool:
    if state["started"] or not getattr(settings, "background_jobs_enabled", True):
        return False
    threading.Thread(target=_loop, daemon=True, name="auto-learn").start()
    state["started"] = True
    log.info("auto-learn background thread started (watch 30m, grade 24h)")
    return True
