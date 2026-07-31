# 📈 IndiaScreen (final build) — NSE/BSE Stock Screener

This is the fully combined rework of the original IndiaScreen v0.2
codebase. All the analysis modules (technicals, fundamentals, candlestick
patterns, sector rotation, options-chain analytics) are unchanged. Every
drawback identified across the review is now addressed — see
**`/DRAWBACKS.md`** for the full analysis (including honest limitations)
and **`/CHANGELOG.md`** for what changed at each stage.

## What's in this build

| Area | What changed |
|---|---|
| Data fetching | `screener/providers.py` — fallback chain, retry/backoff, circuit breaker, async bulk fetch |
| Alerts/compliance | `compliance/` + rewritten `alerts/telegram_bot.py` — non-advisory language, disclosures, pull-based by default |
| Backtesting | Slippage modeling in `backtest/engine.py`; new `backtest/fno_cost_model.py` for options/futures |
| Survivorship bias | New `screener/index_history.py`; `BacktestEngine` now flags backtests with incomplete point-in-time coverage |
| Multi-user | New `backend/` — FastAPI + JWT auth + SQLAlchemy (SQLite or Postgres), per-user portfolios/watchlists |
| Real-time | New `/ws/quotes/{symbol}` WebSocket — server polls once, fans out to all clients |
| Health monitoring | New `health/monitor.py` — per-source ok/degraded/flaky status, exposed at `GET /health` |
| Data licensing | New `LICENSING_NOTES.md` — personal-use vs. redistribution, licensed vendor alternatives |
| Silent downgrades | NL Screener tab now shows a banner when running in keyword-fallback mode |
| Ops | `docker/docker-compose.yml` — dashboard + backend + Postgres; `.env.example` |

## ⚠️ Disclaimer

Educational/research tool. Not investment advice. Not a SEBI-registered
Investment Adviser or Research Analyst. Full disclaimer:
`compliance/DISCLAIMER.md`. Data licensing notes: `LICENSING_NOTES.md`.

## 🚀 Quick Start

### Local, single-user (Streamlit — same as before)
```bash
cp .env.example .env        # fill in ANTHROPIC_API_KEY / TELEGRAM_* if needed
pip install -r requirements.txt
streamlit run ui/app.py
```

### Multi-user backend (new)
```bash
export BACKEND_SECRET_KEY=$(openssl rand -hex 32)   # required, no insecure default
uvicorn backend.app:app --reload
# Register:  POST /auth/register  {"email": "...", "password": "..."}
# Login:     POST /auth/login     (form: username, password) -> JWT
# Portfolio: GET/POST/DELETE /portfolio   (Bearer token required)
# Watchlist: GET/POST /watchlist
# Live quotes: ws://localhost:8000/ws/quotes/RELIANCE
# Health:    GET /health
```

### Everything via Docker
```bash
docker compose -f docker/docker-compose.yml up
# dashboard: :8501   backend: :8000 (Postgres-backed)
```

### Python API — resilient data feed + survivorship-aware backtest
```python
from screener.providers import DataFeed
from screener.index_history import IndexHistory
from backtest.engine import BacktestEngine, CostModel

feed = DataFeed()
result = feed.get_ohlcv("RELIANCE")
print(result.source, result.is_stale, result.data.tail())

# Record today's constituents so future backtests have point-in-time coverage
ih = IndexHistory()
ih.record_snapshot("nifty50", ["RELIANCE", "TCS", "INFY"])   # do this regularly

engine = BacktestEngine(strategy, data, index_name="nifty50")
res = engine.run()
if res.survivorship_bias_note:
    print("⚠️", res.survivorship_bias_note)
```

## 🤖 Telegram bot (opt-in, pull-based by default)

```bash
export TELEGRAM_TOKEN=your_token
export TELEGRAM_CHAT_ID=your_chat_id
python -m alerts.telegram_bot --test
```

Read `compliance/DISCLAIMER.md` before wiring `broadcast_digest()` into a
scheduled job pointed at a subscriber list.

## 📦 Project Structure

```
indiascreen/
├── screener/
│   ├── providers.py       # resilient multi-provider data feed
│   ├── index_history.py   # point-in-time constituent snapshots
│   ├── fetch.py, live.py, technicals.py, fundamentals.py, patterns.py,
│   │   sectors.py, options.py, news.py, promoters.py, cache.py,
│   │   nl_screener.py, scanner.py
├── compliance/
│   ├── __init__.py         # signal relabeling + disclaimers
│   └── DISCLAIMER.md
├── backtest/
│   ├── engine.py            # slippage modeling + survivorship-bias check
│   ├── fno_cost_model.py     # options/futures cost model
│   └── strategies/
├── backend/                  # NEW — multi-user API
│   ├── app.py                 # FastAPI app, routes, WebSocket
│   ├── auth.py                 # JWT auth (bcrypt directly, not passlib)
│   └── db.py                    # SQLAlchemy models (SQLite/Postgres)
├── health/
│   └── monitor.py            # NEW — per-source health checks
├── alerts/telegram_bot.py      # pull-based, compliance-safe
├── portfolio/tracker.py         # original single-user tracker (unchanged)
├── ui/app.py                     # Streamlit dashboard + NL fallback banner
├── docker/                        # Dockerfile + docker-compose (+ Postgres)
├── .env.example
├── DRAWBACKS.md
├── CHANGELOG.md
└── LICENSING_NOTES.md
```

## 📊 Data Sources

| Source | Data | Reliability note |
|--------|------|-------------------|
| yfinance | OHLCV, P/E, market cap, ROE, dividends | Unofficial Yahoo scraper — wrapped with retry + fallback |
| NSE Bhavcopy | EOD delivery % | URL host has changed before; wrapped with retry |
| NSE Website API | Live quotes, option chain, FII/DII | Frequently blocks datacenter IPs; circuit-breaker protected, not eliminated |
| Google News RSS | Headline sentiment | Headline-only, no full-text |

No data source here is guaranteed real-time or error-free, and none are
licensed for redistribution beyond personal use — see `LICENSING_NOTES.md`.
Always verify against your broker/exchange before acting.

**License:** MIT
