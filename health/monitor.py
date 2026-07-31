"""
health/monitor.py — Pipeline health checks for IndiaScreen

WHY THIS EXISTS (DRAWBACKS.md item: "No pipeline health monitoring"):
    If NSE or Yahoo silently changes a response format, renames a field, or
    starts blocking a data center IP range, the first symptom is *wrong
    numbers in a scan*, not an error — because every layer below this
    (fetch.py, live.py, providers.py) is written to degrade gracefully
    rather than crash. Graceful degradation is good for a single run; it's
    bad if nobody notices the degradation is happening on every run.

    This module runs a small, cheap synthetic check against each data
    source on a known-good, liquid symbol (RELIANCE) and records
    pass/fail + latency to a local SQLite table, so:
      - `HealthMonitor.status()` gives you a one-shot healthcheck you can
        wire into `/health` on the FastAPI backend or a Streamlit sidebar.
      - `HealthMonitor.history()` gives you a trend, so "yfinance has
        failed the last 40 checks in a row" is visible instead of buried
        in per-symbol debug logs.

Usage:
    from health.monitor import HealthMonitor
    hm = HealthMonitor()
    hm.check_all()          # run now, record results
    print(hm.status())      # {'yfinance': 'ok', 'nse': 'degraded', ...}
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB = Path.home() / ".indiascreen" / "health.db"
SENTINEL_SYMBOL = "RELIANCE"  # liquid, always-listed — a failure here is the source's fault


@dataclass
class CheckResult:
    provider: str
    ok: bool
    latency_ms: Optional[float]
    error: Optional[str]
    checked_at: datetime


class HealthMonitor:
    def __init__(self, db_path: Optional[Path] = None,
                 degraded_threshold: int = 3, window: int = 10):
        """
        degraded_threshold: consecutive failures within `window` most-recent
        checks before a provider is reported 'degraded' instead of a single
        transient 'fail'.
        """
        self.db_path = Path(db_path or DEFAULT_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.degraded_threshold = degraded_threshold
        self.window = window
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS checks (
                    provider TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    latency_ms REAL,
                    error TEXT,
                    checked_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_provider_time ON checks(provider, checked_at)")

    def _record(self, result: CheckResult) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO checks (provider, ok, latency_ms, error, checked_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (result.provider, int(result.ok), result.latency_ms,
                 result.error, result.checked_at.isoformat()),
            )

    def _check_yfinance(self) -> CheckResult:
        start = time.monotonic()
        try:
            import yfinance as yf
            df = yf.download(f"{SENTINEL_SYMBOL}.NS", period="5d",
                              progress=False, auto_adjust=True)
            ok = df is not None and not df.empty
            return CheckResult("yfinance", ok, (time.monotonic() - start) * 1000,
                                None if ok else "empty response", datetime.utcnow())
        except Exception as e:
            return CheckResult("yfinance", False, None, str(e), datetime.utcnow())

    def _check_nse(self) -> CheckResult:
        start = time.monotonic()
        try:
            from screener.live import get_live_quote
            q = get_live_quote(SENTINEL_SYMBOL)
            ok = "error" not in q and bool(q.get("ltp"))
            return CheckResult("nse", ok, (time.monotonic() - start) * 1000,
                                q.get("error"), datetime.utcnow())
        except Exception as e:
            return CheckResult("nse", False, None, str(e), datetime.utcnow())

    def check_all(self) -> list[CheckResult]:
        results = [self._check_yfinance(), self._check_nse()]
        for r in results:
            self._record(r)
        return results

    def _recent(self, provider: str) -> list[bool]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT ok FROM checks WHERE provider = ? "
                "ORDER BY checked_at DESC LIMIT ?",
                (provider, self.window),
            ).fetchall()
        return [bool(r[0]) for r in rows]

    def status(self) -> dict[str, str]:
        """Returns e.g. {'yfinance': 'ok', 'nse': 'degraded', 'options': 'unknown'}"""
        out = {}
        for provider in ("yfinance", "nse"):
            recent = self._recent(provider)
            if not recent:
                out[provider] = "unknown"
            elif not recent[0]:
                # most recent check failed — degraded if it's part of a streak
                streak = recent[: self.degraded_threshold]
                out[provider] = "degraded" if len(streak) >= self.degraded_threshold and not any(streak) else "flaky"
            else:
                out[provider] = "ok"
        return out

    def history(self, provider: str, hours: int = 24) -> list[CheckResult]:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT provider, ok, latency_ms, error, checked_at FROM checks "
                "WHERE provider = ? AND checked_at >= ? ORDER BY checked_at",
                (provider, cutoff),
            ).fetchall()
        return [
            CheckResult(r[0], bool(r[1]), r[2], r[3], datetime.fromisoformat(r[4]))
            for r in rows
        ]
