"""Quick terminal demo (needs internet for Yahoo Finance).

    cd backend
    python -m scripts.demo            # default NSE symbols
    python -m scripts.demo AAPL MSFT  # any Yahoo symbols
"""
import sys

from app.engines.decision import analyze_symbol

SYMBOLS = sys.argv[1:] or ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS"]

print("=" * 78)
print("AI TRADING PLATFORM — research demo (paper mode, not investment advice)")
print("=" * 78)

for sym in SYMBOLS:
    try:
        r = analyze_symbol(sym)
    except Exception as exc:
        print(f"\n{sym}: ERROR — {exc}")
        continue

    print(f"\n{sym}  →  {r['action']}   "
          f"(composite {r['composite_score']}, confidence {r['confidence']}%, risk {r['risk_score']}/10)")
    print(f"   entry {r['entry']} | stop {r['stop_loss']} | T1 {r['target_1']} | "
          f"T2 {r['target_2']} | RR {r['risk_reward']}")
    for line in r["reasoning"][:7]:
        print(f"   • {line}")

print("\nDone. Full JSON via the API: GET /api/analyze/<symbol> (see http://localhost:8000/docs)")
