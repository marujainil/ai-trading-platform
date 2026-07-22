"""Market data provider (free tier: Yahoo Finance via yfinance).

Swappable by design — implement the same three functions against a paid feed
(e.g. TrueData, Global Datafeeds, Polygon) without touching the engines.

NSE symbols use the ".NS" suffix (RELIANCE.NS), BSE uses ".BO".
"""
import json
import logging
import time
from io import StringIO

import pandas as pd
import yfinance as yf

from app.config import settings
from app.core import cache

log = logging.getLogger(__name__)


class DataError(Exception):
    """Raised when market data cannot be retrieved for a symbol."""


DEFAULT_USDINR = 96.35   # emergency fallback only (user-confirmed level, Jul 2026); live rate is fetched first
_FX = {"rate": None, "ts": 0.0}


def get_usd_inr() -> float:
    """Live USD→INR rate. Order: in-process memo → cache → live fetch → constant
    fallback (₹83.5) so the platform can ALWAYS show rupees."""
    now = time.time()
    if _FX["rate"] and now - _FX["ts"] < 3600:
        return _FX["rate"]
    c = cache.get("fx:usdinr")
    if c:
        _FX.update(rate=float(c), ts=now)
        return _FX["rate"]
    rate = None
    for fx_ticker in ("USDINR=X", "INR=X"):     # two Yahoo aliases for the same pair
        try:
            fx_df = get_ohlcv(fx_ticker, period="3mo", min_bars=5)
            rate = float(fx_df["close"].iloc[-1])
            break
        except Exception as exc:
            log.warning("%s fetch failed (%s)", fx_ticker, exc)
    if not rate:
        log.warning("live USDINR unavailable; using fallback %.2f", DEFAULT_USDINR)
    if not rate or rate <= 0:
        rate = DEFAULT_USDINR
    cache.set("fx:usdinr", str(rate), ttl=3600)
    _FX.update(rate=rate, ts=now)
    return rate


def _groww_enabled() -> bool:
    try:
        from app.data import groww
        return groww.is_enabled()
    except Exception:
        return False


def resample_ohlcv(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    """Aggregate finer bars into coarser ones (e.g. 5m → 10m)."""
    out = df.resample(rule).agg({"open": "first", "high": "max", "low": "min",
                                 "close": "last", "volume": "sum"}).dropna()
    return out[out["volume"].notna()]


def get_ohlcv(symbol: str, period: str = "1y", interval: str = "1d",
              min_bars: int = 60) -> pd.DataFrame:
    """Return a DataFrame with columns: open, high, low, close, volume (DatetimeIndex).

    interval="10m" is not native to Yahoo — we fetch 5m and resample.
    """
    resample_to = None
    if interval == "10m":                       # Yahoo has no 10m; build it from 5m
        interval, resample_to = "5m", "10min"

    key = f"ohlcv:{symbol}:{period}:{interval}:{resample_to}"
    cached = cache.get(key)
    if cached:
        df = pd.read_json(StringIO(cached), orient="split")
        df.index = pd.to_datetime(df.index)
        return df

    df = None
    su = symbol.strip().upper()
    is_cry = ("-" in su) and not su.endswith((".NS", ".BO"))
    use_groww = _groww_enabled() and su.endswith((".NS", ".BO"))

    if is_cry:                                  # crypto → Binance (keyless), Yahoo fallback
        try:
            from app.data import binance
            bin_interval = "5m" if resample_to else interval
            df = binance.get_ohlcv(symbol, period=period, interval=bin_interval)
            if resample_to:
                df = resample_ohlcv(df, resample_to)
        except Exception as exc:
            log.debug("Binance OHLCV miss for %s (%s); trying Yahoo", symbol, exc)
            df = None

    if df is None and use_groww:
        try:
            from app.data import groww
            groww_interval = "5m" if resample_to else interval
            df = groww.get_ohlcv(symbol, period=period, interval=groww_interval)
            if resample_to:
                df = resample_ohlcv(df, resample_to)
        except Exception as exc:                # fall back to Yahoo on any Groww hiccup
            log.debug("Groww OHLCV miss for %s (%s); trying Yahoo", symbol, exc)
            df = None

    if df is None:
        try:
            df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=True)
        except Exception as exc:
            raise DataError(f"Failed to fetch OHLCV for {symbol}: {exc}") from exc
        if df is None or df.empty:
            raise DataError(f"No price data returned for '{symbol}'. Check the symbol (NSE needs '.NS').")
        df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna()
        if resample_to:
            df = resample_ohlcv(df, resample_to)

    if is_usd_asset(su):                         # USD-quoted (crypto/US) → convert to ₹ ONCE, here
        fx = get_usd_inr()
        df = df.copy()
        df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]] * fx

    if len(df) < min_bars:
        raise DataError(f"Only {len(df)} bars available for {symbol}; need at least {min_bars}. Try a longer period.")

    # Fresh data matters most for crypto (24×7) and intraday; cache those briefly.
    ttl = 120 if (is_cry or (resample_to or interval not in ("1d",))) else 900
    cache.set(key, df.to_json(orient="split", date_format="iso"), ttl=ttl)
    return df


def get_fundamentals(symbol: str) -> dict:
    """Subset of company fundamentals. Missing fields are simply absent (handled downstream)."""
    key = f"info:{symbol}"
    cached = cache.get(key)
    if cached:
        return json.loads(cached)

    wanted = [
        "longName", "sector", "industry", "marketCap", "trailingPE", "forwardPE",
        "priceToBook", "returnOnEquity", "debtToEquity", "profitMargins", "operatingMargins",
        "revenueGrowth", "earningsGrowth", "freeCashflow", "dividendYield", "beta",
        "heldPercentInsiders", "heldPercentInstitutions", "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
        "currentRatio", "quickRatio", "totalCashPerShare", "bookValue", "trailingEps",
        "earningsTimestamp", "earningsTimestampStart",
    ]
    out: dict = {}
    try:
        info = yf.Ticker(symbol).info or {}
        out = {k: info.get(k) for k in wanted if info.get(k) is not None}
    except Exception as exc:
        log.warning("Fundamentals unavailable for %s: %s", symbol, exc)

    cache.set(key, json.dumps(out), ttl=6 * 3600)
    return out


def get_news_headlines(symbol: str, limit: int = 10) -> list[str]:
    key = f"news:{symbol}"
    cached = cache.get(key)
    if cached:
        return json.loads(cached)[:limit]

    titles: list[str] = []
    try:
        for item in (yf.Ticker(symbol).news or [])[: limit * 2]:
            # yfinance has shipped two shapes over time; support both.
            title = item.get("title") or (item.get("content") or {}).get("title")
            if title:
                titles.append(title)
    except Exception as exc:
        log.warning("News unavailable for %s: %s", symbol, exc)

    try:
        from app.data import newsfeeds
        for extra in newsfeeds.headlines_for(symbol):
            if extra not in titles:
                titles.append(extra)
    except Exception as exc:
        log.debug("RSS merge failed for %s: %s", symbol, exc)

    titles = titles[:max(limit, 12)]
    cache.set(key, json.dumps(titles), ttl=1800)
    return titles


def get_market_regime() -> dict:
    """Broad-market trend from the configured index (default NIFTY 50).

    Returns {"score": 0-100, "label": str}. Neutral 50 when the index can't be fetched.
    """
    key = f"regime:{settings.market_index_symbol}"
    cached = cache.get(key)
    if cached:
        return json.loads(cached)

    result = {"score": 50.0, "label": "unknown", "index": settings.market_index_symbol}
    try:
        df = get_ohlcv(settings.market_index_symbol, period="2y")
        close = df["close"]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        ema200 = close.ewm(span=200, adjust=False).mean().iloc[-1]
        last = float(close.iloc[-1])
        ret_3m = float(close.iloc[-1] / close.iloc[-min(63, len(close) - 1)] - 1)

        score = 50.0
        score += 15 if last > ema200 else -15
        score += 10 if ema50 > ema200 else -10
        score += max(-15.0, min(15.0, ret_3m * 150))  # ±10% over 3m maps to ±15 pts
        score = round(max(0.0, min(100.0, score)), 1)

        label = ("strong_uptrend" if score >= 75 else "uptrend" if score >= 60
                 else "downtrend" if score <= 40 else "strong_downtrend" if score <= 25 else "sideways")
        # fix ordering of labels for low scores
        if score <= 25:
            label = "strong_downtrend"
        elif score <= 40:
            label = "downtrend"
        result = {"score": score, "label": label, "index": settings.market_index_symbol}
    except Exception as exc:
        log.warning("Market regime unavailable: %s", exc)

    cache.set(key, json.dumps(result), ttl=1800)
    return result


def last_price(symbol: str) -> float:
    su = symbol.strip().upper()
    is_cry = ("-" in su) and not su.endswith((".NS", ".BO"))
    if is_cry:
        try:
            from app.data import binance
            px = binance.last_price(symbol)
            return px * get_usd_inr() if su.endswith("-USD") else px
        except Exception as exc:
            log.debug("Binance LTP miss for %s (%s); using Yahoo close", symbol, exc)
    if _groww_enabled() and su.endswith((".NS", ".BO")):
        try:
            from app.data import groww
            return groww.last_price(symbol)
        except Exception as exc:
            log.debug("Groww LTP miss for %s (%s); using Yahoo close", symbol, exc)
    df = get_ohlcv(symbol, period="3mo", min_bars=5)   # already ₹ for USD assets
    return float(df["close"].iloc[-1])


# ------------------------------ INR conversion ------------------------------ #

def is_usd_asset(symbol: str) -> bool:
    su = symbol.strip().upper()
    if su.startswith("^") or "=X" in su:        # indices & FX tickers stay in native units
        return False
    return not su.endswith((".NS", ".BO"))


def usd_inr_rate() -> float:
    """Alias for get_usd_inr() — kept for callers/tests. Always returns a rate."""
    return get_usd_inr()


def to_inr_df(df: pd.DataFrame, rate: float) -> pd.DataFrame:
    out = df.copy()
    for col in ("open", "high", "low", "close"):
        out[col] = out[col] * rate
    return out


# ------------------------- sector context (India) --------------------------- #

# Yahoo sector name → NSE sector index. A stock is judged against its OWN sector,
# not just the NIFTY: strong-stock-in-weak-sector and weak-stock-in-strong-sector
# are different trades, and the broad index cannot tell them apart.
SECTOR_INDEX = {
    "Technology": "^CNXIT",
    "Communication Services": "^CNXIT",
    "Financial Services": "^NSEBANK",
    "Financial": "^NSEBANK",
    "Healthcare": "^CNXPHARMA",
    "Consumer Defensive": "^CNXFMCG",
    "Consumer Cyclical": "^CNXAUTO",
    "Basic Materials": "^CNXMETAL",
    "Energy": "^CNXENERGY",
    "Utilities": "^CNXENERGY",
    "Industrials": "^CNXINFRA",
    "Real Estate": "^CNXREALTY",
}


def sector_index_for(sector: str | None) -> str | None:
    return SECTOR_INDEX.get(sector) if sector else None


def earnings_days_away(fundamentals: dict) -> int | None:
    """Days until the next reported earnings date, or None if unknown/past."""
    import time as _t
    ts = fundamentals.get("earningsTimestampStart") or fundamentals.get("earningsTimestamp")
    if not ts:
        return None
    try:
        days = (float(ts) - _t.time()) / 86400.0
    except (TypeError, ValueError):
        return None
    if days < -1 or days > 120:
        return None
    return int(round(days))
