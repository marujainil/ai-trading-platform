"""AI Research Engine — fundamental scoring.

Scores a company 0-100 from valuation, profitability, growth, leverage, and
ownership signals. Every check degrades gracefully when a field is missing
(free data sources are patchy) and the score is annotated with `coverage` so
the Decision Engine can discount low-information scores.

Promoter holding: yfinance's `heldPercentInsiders` is the closest free proxy
for Indian promoter holding; `heldPercentInstitutions` proxies FII/DII interest.
For filing-level data (shareholding patterns, insider-trade disclosures, quarterly
results) plug a provider into `app/data/` — the scoring interface stays the same.
"""
from __future__ import annotations


def _check(cond: bool | None, points: float, why_true: str, why_false: str | None = None):
    """Returns (delta, note, counted). cond=None → field missing, not counted."""
    if cond is None:
        return 0.0, None, False
    if cond:
        return points, why_true, True
    return -points * 0.6, why_false, True  # misses hurt a bit less than hits help


def fundamental_score(info: dict) -> dict:
    checks: list[tuple[float, str | None, bool]] = []

    pe = info.get("trailingPE")
    roe = info.get("returnOnEquity")
    d2e = info.get("debtToEquity")
    pm = info.get("profitMargins")
    rev_g = info.get("revenueGrowth")
    earn_g = info.get("earningsGrowth")
    fcf = info.get("freeCashflow")
    insiders = info.get("heldPercentInsiders")
    institutions = info.get("heldPercentInstitutions")
    pb = info.get("priceToBook")
    cr = info.get("currentRatio")

    checks.append(_check(None if pe is None else (0 < pe < 35), 6,
                         f"Reasonable valuation (P/E {pe:.1f})" if pe else "",
                         f"Rich/negative valuation (P/E {pe:.1f})" if pe else None))
    checks.append(_check(None if roe is None else roe > 0.15, 8,
                         f"Strong ROE ({roe:.0%})" if roe is not None else "",
                         f"Weak ROE ({roe:.0%})" if roe is not None else None))
    checks.append(_check(None if d2e is None else d2e < 100, 6,
                         f"Low leverage (D/E {d2e:.0f}%)" if d2e is not None else "",
                         f"High leverage (D/E {d2e:.0f}%)" if d2e is not None else None))
    checks.append(_check(None if pm is None else pm > 0.10, 6,
                         f"Healthy profit margin ({pm:.0%})" if pm is not None else "",
                         f"Thin margin ({pm:.0%})" if pm is not None else None))
    checks.append(_check(None if rev_g is None else rev_g > 0.10, 6,
                         f"Revenue growing ({rev_g:.0%} YoY)" if rev_g is not None else "",
                         f"Sluggish revenue growth ({rev_g:.0%})" if rev_g is not None else None))
    checks.append(_check(None if earn_g is None else earn_g > 0.10, 6,
                         f"Earnings growing ({earn_g:.0%})" if earn_g is not None else "",
                         f"Earnings under pressure ({earn_g:.0%})" if earn_g is not None else None))
    checks.append(_check(None if fcf is None else fcf > 0, 5,
                         "Positive free cash flow", "Negative free cash flow"))
    checks.append(_check(None if insiders is None else insiders > 0.30, 5,
                         f"High promoter/insider holding ({insiders:.0%})" if insiders is not None else "",
                         f"Low promoter/insider holding ({insiders:.0%})" if insiders is not None else None))
    checks.append(_check(None if institutions is None else institutions > 0.15, 4,
                         f"Meaningful institutional ownership ({institutions:.0%})" if institutions is not None else "",
                         None))
    checks.append(_check(None if pb is None else 0 < pb < 8, 3,
                         f"P/B in sane range ({pb:.1f})" if pb is not None else "", None))
    checks.append(_check(None if cr is None else cr > 1.2, 3,
                         f"Comfortable liquidity (current ratio {cr:.1f})" if cr is not None else "",
                         f"Tight liquidity (current ratio {cr:.1f})" if cr is not None else None))

    score = 50.0
    notes: list[str] = []
    counted = 0
    for delta, note, was_counted in checks:
        score += delta
        if note:
            notes.append(note)
        counted += int(was_counted)

    coverage = counted / len(checks)
    score = round(min(100.0, max(0.0, score)), 1)

    ratios = {
        "pe": pe, "forward_pe": info.get("forwardPE"), "pb": pb,
        "roe": roe, "debt_to_equity": d2e, "profit_margin": pm,
        "operating_margin": info.get("operatingMargins"),
        "revenue_growth": rev_g, "earnings_growth": earn_g,
        "dividend_yield": info.get("dividendYield"), "beta": info.get("beta"),
        "current_ratio": cr, "eps_ttm": info.get("trailingEps"),
        "market_cap": info.get("marketCap"),
        "promoter_or_insider_holding": insiders,
        "institutional_holding": institutions,
    }

    if coverage < 0.4:
        notes.append("Limited fundamental data available — score is low-confidence")

    return {
        "score": score,
        "coverage": round(coverage, 2),
        "notes": notes,
        "ratios": {k: v for k, v in ratios.items() if v is not None},
        "company": info.get("longName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "raw": {k: info.get(k) for k in ("earningsTimestamp", "earningsTimestampStart")
                if info.get(k) is not None},
    }
