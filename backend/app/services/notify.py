"""Telegram alerts. Configure in-app (📱 box) or via backend/.env
(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID). Unset → alerts simply skip."""
import json
import logging
from pathlib import Path

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_CFG_FILE = Path(__file__).resolve().parents[2] / ".telegram"
_RT = {"bot_token": None, "chat_id": None}


def _load_persisted() -> None:
    try:
        if _CFG_FILE.exists():
            d = json.loads(_CFG_FILE.read_text(encoding="utf-8"))
            _RT.update(bot_token=d.get("bot_token") or None, chat_id=d.get("chat_id") or None)
    except Exception:
        pass


def _creds() -> tuple[str | None, str | None]:
    return (_RT["bot_token"] or settings.telegram_bot_token,
            _RT["chat_id"] or settings.telegram_chat_id)


def is_configured() -> bool:
    token, chat = _creds()
    return bool(token and chat)


def send(text: str) -> tuple[bool, str | None]:
    token, chat = _creds()
    if not (token and chat):
        return False, "not configured"
    try:
        r = httpx.post(f"https://api.telegram.org/bot{token}/sendMessage",
                       json={"chat_id": chat, "text": text}, timeout=10)
        return (True, None) if r.status_code == 200 else (False, f"Telegram HTTP {r.status_code}")
    except Exception as exc:                      # never let alerts break the app
        log.warning("Telegram send failed: %s", exc)
        return False, str(exc)[:80]


def telegram_send(text: str) -> bool:             # legacy callers
    ok, _ = send(text)
    return ok


def set_config(bot_token: str | None, chat_id: str | None) -> dict:
    """Set/clear runtime Telegram config (persists like the Groww token) and prove
    it works by sending a test message."""
    _RT.update(bot_token=(bot_token or "").strip() or None,
               chat_id=(chat_id or "").strip() or None)
    try:
        if _RT["bot_token"] and _RT["chat_id"]:
            _CFG_FILE.write_text(json.dumps(_RT), encoding="utf-8")
        elif _CFG_FILE.exists():
            _CFG_FILE.unlink()
    except Exception:
        pass
    if not is_configured():
        return {"connected": False, "error": "cleared (both fields needed)"}
    ok, err = send("✅ AI Trading Advisor connected — STRONG BUY and watchlist alerts will arrive here.")
    return {"connected": ok, **({} if ok else {"error": err})}
