"""Tiny cache abstraction: uses Redis when REDIS_URL is set and reachable,
otherwise falls back to an in-process TTL dict. Values are strings (JSON)."""
import time
import logging

from app.config import settings

log = logging.getLogger(__name__)

_redis = None
_mem: dict[str, tuple[float, str]] = {}

if settings.redis_url:
    try:
        import redis as _redis_lib

        _redis = _redis_lib.Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
        _redis.ping()
        log.info("Cache: connected to Redis at %s", settings.redis_url)
    except Exception as exc:  # pragma: no cover - depends on environment
        log.warning("Cache: Redis unavailable (%s); using in-memory cache", exc)
        _redis = None


def get(key: str) -> str | None:
    if _redis is not None:
        try:
            return _redis.get(key)
        except Exception:
            pass
    item = _mem.get(key)
    if item and item[0] > time.time():
        return item[1]
    _mem.pop(key, None)
    return None


def set(key: str, value: str, ttl: int = 900) -> None:
    if _redis is not None:
        try:
            _redis.setex(key, ttl, value)
            return
        except Exception:
            pass
    _mem[key] = (time.time() + ttl, value)


def clear() -> None:
    _mem.clear()
    if _redis is not None:
        try:
            _redis.flushdb()
        except Exception:
            pass
