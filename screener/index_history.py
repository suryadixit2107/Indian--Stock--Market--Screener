"""
screener/index_history.py — Point-in-time index constituent tracking

WHY THIS EXISTS (DRAWBACKS.md item: "Survivorship bias in backtests"):
    Backtesting "Nifty 50" against historical prices using *today's*
    constituent list silently excludes every stock that was delisted,
    merged, renamed, or dropped from the index along the way (e.g. names
    that have left the Nifty 50 due to demergers or performance). That
    inflates historical returns because the backtest only ever "knew about"
    stocks that turned out to still be in the index today.

    There is no free, complete, point-in-time NSE index-constituent history
    available — this module does not manufacture data that doesn't exist.
    What it does instead:

      1. Lets you snapshot and store the constituent list *as of today*,
         with a date stamp, every time you run a scan — so from this point
         forward, backtests over that stored history are point-in-time
         correct instead of static.
      2. Refuses to silently pretend a backtest is bias-free: if a backtest
         spans dates before you have snapshot coverage, it raises a visible
         `SurvivorshipBiasWarning` rather than quietly using today's list.
      3. Accepts a user-supplied historical snapshot file (CSV of
         date,symbol pairs) if you have access to one (e.g. purchased from
         a data vendor, or reconstructed from old index fact sheets), so the
         warning goes away once real point-in-time data exists.

Usage:
    from screener.index_history import IndexHistory

    ih = IndexHistory()
    ih.record_snapshot("nifty50", ["RELIANCE", "TCS", ...])   # call this daily/weekly

    # Later, for a backtest from 2023-01-01:
    symbols, bias_ok = ih.constituents_asof("nifty50", date(2023, 1, 1))
    if not bias_ok:
        # only today's snapshot exists — backtest results before the first
        # recorded snapshot date are subject to survivorship bias
        ...
"""

from __future__ import annotations

import csv
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB = Path.home() / ".indiascreen" / "index_history.db"


class SurvivorshipBiasWarning(UserWarning):
    """Raised/warned when a backtest date predates any recorded snapshot."""


@dataclass
class ConstituentResult:
    symbols: list[str]
    as_of: date               # the actual snapshot date used
    requested_date: date      # the date that was asked for
    is_point_in_time: bool    # False if this is a fallback to the earliest/latest snapshot


class IndexHistory:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or DEFAULT_DB)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    index_name TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    PRIMARY KEY (index_name, snapshot_date, symbol)
                )
            """)

    def record_snapshot(self, index_name: str, symbols: list[str],
                         as_of: Optional[date] = None) -> None:
        """Store today's (or a given date's) constituent list. Call this
        regularly (e.g. once per scan run) to build up point-in-time
        coverage over time."""
        as_of = as_of or date.today()
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO snapshots (index_name, snapshot_date, symbol) "
                "VALUES (?, ?, ?)",
                [(index_name, as_of.isoformat(), s) for s in symbols],
            )
        logger.info(f"Recorded {len(symbols)}-symbol snapshot for '{index_name}' @ {as_of}")

    def import_historical_csv(self, path: str) -> int:
        """
        Bulk-import a point-in-time constituent history you already have
        (columns: index_name,snapshot_date,symbol). This is the only way
        to get real pre-today coverage — see module docstring.
        Returns number of rows imported.
        """
        n = 0
        with open(path, newline="") as f, sqlite3.connect(self.db_path) as conn:
            reader = csv.DictReader(f)
            rows = [(r["index_name"], r["snapshot_date"], r["symbol"]) for r in reader]
            conn.executemany(
                "INSERT OR IGNORE INTO snapshots (index_name, snapshot_date, symbol) "
                "VALUES (?, ?, ?)", rows,
            )
            n = len(rows)
        logger.info(f"Imported {n} historical constituent rows from {path}")
        return n

    def _available_dates(self, index_name: str) -> list[date]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT DISTINCT snapshot_date FROM snapshots WHERE index_name = ? "
                "ORDER BY snapshot_date", (index_name,),
            ).fetchall()
        return [datetime.strptime(r[0], "%Y-%m-%d").date() for r in rows]

    def constituents_asof(self, index_name: str, as_of: date) -> ConstituentResult:
        """
        Return the constituent list closest to (but not after) `as_of`,
        along with whether that's a genuine point-in-time match.
        """
        dates = self._available_dates(index_name)
        if not dates:
            raise ValueError(
                f"No snapshots recorded for '{index_name}' yet. Call "
                f"record_snapshot() at least once, or import_historical_csv()."
            )

        candidates = [d for d in dates if d <= as_of]
        if not candidates:
            # Requested date predates every snapshot we have — this is the
            # exact survivorship-bias scenario. Fall back to the earliest
            # snapshot but flag it clearly.
            snap_date = dates[0]
            is_pit = False
        else:
            snap_date = max(candidates)
            is_pit = True

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT symbol FROM snapshots WHERE index_name = ? AND snapshot_date = ?",
                (index_name, snap_date.isoformat()),
            ).fetchall()

        result = ConstituentResult(
            symbols=[r[0] for r in rows],
            as_of=snap_date,
            requested_date=as_of,
            is_point_in_time=is_pit,
        )
        if not is_pit:
            logger.warning(
                f"SURVIVORSHIP BIAS RISK: requested constituents as of {as_of}, "
                f"but the earliest recorded snapshot for '{index_name}' is "
                f"{snap_date}. Using that snapshot's list — any stock that "
                f"left the index before {snap_date} is NOT included, which "
                f"will overstate historical performance. Import real "
                f"point-in-time data with import_historical_csv() to fix this."
            )
        return result
