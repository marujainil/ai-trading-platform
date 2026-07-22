# AI Trading Advisor — pick any stock/crypto → clear Buy/Sell/Hold, plus an autonomous paper-trader

A modular, production-grade **AI stock research + paper-trading platform** focused on
Indian markets (NSE via Yahoo Finance `.NS` symbols), with a FastAPI backend, a
multi-factor AI decision engine, a risk manager, a backtester, and a learning engine.

> **Important:** This is a research and paper-trading tool. Signals are **not investment
> advice** and **no returns are guaranteed**. Live broker execution is intentionally
> stubbed — see [Live trading](#live-trading-read-before-enabling).

---

## What's in Module 1

| Module | Status | Where |
|---|---|---|
| Market data layer (OHLCV, fundamentals, news; cached) | ✅ | `app/data/market_data.py` |
| Technical engine — EMA/SMA/VWAP, MACD, RSI, ADX/DI, ATR, Supertrend, Bollinger, Ichimoku, Fibonacci, volume profile, S/R, candlestick + structure patterns, trend & breakout detection | ✅ | `app/engines/technical.py`, `patterns.py` |
| Fundamental engine — P/E, ROE, D/E, margins, growth, FCF, promoter/institutional holdings, coverage-aware scoring | ✅ | `app/engines/fundamental.py` |
| News + social sentiment — LLM (optional Anthropic key) with keyword fallback; pluggable social providers | ✅ | `app/engines/sentiment.py` |
| AI decision engine — weighted composite → BUY/SELL/HOLD with confidence, risk score 1–10, entry/stop/T1/T2, RR, reasoning | ✅ | `app/engines/decision.py` |
| Risk manager — % risk per trade, daily-loss halt, exposure cap, position limits, volatility circuit-breaker, position sizing | ✅ | `app/engines/risk.py` |
| Paper trading + portfolio — cash, positions, MTM, sector allocation, PnL, equity snapshots, full audit log | ✅ | `app/services/portfolio.py` |
| Backtester — same scoring logic as live; win rate, PF, Sharpe, Sortino, max DD, CAGR, expectancy; costs in bps | ✅ | `app/engines/backtest.py` |
| Learning engine — buckets closed trades by regime/trend/RSI/ADX/confidence and surfaces "what works" | ✅ | `app/engines/learning.py` |
| Broker adapters — paper broker live; Zerodha adapter documented stub | ✅ | `app/brokers/` |
| REST API + Swagger UI | ✅ | `app/api/routes.py`, `/docs` |
| Tests (network-free, synthetic data) | ✅ 23 passing | `tests/` |
| Visual web dashboard — candlestick charts with 7 timeframes (5m/10m/15m/1H/1M/6M/1Y), EMAs + Supertrend + intraday VWAP + RSI on every timeframe, chart engine bundled locally (no CDN needed), signal cards, screener, portfolio, backtest UI | ✅ | `app/static/` |
| **Autopilot** — trades by itself: scans the watchlist on a schedule, buys the strongest signals, manages stops/targets/breakeven, exits on signal flips, 24h re-entry cooldown, full activity feed + optional Telegram alerts | ✅ | `app/services/autopilot.py` |

Open **http://localhost:8000** for the visual dashboard. The **Advisor tab** is the main screen: search or tap any stock/crypto and get a big colour-coded verdict (BUY/SELL/HOLD) with a plain-English summary, the trade plan, an interactive chart, the news, and the full reasoning. Developers can still use Swagger UI at `/docs`.

---

## How to run it (on your computer)

This platform runs as a program **on your own machine** (or a server you control) — not
inside a chat. You start it once in a terminal; it keeps running and serves a local
website (the API + Swagger UI) that you open in your browser.

**Prerequisite:** Python 3.11+ ([python.org/downloads](https://www.python.org/downloads/)).
On Windows, tick **"Add Python to PATH"** during install.

### Option A — simplest (no Docker, SQLite built in)

**Windows (PowerShell):**
```powershell
cd ai-trading-platform\backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

**macOS / Linux:**
```bash
cd ai-trading-platform/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open **http://localhost:8000** in your browser — you'll see the visual dashboard
(charts need internet, same as the market data). A `trading.db`
SQLite file is created automatically, and you start with **₹10,00,000 paper cash**.

Quick terminal demo (prints signals for a few NSE stocks):
```bash
python -m scripts.demo
python -m scripts.demo TATAMOTORS.NS ITC.NS
```

### Option B — Docker (PostgreSQL + Redis included)

```bash
cd ai-trading-platform
docker compose up --build
```
Same URL: http://localhost:8000

### Run the tests
```bash
cd backend && pytest
```

---

## Autopilot — the AI trades for you (paper money)

Open **http://localhost:8000** → the **🤖 Autopilot** tab → press **Start Autopilot**.
From then on it works alone: every 15 minutes (configurable) it researches every
symbol on the watchlist with the full engine, buys the best BUY signals that clear
your thresholds, sizes them through the Risk Manager, moves stops to breakeven at
Target 1, exits at stop-loss / Target 2 / bearish flips, and explains every action
in the activity feed. Press **Scan now** anytime to trigger a cycle instantly.

- **Runs 24×7 across three markets**: NSE/BSE (`.NS`/`.BO`) 09:15–15:30 IST, US
  stocks (`AAPL`, `MSFT`, ...) 09:30–16:00 New York time (≈7 PM–1:30 AM IST), and
  crypto (`BTC-USD`, `ETH-INR`, ...) round-the-clock. No software can trade a closed
  exchange, so overnight the AI researches Indian stocks and queues the best ones to
  buy automatically at the 09:15 open (🌙 events in the feed).
- **Transparent scans**: every scan logs each symbol's composite score and trend
  (`RELIANCE.NS 61↑, TCS.NS 58↔ …`) next to your active gate, so you always see
  exactly why it traded or held back.
- **Your thresholds are in charge**: the *Min composite* setting directly controls
  entries (with a bullish-trend confirmation) — it is not capped by the research
  engine's stricter default BUY label. AMC *stocks* work too (e.g. `HDFCAMC.NS`);
  mutual-fund NAVs aren't tradeable instruments here.
- **Phone alerts (optional)**: create a bot with Telegram's **@BotFather**, get your
  chat id from **@userinfobot**, put both in `backend/.env` (see `.env.example`),
  restart. Every buy/sell/stop-move then pings your phone.
- **Keep the terminal window open** — the Autopilot lives inside the running server.
- It trades **paper money**. Watch it for weeks, tune thresholds, and only think
  about real money after it has earned your trust — profits are never guaranteed.

## 5-minute tour (copy-paste `curl`, or click in /docs)

```bash
# 1) Full AI analysis of one stock (NSE symbols end in .NS)
curl "http://localhost:8000/api/analyze/RELIANCE.NS"

# 2) Scan a watchlist, ranked by composite score + sector strength
curl "http://localhost:8000/api/screener?symbols=RELIANCE.NS,TCS.NS,INFY.NS,HDFCBANK.NS"

# 3) Market regime (NIFTY health)
curl "http://localhost:8000/api/market/regime"

# 4) Backtest the strategy on 5 years of TCS
curl -X POST "http://localhost:8000/api/backtest" -H "Content-Type: application/json" \
     -d '{"symbol": "TCS.NS", "period": "5y"}'

# 5) Place a risk-checked paper BUY (quantity auto-sized by the Risk Manager)
curl -X POST "http://localhost:8000/api/orders" -H "Content-Type: application/json" \
     -d '{"symbol": "RELIANCE.NS", "side": "BUY"}'

# 6) Portfolio, trades, and risk limits
curl "http://localhost:8000/api/portfolio"
curl "http://localhost:8000/api/trades"
curl "http://localhost:8000/api/risk/limits"

# 7) Sell (close) and then see what the learning engine noticed
curl -X POST "http://localhost:8000/api/orders" -H "Content-Type: application/json" \
     -d '{"symbol": "RELIANCE.NS", "side": "SELL"}'
curl "http://localhost:8000/api/learning/insights"
```

Every analyze response includes: action, composite score, confidence %, risk score /10,
entry, stop-loss, Target 1, Target 2, risk-reward, and a human-readable reasoning list.

---

## Configuration (`backend/.env`, all optional)

Copy `backend/.env.example` → `backend/.env` and edit:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./trading.db` | Set to Postgres URL for production |
| `REDIS_URL` | *(empty → in-memory cache)* | Faster shared caching |
| `ANTHROPIC_API_KEY` | *(empty → keyword sentiment)* | Enables LLM news analysis |
| `STARTING_CASH` | `1000000` | Paper account size (INR) |
| `MARKET_INDEX_SYMBOL` | `^NSEI` | Regime index (NIFTY 50) |

Default risk limits (editable live via `PUT /api/risk/limits`): 1% risk per trade,
3% daily-loss halt, 60% max exposure, 10 open positions, volatility block above 6% ATR.

---

## Real market data with Groww (optional but recommended)

By default the platform uses free Yahoo Finance data (delayed, and it can't scan
thousands of symbols). Connect a **Groww API** token to unlock real NSE + BSE data
and the **"scan entire market"** mode:

1. Subscribe to the Groww trading API (₹499/month) at https://groww.in/trade-api and
   generate your access token.
2. **Easiest:** open the Auto-Trader tab → paste the token into the **🔑 Groww token** box →
   Connect. No `.env` edit, no restart. (It's saved for the day, so a restart won't lose it.)
   Alternatively put `GROWW_API_TOKEN=your_token` in `backend/.env` and restart.
3. The status shows **"Groww ✓"** and all ~7000 stocks become scannable.
4. In Autopilot settings, set **Scan universe → Entire market — all NSE + BSE**.

What this changes:
- Prices, live quotes (for paper fills) and history now come from Groww for `.NS`/`.BO`
  symbols (Yahoo remains the automatic fallback and still serves US stocks + crypto).
- **Entire-market mode** pulls Groww's full ~7000-symbol instrument list and scans it in
  rotating slices of 250 per cycle, so the whole market is covered over several scans —
  the AI genuinely considers every NSE + BSE stock, not just a watchlist. Crypto you add
  keeps trading 24×7 alongside it.
- Groww daily candles include full history, so EMA200 and long-term trend work correctly.

Notes: Groww access tokens **expire daily** — regenerate and update `.env` each trading
day (a limitation of their API). Groww intraday history is limited to ~3 months; daily is
full. Real **order execution** through Groww is supported by their API but stays **off**
in this build — the platform trades paper money using real Groww prices. Enabling live
orders is a deliberate, separate step that also requires following SEBI's algo-trading
rules; do it only after the paper results earn your trust.

## Crypto with Binance (no key needed — works out of the box)

Crypto data comes from Binance's free public API — **no account or key required**.
`BTC-USD`, `ETH-USD`, `SOL-USD` etc. work the moment you start the server, and
because crypto never closes, the Autopilot trades it **24×7**.

- In **entire-market** mode the AI also scans the **top ~150 crypto by volume**
  automatically (rotating slices per cycle), not just the ones you typed.
- `-USD` is treated as the USDT pair on Binance. `-INR` pairs aren't on Binance and
  fall back to Yahoo.
- The Autopilot tab shows **"Binance ✓ (crypto 24×7)"** when it's reachable.

So the two data sources together give you: **all NSE + BSE stocks via Groww**
(needs the ₹499/mo token) **+ all major crypto via Binance** (free) — scanned
continuously, bought and sold automatically, in one bot.

## Data notes & honest limitations

- **Yahoo Finance is free but delayed** (~15 min for NSE) and occasionally patchy.
  Perfect for research/paper trading; use a paid feed before any live use.
- **Backtests are technical-only.** Free sources don't provide *point-in-time*
  fundamentals or news history, so the backtester scores exactly what it can know
  historically — no look-ahead bias smuggled in. Fundamentals/news/sentiment still
  power **live** signals.
- Social sentiment providers (X/Reddit/Telegram/YouTube) are a plug-in interface
  (`app/engines/sentiment.py → SocialProvider`); they need your own API keys.
  Without them the social factor stays neutral and its weight is small by design.

## Live trading (read before enabling)

`app/brokers/zerodha.py` documents the exact Kite Connect steps but deliberately
returns `ok=False`. Before enabling real orders in India you must:
1. Get a Kite Connect developer subscription and generate a daily access token.
2. Comply with **SEBI's algorithmic-trading rules for retail** and your broker's
   API/algo approval process (Exchange-approved algos, order tagging, etc.).
3. Run the platform in paper mode long enough to trust its behaviour, then start
   with tiny position sizes.

## Roadmap

- **Module 3:** Real news/social providers, richer alerting
- **Module 4:** Options analytics (IV/OI), multi-broker adapters, strategy variants A/B

## Project structure

```
ai-trading-platform/
├─ docker-compose.yml
└─ backend/
   ├─ requirements.txt · Dockerfile · pytest.ini · .env.example
   ├─ app/
   │  ├─ static/           # dashboard.html + dashboard.js (the web UI)
   │  ├─ main.py            # FastAPI app + startup seed
   │  ├─ config.py          # env-driven settings
   │  ├─ database.py        # SQLAlchemy (SQLite default / Postgres)
   │  ├─ models.py          # accounts, positions, trades, signals, backtests…
   │  ├─ schemas.py         # request validation
   │  ├─ api/routes.py      # REST endpoints
   │  ├─ core/cache.py      # Redis-or-memory TTL cache
   │  ├─ data/market_data.py
   │  ├─ engines/           # technical · patterns · fundamental · sentiment
   │  │                     # decision · risk · backtest · learning
   │  ├─ brokers/           # base · paper · zerodha (stub)
   │  └─ services/portfolio.py
   ├─ scripts/demo.py
   └─ tests/                # 23 network-free tests
```
