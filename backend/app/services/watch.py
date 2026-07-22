"""Watchlist: follow symbols; alert on rating changes."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import models
from app.engines.decision import analyze_symbol
from app.services import notify

log = logging.getLogger(__name__)


def _row(it: models.WatchItem) -> dict:
    return {"symbol": it.symbol, "last_rating": it.last_rating,
            "last_composite": it.last_composite, "last_price": it.last_price,
            "prev_rating": it.prev_rating,
            "changed_at": it.changed_at.isoformat() if it.changed_at else None,
            "updated_at": it.updated_at.isoformat() if it.updated_at else None}


def list_items(db) -> list[dict]:
    return [_row(i) for i in db.query(models.WatchItem)
            .order_by(models.WatchItem.created_at).all()]


def add(db, symbol: str) -> list[dict]:
    su = symbol.strip().upper()
    if su and not db.query(models.WatchItem).filter_by(symbol=su).first():
        db.add(models.WatchItem(symbol=su))
        db.commit()
    return list_items(db)


def remove(db, symbol: str) -> list[dict]:
    su = symbol.strip().upper()
    db.query(models.WatchItem).filter_by(symbol=su).delete()
    db.commit()
    return list_items(db)


def refresh(db, alert: bool = True, include_news: bool = False) -> dict:
    """Re-analyze every watched symbol; record + alert rating changes."""
    changes = []
    now = datetime.now(timezone.utc)
    for it in db.query(models.WatchItem).all():
        try:
            r = analyze_symbol(it.symbol, include_news=include_news)
        except Exception as exc:
            log.warning("watch refresh failed %s: %s", it.symbol, exc)
            continue
        new_rating = r.get("rating", r["action"])
        if it.last_rating and new_rating != it.last_rating:
            it.prev_rating, it.changed_at = it.last_rating, now
            changes.append({"symbol": it.symbol, "from": it.prev_rating,
                            "to": new_rating, "composite": r["composite_score"]})
            if alert:
                notify.send(f"⭐ {it.symbol}: {it.prev_rating} → {new_rating}"
                            f" (score {r['composite_score']}, conf {r['confidence']}%)")
        it.last_rating = new_rating
        it.last_composite = r["composite_score"]
        it.last_price = r["entry"]
        it.updated_at = now
    db.commit()
    return {"items": list_items(db), "changes": changes}
