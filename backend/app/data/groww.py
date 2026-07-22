"""Groww API data provider — real NSE + BSE market data.

Enabled when GROWW_API_TOKEN is set in backend/.env. Provides:
  * the full tradable instrument universe (all NSE + BSE stocks),
  * historical daily/intraday candles (daily has full history — enough for EMA200),
  * live last-traded price.

Docs: https://groww.in/trade-api/docs/curl/historical-data
      https://groww.in/trade-api/docs/curl/live-data
      https://groww.in/trade-api/docs/curl/instruments

Design notes
------------
* We keep symbols in the platform's existing ".NS"/".BO" convention everywhere else;
  this module maps them to Groww's (exchange, trading_symbol) pair at the edge.
* Daily candles: interval_in_minutes=1440, up to ~3 years per request → full EMA200.
* The instrument master is a CSV Groww publishes; we cache it for a day.
* Every network call is defensive: on any failure we raise DataError so callers can
  fall back (e.g. to Yahoo) or skip the symbol without crashing the scan.
"""
from __future__ import annotations

import csv
import io
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from app.config import settings

log = logging.getLogger(__name__)

BASE = "https://api.groww.in"
INSTRUMENTS_CSV = "https://growwapi-assets.groww.in/instruments/instrument.csv"

_INSTRUMENTS: dict | None = None          # cache: trading_symbol -> row dict
_INSTR_TS = 0.0
_UNIVERSE_CACHE: dict = {}                 # exchange -> list[str] of ".NS"/".BO" symbols

# Token can come from .env (GROWW_API_TOKEN) or be pasted into the app at runtime.
# Groww tokens expire daily, so the in-app value is the easy path; we persist it to a
# small file so a same-day server restart doesn't require re-pasting.
_RUNTIME_TOKEN: str | None = None
_TOKEN_FILE = Path(__file__).resolve().parents[2] / ".groww_token"


class GrowwError(Exception):
    pass


def _load_persisted_token() -> None:
    global _RUNTIME_TOKEN
    try:
        if _TOKEN_FILE.exists():
            _RUNTIME_TOKEN = _TOKEN_FILE.read_text(encoding="utf-8").strip() or None
    except Exception:
        _RUNTIME_TOKEN = None


def _token() -> str | None:
    return _RUNTIME_TOKEN or settings.groww_api_token


def set_token(token: str | None) -> dict:
    """Set/clear the Groww token at runtime and validate by loading the instrument
    master. Returns {connected, instruments|error}. Never logs the token itself."""
    global _RUNTIME_TOKEN, _INSTRUMENTS, _INSTR_TS
    _RUNTIME_TOKEN = (token or "").strip() or None
    _INSTRUMENTS, _INSTR_TS = None, 0.0
    try:
        if _RUNTIME_TOKEN:
            _TOKEN_FILE.write_text(_RUNTIME_TOKEN, encoding="utf-8")
        elif _TOKEN_FILE.exists():
            _TOKEN_FILE.unlink()
    except Exception:
        pass
    if not _token():
        return {"connected": False, "error": "token cleared"}
    try:
        instruments = _load_instruments()
    except Exception as exc:
        return {"connected": False, "error": str(exc)}
    # The instrument CSV is public, so it loads even with a bad token. Prove the
    # token itself with an authenticated call before showing the green tick.
    try:
        px = last_price("RELIANCE.NS")
        return {"connected": True, "instruments": len(instruments),
                "probe": {"symbol": "RELIANCE.NS", "ltp": round(px, 2)}}
    except Exception as exc:
        msg = str(exc)
        if any(code in msg for code in ("401", "403", "Unauthorized", "unauthorized")):
            return {"connected": False,
                    "error": "Groww rejected the token (expired or wrong) — generate a fresh one"}
        return {"connected": True, "instruments": len(instruments),
                "warning": f"instruments loaded; live-quote check inconclusive ({msg[:80]})"}


def is_enabled() -> bool:
    return bool(_token())


def _headers() -> dict:
    return {"Accept": "application/json",
            "Authorization": f"Bearer {_token()}",
            "X-API-VERSION": "1.0"}


# --------------------------------------------------------------- symbol mapping

def split_symbol(symbol: str) -> tuple[str, str]:
    """'RELIANCE.NS' -> ('NSE', 'RELIANCE'); 'TCS.BO' -> ('BSE', 'TCS')."""
    su = symbol.strip().upper()
    if su.endswith(".NS"):
        return "NSE", su[:-3]
    if su.endswith(".BO"):
        return "BSE", su[:-3]
    return "NSE", su                        # bare symbol assumed NSE


def _suffix(exchange: str) -> str:
    return ".BO" if exchange.upper() == "BSE" else ".NS"


# ------------------------------------------------------------ instrument master

def _load_instruments() -> dict:
    global _INSTRUMENTS, _INSTR_TS
    if _INSTRUMENTS is not None and (time.time() - _INSTR_TS) < 86400:
        return _INSTRUMENTS
    try:
        r = httpx.get(INSTRUMENTS_CSV, timeout=30)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text))
        rows = {}
        for row in reader:
            key = (row.get("trading_symbol") or row.get("tradingsymbol") or "").strip().upper()
            if key:
                rows[(row.get("exchange", "").upper(), key)] = row
        _INSTRUMENTS = rows
        _INSTR_TS = time.time()
        log.info("Groww instrument master loaded: %d instruments", len(rows))
    except Exception as exc:
        raise GrowwError(f"Could not load Groww instrument master: {exc}") from exc
    return _INSTRUMENTS


def universe(exchange: str = "NSE", equity_only: bool = True) -> list[str]:
    """All tradable symbols on an exchange, in '.NS'/'.BO' form."""
    ex = exchange.upper()
    if ex in _UNIVERSE_CACHE and (time.time() - _INSTR_TS) < 86400:
        return _UNIVERSE_CACHE[ex]
    instruments = _load_instruments()
    suffix = _suffix(ex)
    out = []
    for (row_ex, tsym), row in instruments.items():
        if row_ex != ex:
            continue
        seg = (row.get("segment") or row.get("instrument_type") or "").upper()
        if equity_only and seg and seg not in ("CASH", "EQ", "EQUITY"):
            continue
        out.append(f"{tsym}{suffix}")
    _UNIVERSE_CACHE[ex] = sorted(set(out))
    return _UNIVERSE_CACHE[ex]


def full_universe(equity_only: bool = True) -> list[str]:
    """Every NSE + BSE tradable symbol (deduped, NSE preferred for dual-listed)."""
    nse = universe("NSE", equity_only)
    bse = universe("BSE", equity_only)
    nse_bases = {s[:-3] for s in nse}
    merged = nse + [s for s in bse if s[:-3] not in nse_bases]  # avoid dual-listing dupes
    return merged


# --------------------------------------------------------------- historical data

_INTERVAL_MIN = {"1d": 1440, "1h": 60, "15m": 15, "10m": 10, "5m": 5}
_PERIOD_DAYS = {"1mo": 30, "3mo": 92, "6mo": 183, "1y": 366, "2y": 732, "5y": 1826, "max": 1080}


def get_ohlcv(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    exchange, tsym = split_symbol(symbol)
    minutes = _INTERVAL_MIN.get(interval, 1440)
    days = _PERIOD_DAYS.get(period, 366)
    end = datetime.now()
    start = end - timedelta(days=days)

    params = {
        "exchange": exchange, "segment": "CASH", "trading_symbol": tsym,
        "start_time": start.strftime("%Y-%m-%d %H:%M:%S"),
        "end_time": end.strftime("%Y-%m-%d %H:%M:%S"),
        "interval_in_minutes": str(minutes),
    }
    try:
        r = httpx.get(f"{BASE}/v1/historical/candle/range", params=params,
                      headers=_headers(), timeout=30)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        raise GrowwError(f"Groww candles failed for {symbol}: {exc}") from exc

    candles = (data.get("payload") or {}).get("candles") or []
    if not candles:
        raise GrowwError(f"Groww returned no candles for {symbol}")

    df = pd.DataFrame(candles, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
    df = df.set_index("ts")[["open", "high", "low", "close", "volume"]].astype(float)
    return df


def last_price(symbol: str) -> float:
    exchange, tsym = split_symbol(symbol)
    try:
        r = httpx.get(f"{BASE}/v1/live-data/ltp", headers=_headers(), timeout=15,
                      params={"exchange": exchange, "segment": "CASH", "trading_symbol": tsym})
        r.raise_for_status()
        payload = r.json().get("payload") or {}
        # payload shape: {"NSE_RELIANCE": 1234.5} or {"last_price": ...}
        if "last_price" in payload:
            return float(payload["last_price"])
        for v in payload.values():
            if isinstance(v, (int, float)):
                return float(v)
    except Exception as exc:
        raise GrowwError(f"Groww LTP failed for {symbol}: {exc}") from exc
    raise GrowwError(f"Groww LTP: unexpected response for {symbol}")
