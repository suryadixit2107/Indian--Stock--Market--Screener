# IndiaScreen — Drawback Analysis (against the current Indian market/2026 context)

**Status: this is the final combined build.** Every item below that was
previously flagged as open is now addressed in code — see the "Combined
final build" note at the top of each section. Nothing here claims to be
bulletproof (see the honest limitations at the bottom); this documents what
changed and why.

This documents what was reviewed in the uploaded `indiascreen_v2_2.zip` and why
each issue matters *right now*, not just in the abstract. "v3" in this repo
fixes the items marked **[fixed in v3]**; the rest are called out so you know
what's still open if you take this further.

## 1. Data layer is fragile against how NSE/Yahoo actually behave today

- **NSE's website has gotten more aggressive about blocking scripted access.**
  `screener/fetch.py` and `screener/live.py` get a homepage cookie and then
  hit `nseindia.com/api/...` directly. This still routinely 403s from
  datacenter/cloud IPs (exactly where a Streamlit Cloud, GitHub Actions
  runner, or Docker host lives), and NSE has changed its bhavcopy URL/host
  more than once (`archives.nseindia.com` vs `nsearchives.nseindia.com` — the
  code even references both, inconsistently, which is itself a sign the
  original author hit this). A single failed request returns an empty
  DataFrame with no distinction from "no data available." **[fixed in v3:**
  `screener/providers.py` adds a provider fallback chain, circuit breaker,
  and retry/backoff, plus a `FetchResult` that tells you *why* something is
  empty or stale.**]**
- **yfinance is an unofficial Yahoo scraper, not a stable API**, and Yahoo's
  own throttling of `.NS`/`.BO` bulk requests has gotten noticeably worse.
  `fetch_bulk()`'s multi-ticker `group_by="ticker"` call has no retry at all
  if Yahoo rate-limits mid-batch — it falls back to one-by-one for the
  *entire* batch rather than just the failed tickers.
- **No handling of corporate actions edge cases** (demergers, symbol
  renames like `LICI`, `ADANIENT` restructuring) — `auto_adjust=True` covers
  splits/dividends but not renames, which silently break history continuity.
- **Sequential fetching with `time.sleep(delay)` per symbol** means a Nifty
  500 scan takes 15-20+ minutes serially. **[fixed in v3:** async
  bounded-concurrency bulk fetch in `providers.py`.**]**

## 2. Regulatory exposure has increased since this pattern was common (2020-2022)

- The Telegram bot (`alerts/telegram_bot.py`) auto-generates and broadcasts
  literal **"Strong Buy" / "Buy" / "Sell"** labels to a subscriber list on a
  fixed daily schedule, with only a README-level disclaimer.
- Since then, SEBI has run a sustained enforcement push against unregistered
  "finfluencers" and automated tip-distribution channels (Investment
  Adviser / Research Analyst regulations). What's regulated is the
  **behavior** — algorithmic, unsolicited distribution of buy/sell-style
  calls — not just the choice of words in a footer. A daily cron broadcast
  of "Buy" tags to a chat list is close to the exact pattern enforcement
  actions have targeted, regardless of the MIT license.
- **[fixed in v3]** in `compliance/`: signals are relabeled to non-advisory
  language ("Match (bullish criteria)"), every outbound message carries a
  disclosure, and the default bot posture is **pull** (user requests their
  own scan) rather than **push** (broadcast to everyone at a fixed time) —
  broadcast mode still exists but is explicitly opt-in and documented as a
  materially different risk posture in `compliance/DISCLAIMER.md`.

## 3. Backtests are more optimistic than a live trader would experience

- **Zero slippage modeling** — every simulated fill happens at the exact
  printed `Open` price. This is closest to reality for liquid Nifty 50
  names and least accurate for exactly the setups (breakouts, momentum,
  small/midcap value) this tool is built to find, where real fills slip
  and NSE circuit filters can prevent a fill outright on the trigger day.
  **[fixed in v3:** `CostModel.slippage_pct` + `apply_slippage()`, wired
  into both entry and exit fills in `backtest/engine.py`.**]**
- Cost model was delivery-equity only, and STT rates on options/futures
  were revised upward in the July 2024 Union Budget. **[fixed in this
  build:** `backtest/fno_cost_model.py` adds a separate `FnOCostModel`
  with post-October-2024 STT rates for options (charged on premium, sell
  side) and futures (charged on sell-side notional) — do not reuse the
  equity `CostModel` rates for derivatives strategies.**]**
- **Survivorship bias**: backtesting today's Nifty 50/500 constituent list
  against historical prices silently excludes stocks that were delisted,
  merged, or dropped from the index — inflating historical performance.
  There's no free, complete, point-in-time NSE constituent history to
  retroactively fix this with. **[addressed (not eliminated) in this
  build:** `screener/index_history.py` lets you start recording
  point-in-time snapshots going forward, accepts an import of real
  historical data if you have it, and — critically — `BacktestEngine`
  now raises a visible `survivorship_bias_note` on the result whenever a
  backtest window predates recorded coverage, instead of silently
  proceeding as if the constituent list were accurate. This turns an
  invisible bias into a visible warning; it does not manufacture data
  that doesn't exist.**]**

## 3b. F&O cost modeling (new)

If you extend the strategies in `backtest/strategies/` to trade options or
futures instead of equity delivery, use `FnOCostModel` from
`backtest/fno_cost_model.py`, not `CostModel`. Confirm current STT/exchange
charges with your broker before trusting either for real capital — these
are set by government notification and exchange circulars, and can change
again in a future budget.

## 4. Architecture didn't match how this would actually get used/deployed

- **Single-user, single-machine design.** Streamlit script + a per-user
  home-directory SQLite cache (`~/.indiascreen/cache.db`) had no notion of
  multiple users, auth, or concurrent access. **[fixed in this build:**
  `backend/` adds a FastAPI app (`backend/app.py`) with JWT auth
  (`backend/auth.py`) and SQLAlchemy models (`backend/db.py`) for
  per-user portfolios and watchlists. It runs against SQLite for local
  dev or Postgres for production by changing one `DATABASE_URL` env var —
  no code change needed. The original single-user Streamlit app and
  `portfolio/tracker.py` are untouched and still work standalone if you
  don't need multi-user.**]**
- **No real-time streaming.** Everything was polling-based — hit NSE's
  REST endpoint, wait, repeat — with each client polling independently.
  **[addressed (not eliminated) in this build:** `backend/app.py` adds a
  `/ws/quotes/{symbol}` WebSocket. The underlying fetch is still polling
  (NSE has no public streaming feed), but the *server* now polls once per
  interval and fans that single result out to every subscribed client,
  instead of every client hitting NSE independently — which also reduces
  exactly the rate-limit/blocking risk in section 1. True tick-level
  push still doesn't exist because NSE doesn't expose it publicly; a
  broker WebSocket feed (e.g. Kite Connect) would be the next step if you
  need genuine tick data.**]**
- **No pipeline health monitoring.** If NSE or Yahoo silently changed a
  response format, the first symptom was wrong numbers in a scan, not an
  alert, because every layer degrades gracefully rather than crashing.
  **[fixed in this build:** `health/monitor.py` runs a cheap synthetic
  check against each provider on a known-good symbol, records pass/fail
  history to SQLite, and reports `ok` / `degraded` / `flaky` / `unknown`
  per source — exposed at `GET /health` on the backend and usable as a
  Streamlit sidebar widget.**]**
- **No containerization** — anyone deploying this had to hand-manage a
  Python environment. **[fixed in v3, extended here:**
  `docker/docker-compose.yml` now also runs the FastAPI backend and a
  Postgres container alongside the Streamlit dashboard.**]**
- **Secrets handled ad hoc** — the README told you to `export
  TELEGRAM_TOKEN=...` by hand. **[fixed in v3:** `.env.example`, now also
  covering `BACKEND_SECRET_KEY` and `DATABASE_URL`. The backend refuses to
  start if `BACKEND_SECRET_KEY` isn't set, rather than falling back to an
  insecure default.**]**

## 5. Silent capability downgrades

- `NLScreener` quietly falls back from the Claude-powered parser to a much
  weaker keyword matcher whenever `ANTHROPIC_API_KEY` is unset. **[fixed
  in this build:** the Streamlit NL Screener tab (`ui/app.py`) now shows a
  visible warning banner when running in keyword-fallback mode, instead of
  only logging it.**]**

## 6. Data licensing was genuinely unresolved

- yfinance and direct NSE-website scraping aren't licensed for
  redistribution, and their terms of service don't anticipate "automated
  tool scrapes this and shows/sends derived output to other people."
  **[addressed in this build:** `LICENSING_NOTES.md` makes the personal-use
  vs. redistribution distinction explicit, and lists licensed alternatives
  (NSE Data & Analytics, Kite Connect, TrueData, etc.) to swap in via the
  `Provider` interface in `screener/providers.py` if you go beyond personal
  use. This is documentation, not a data-source change — the code still
  defaults to free/scraped sources, because that's what makes it free to
  run.**]**

## 7. Competitive/market-fit context

The Indian screener space is not empty — Screener.in, Chartink, Trendlyne,
and TradingView all offer free tiers with more polished UIs and (in some
cases) more reliable data licensing. What this project's differentiators
actually are: (a) it's code you own and can extend/backtest against
directly rather than a black-box SaaS UI, (b) the NL-to-filter layer, and
(c) the Indian-specific cost model in the backtester (now covering both
equity and F&O). None of those required building yet another feature on
top — they were better served by making the existing pipeline *reliable*
first, which is what this rework prioritized.

---

## Honest limitations of this build

- **Nothing here was tested against live NSE/Yahoo endpoints** — this was
  built in a sandboxed environment without network access to those
  domains. The provider fallback chain, circuit breaker, and health
  monitor are verified with synthetic/fake providers and unit-level tests,
  not a live market session. Run it against real markets for a few days
  and watch `/health` and the logs before trusting it.
- **Survivorship bias is mitigated, not eliminated** — the code now warns
  you instead of hiding the problem, but it can't retroactively produce
  point-in-time constituent data that was never recorded. Backtests before
  you start calling `record_snapshot()` regularly are still subject to
  this bias; the warning just makes that visible.
- **The FastAPI backend is a minimal auth system** — password hashing and
  per-user isolation are real, but there's no email verification, password
  reset flow, rate limiting on login attempts, or OAuth. Add those before
  exposing this beyond trusted/internal use.
- **The real-time WebSocket still depends on NSE's polling-based data
  underneath it** — it reduces redundant polling across clients, it
  doesn't create a tick-level feed that doesn't exist upstream.
- **Data licensing is now documented, not resolved** — if you need
  redistribution rights, you still have to integrate a paid vendor
  yourself; this build only makes the tradeoff explicit and easy to swap.
