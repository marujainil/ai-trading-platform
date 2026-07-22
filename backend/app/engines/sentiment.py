"""News Intelligence + Social Sentiment.

News: pulls recent headlines and classifies them bullish / bearish / neutral.
  - With ANTHROPIC_API_KEY set → LLM classification (structured JSON).
  - Without it → transparent keyword heuristic (clearly labeled in output).

Social: adapter interface only in Module 1. Reddit/X/Telegram/YouTube collectors
need per-platform API credentials; wire them into `SocialProvider` and the
Decision Engine picks the score up automatically (it already consumes the slot).
"""
from __future__ import annotations

import json
import logging

from app.config import settings
from app.data.market_data import get_news_headlines

log = logging.getLogger(__name__)

_BULLISH = ["beats", "beat", "surge", "record", "upgrade", "upgraded", "buy", "profit rises",
            "strong", "growth", "wins", "order win", "expansion", "raises guidance", "dividend",
            "buyback", "outperform", "all-time high", "approval", "acquires"]
_BEARISH = ["misses", "miss", "falls", "plunge", "downgrade", "downgraded", "sell", "loss",
            "weak", "probe", "investigation", "fraud", "lawsuit", "recall", "cuts guidance",
            "layoffs", "default", "penalty", "resigns", "underperform", "downtime", "fine"]


def _keyword_sentiment(headlines: list[str]) -> dict:
    score = 0
    for h in headlines:
        hl = h.lower()
        score += sum(1 for w in _BULLISH if w in hl)
        score -= sum(1 for w in _BEARISH if w in hl)
    norm = max(-1.0, min(1.0, score / max(len(headlines), 1) / 2))
    label = "bullish" if norm > 0.15 else "bearish" if norm < -0.15 else "neutral"
    return {"score": round(norm, 2), "label": label, "method": "keyword_heuristic",
            "summary": f"Keyword scan of {len(headlines)} headlines"}


def _llm_sentiment(headlines: list[str], symbol: str) -> dict | None:
    """Classify with Claude via the Anthropic API. Returns None on any failure."""
    try:
        import anthropic

        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = (
            f"You are a sell-side news analyst. Recent headlines for {symbol}:\n"
            + "\n".join(f"- {h}" for h in headlines)
            + "\n\nRespond with ONLY a JSON object (no prose, no markdown fences):\n"
            '{"score": <float -1 to 1, bearish to bullish>, '
            '"label": "bullish"|"bearish"|"neutral", '
            '"summary": "<one sentence on the dominant driver>"}'
        )
        msg = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        data = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
        data["score"] = max(-1.0, min(1.0, float(data.get("score", 0))))
        data["method"] = f"llm:{settings.anthropic_model}"
        return data
    except Exception as exc:
        log.warning("LLM sentiment failed for %s (%s); falling back to keywords", symbol, exc)
        return None


def news_sentiment(symbol: str) -> dict:
    headlines = get_news_headlines(symbol)
    if not headlines:
        return {"score": 0.0, "label": "neutral", "method": "no_news",
                "summary": "No recent headlines found", "headlines": [], "score_0_100": 50.0}

    result = None
    if settings.anthropic_api_key:
        result = _llm_sentiment(headlines, symbol)
    if result is None:
        result = _keyword_sentiment(headlines)

    result["headlines"] = headlines[:5]
    result["score_0_100"] = round(50 + 50 * result["score"], 1)
    return result


class SocialProvider:
    """Adapter interface for social platforms. Implement `fetch_posts` per platform
    (praw for Reddit, X API v2, Telethon for Telegram, YouTube Data API) and register
    instances in `PROVIDERS`. `social_sentiment` aggregates whatever is registered."""

    name = "base"

    def fetch_posts(self, symbol: str, limit: int = 50) -> list[str]:  # pragma: no cover
        raise NotImplementedError


PROVIDERS: list[SocialProvider] = []  # register real providers here


def social_sentiment(symbol: str) -> dict:
    if not PROVIDERS:
        return {"score": 0.0, "label": "neutral", "method": "not_configured", "score_0_100": 50.0,
                "summary": "No social providers configured (Reddit/X/Telegram need API keys)"}
    posts: list[str] = []
    for p in PROVIDERS:
        try:
            posts.extend(p.fetch_posts(symbol))
        except Exception as exc:  # pragma: no cover
            log.warning("Social provider %s failed: %s", p.name, exc)
    result = _keyword_sentiment(posts) if posts else {"score": 0.0, "label": "neutral",
                                                      "method": "no_posts", "summary": "No posts found"}
    result["score_0_100"] = round(50 + 50 * result["score"], 1)
    return result
