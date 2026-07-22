"""Binance public market-data provider — live crypto, no API key needed.

Binance's public REST endpoints (klines, ticker price) are free and keyless, so
crypto data works out of the box. Platform symbols stay in the "BTC-USD" style
and are mapped to Binance pairs ("BTCUSDT") at the edge; "-USD" maps to the USDT
pair. "-INR" pairs are not on Binance and fall back to Yahoo automatically.
"""
from __future__ import annotations

import logging
import time

import httpx
import pandas as pd

log = logging.getLogger(__name__)

BASE = "https://api.binance.com"

_PING = {"ok": None, "ts": 0.0}

_INTERVALS = {"5m": "5m", "15m": "15m", "1h": "1h", "1d": "1d", "1wk": "1w"}
_PERIOD_DAYS = {"1mo": 30, "3mo": 92, "6mo": 183, "1y": 366, "2y": 732, "5y": 1000, "max": 1000,
                "5d": 5, "1d": 1}
_BARS_PER_DAY = {"5m": 288, "15m": 96, "1h": 24, "1d": 1, "1wk": 1 / 7}


class BinanceError(Exception):
    pass


def map_symbol(symbol: str) -> str:
    """'BTC-USD' -> 'BTCUSDT'; 'ETH-USDT' -> 'ETHUSDT'; INR pairs unsupported."""
    su = symbol.strip().upper()
    if "-" not in su:
        raise BinanceError(f"{symbol}: not a crypto pair")
    base, quote = su.split("-", 1)
    if quote == "USD":
        quote = "USDT"
    if quote == "INR":
        raise BinanceError(f"{symbol}: Binance has no INR pairs (Yahoo fallback will be used)")
    return f"{base}{quote}"


def get_ohlcv(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    pair = map_symbol(symbol)
    iv = _INTERVALS.get(interval)
    if not iv:
        raise BinanceError(f"Unsupported interval {interval}")
    days = _PERIOD_DAYS.get(period, 366)
    limit = min(1000, max(10, days * _BARS_PER_DAY[iv]))
    try:
        r = httpx.get(f"{BASE}/api/v3/klines",
                      params={"symbol": pair, "interval": iv, "limit": limit}, timeout=20)
        r.raise_for_status()
        rows = r.json()
    except Exception as exc:
        raise BinanceError(f"Binance klines failed for {symbol}: {exc}") from exc
    if not rows:
        raise BinanceError(f"Binance returned no candles for {symbol}")

    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["ts", "open", "high", "low", "close", "volume"]
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").astype(float)
    return df[["open", "high", "low", "close", "volume"]]


def last_price(symbol: str) -> float:
    pair = map_symbol(symbol)
    try:
        r = httpx.get(f"{BASE}/api/v3/ticker/price", params={"symbol": pair}, timeout=10)
        r.raise_for_status()
        return float(r.json()["price"])
    except Exception as exc:
        raise BinanceError(f"Binance price failed for {symbol}: {exc}") from exc


def ping_ok() -> bool:
    """Cached 5-min connectivity check (used only for the status light)."""
    now = time.time()
    if _PING["ok"] is not None and now - _PING["ts"] < 300:
        return _PING["ok"]
    try:
        httpx.get(f"{BASE}/api/v3/ping", timeout=4).raise_for_status()
        _PING.update(ok=True, ts=now)
    except Exception:
        _PING.update(ok=False, ts=now)
    return _PING["ok"]


_UNIVERSE = {"syms": None, "ts": 0.0}

# leveraged tokens / stablecoin pairs aren't real directional trades — exclude them
_SKIP_SUFFIX = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
_STABLES = {"USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", "DAIUSDT", "EURUSDT", "AEURUSDT"}


def universe(top: int | None = None) -> list[str]:
    """EVERY tradable USDT spot pair on Binance, as platform '-USD' symbols,
    ranked by 24h volume (most liquid first). top=None → all of them.
    Cached for an hour. Returns [] on failure (crypto scan simply skipped)."""
    now = time.time()
    if _UNIVERSE["syms"] is not None and now - _UNIVERSE["ts"] < 3600:
        return _UNIVERSE["syms"] if top is None else _UNIVERSE["syms"][:top]
    try:
        info = httpx.get(f"{BASE}/api/v3/exchangeInfo", timeout=25).json()
        valid = {s["symbol"] for s in info.get("symbols", [])
                 if s.get("status") == "TRADING" and s.get("quoteAsset") == "USDT"
                 and s.get("isSpotTradingAllowed", True)}
        tickers = httpx.get(f"{BASE}/api/v3/ticker/24hr", timeout=30).json()
        ranked = sorted((t for t in tickers if t.get("symbol") in valid),
                        key=lambda t: float(t.get("quoteVolume", 0) or 0), reverse=True)
        syms = []
        for t in ranked:
            sym = t["symbol"]
            if sym.endswith(_SKIP_SUFFIX) or sym in _STABLES:
                continue
            syms.append(f"{sym[:-4]}-USD")          # strip 'USDT' → base, platform style
        _UNIVERSE.update(syms=syms, ts=now)
        log.info("Binance universe loaded: %d USDT pairs (all)", len(syms))
    except Exception as exc:
        log.warning("Binance universe failed: %s", exc)
        return _UNIVERSE["syms"] or []
    return syms if top is None else syms[:top]
