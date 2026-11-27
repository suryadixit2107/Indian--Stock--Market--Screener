# Changelog

## Final build (this rework, combined)

Addresses every remaining item from the v3 code review. Full rationale in
`DRAWBACKS.md`.

**Multi-user architecture**
- New `backend/` — FastAPI app with JWT auth, per-user portfolio and
  watchlist storage via SQLAlchemy (SQLite for dev, Postgres for
  production via `DATABASE_URL`). Verified with a full register → login →
  per-user-isolation → CRUD test suite.
- `docker/docker-compose.yml` now runs `backend` + `postgres` alongside
  the existing `dashboard` service.

**Real-time streaming**
- New `/ws/quotes/{symbol}` WebSocket in `backend/app.py`: server polls
  upstream once per interval and fans results out to all subscribed
  clients, instead of every client polling NSE independently.

**Survivorship bias**
- New `screener/index_history.py`: point-in-time index constituent
  snapshots, historical CSV import, and a `SurvivorshipBiasWarning`-style
  check. `BacktestEngine` now accepts `index_name=` and attaches a
  `survivorship_bias_note` to `BacktestResult` whenever the backtest
  window predates recorded snapshot coverage — verified to fire correctly
  in both the "no snapshots" and "backtest predates earliest snapshot"
  cases.

**F&O cost modeling**
- New `backtest/fno_cost_model.py`: separate options/futures cost model
  with post-October-2024 STT rates, distinct from the equity-delivery
  `CostModel`.

**Data licensing**
- New `LICENSING_NOTES.md`: explicit personal-use-vs-redistribution
  distinction and a list of licensed vendor alternatives, wireable via the
  existing `Provider` interface.

**Pipeline health monitoring**
- New `health/monitor.py`: synthetic per-provider checks recorded to
  SQLite, `status()` reporting `ok`/`degraded`/`flaky`/`unknown`, exposed
  at `GET /health` on the backend.

**Silent capability downgrade**
- `ui/app.py` NL Screener tab now shows a visible warning banner when
  running in keyword-fallback mode (no `ANTHROPIC_API_KEY` set), instead
  of only logging it.

All additions were compiled and runtime-tested in this environment
(including a full FastAPI TestClient suite); live NSE/Yahoo network calls
could not be tested here — see "Honest limitations" in `DRAWBACKS.md`.

## v3.0

Full rationale in `DRAWBACKS.md`. Summary:

**Data reliability**
- Added `screener/providers.py`: multi-provider fallback chain
  (yfinance → cache), retry with exponential backoff + jitter, and a
  per-provider circuit breaker so a blocked/rate-limited source degrades
  gracefully instead of silently returning empty data.
- Added async, bounded-concurrency bulk fetching (`DataFeed.get_ohlcv_bulk`)
  to replace the sequential `for symbol: ...; sleep()` loop.
- `FetchResult` objects distinguish "fresh," "stale (served from cache),"
  and "failed with reason X" so callers/UI can show that distinction
  instead of presenting stale data as live.

**Compliance / regulatory posture**
- New `compliance/` module: relabels internal Buy/Sell-style signals to
  non-advisory language, appends a standard disclosure to every outbound
  message/export, and includes a full `DISCLAIMER.md`.
- Rewrote `alerts/telegram_bot.py`: default mode is pull (user-initiated
  `/scan`), not a scheduled broadcast to a subscriber list; broadcast mode
  still exists but is explicit opt-in with rate limiting per chat.

**Backtesting realism**
- `CostModel` gained `slippage_pct` (applied on both entry and exit fills)
  and a `circuit_limit_pct` field for future circuit-filter-aware fill
  rejection.

**Operability**
- Added `docker/Dockerfile` and `docker/docker-compose.yml`.
- Added `.env.example` and wired secret loading through it instead of
  ad hoc `export` instructions.

**Unchanged**
- Technical indicators, candlestick patterns, fundamentals scoring,
  sector rotation, options-chain analytics, and portfolio tracker are
  carried over from v0.2 with no functional changes — they weren't part
  of the drawback analysis.
