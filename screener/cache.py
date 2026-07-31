"""
screener/cache.py — SQLite caching layer for IndiaScreen

Caches daily OHLCV data and scan results so repeat runs are instant.
Builds a historical signal database you can mine later.

Usage:
    from screener.cache import Cache

    cache = Cache()
    cache.save_ohlcv("RELIANCE", df)
    df = cache.load_ohlcv("RELIANCE")         # instant on second run

    cache.save_signals(results_df)
    history = cache.signal_history("RELIANCE") # past signals for this stock
"""

import sqlite3
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_DB = Path.home() / ".indiascreen" / "cache.db"


class Cache:
    """
    SQLite-backed cache for OHLCV data and screener signals.

    Args:
        db_path:    Path to SQLite database file (default: ~/.indiascreen/cache.db)
        ohlcv_ttl:  Days before OHLCV data is considered stale (default: 1)
        signals_ttl:Days before today's signals are re-run (default: 1)
    """

    def __init__(
        self,
        db_path:     Optional[Path] = None,
        ohlcv_ttl:   int = 1,
        signals_ttl: int = 1,
    ):
        self.db_path     = Path(db_path or DEFAULT_DB)
        self.ohlcv_ttl   = ohlcv_ttl
        self.signals_ttl = signals_ttl
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self):
        """Create tables if they don't exist."""
        with self._conn() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS ohlcv (
                    symbol      TEXT NOT NULL,
                    date        TEXT NOT NULL,
                    open        REAL, high  REAL, low   REAL,
                    close       REAL, volume REAL,
                    cached_at   TEXT NOT NULL,
                    PRIMARY KEY (symbol, date)
                );

                CREATE TABLE IF NOT EXISTS signals (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_date    TEXT NOT NULL,
                    symbol       TEXT NOT NULL,
                    company_name TEXT,
                    sector       TEXT,
                    price        REAL,
                    change_pct   REAL,
                    rsi_14       REAL,
                    pe_ratio     REAL,
                    market_cap_cr REAL,
                    delivery_pct  REAL,
                    fund_score   REAL,
                    signal       TEXT,
                    above_200dma INTEGER,
                    macd_cross   INTEGER
                );

                CREATE TABLE IF NOT EXISTS meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_signals_date   ON signals(scan_date);
                CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals(symbol);
                CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol   ON ohlcv(symbol);
            """)
        logger.debug(f"Cache DB initialised at {self.db_path}")

    # ── OHLCV cache ───────────────────────────────────────────────────────────

    def save_ohlcv(self, symbol: str, df: pd.DataFrame) -> None:
        """Save OHLCV DataFrame to cache."""
        if df.empty:
            return
        cached_at = datetime.now().isoformat()
        rows = []
        for dt, row in df.iterrows():
            rows.append((
                symbol.upper(),
                str(dt.date()) if hasattr(dt, "date") else str(dt),
                float(row.get("Open",   0) or 0),
                float(row.get("High",   0) or 0),
                float(row.get("Low",    0) or 0),
                float(row.get("Close",  0) or 0),
                float(row.get("Volume", 0) or 0),
                cached_at,
            ))
        with self._conn() as con:
            con.executemany(
                "INSERT OR REPLACE INTO ohlcv VALUES (?,?,?,?,?,?,?,?)", rows
            )
        logger.debug(f"OHLCV cached: {symbol} ({len(rows)} rows)")

    def load_ohlcv(self, symbol: str) -> Optional[pd.DataFrame]:
        """
        Load cached OHLCV for a symbol.
        Returns None if cache is stale or empty.
        """
        cutoff = (date.today() - timedelta(days=self.ohlcv_ttl)).isoformat()
        with self._conn() as con:
            df = pd.read_sql(
                "SELECT date, open, high, low, close, volume, cached_at "
                "FROM ohlcv WHERE symbol=? ORDER BY date",
                con, params=(symbol.upper(),),
            )
        if df.empty:
            return None

        # Check freshness — latest cached_at must be today
        latest_cache = df["cached_at"].max()[:10]
        if latest_cache < str(date.today()):
            logger.debug(f"OHLCV cache stale for {symbol} (last: {latest_cache})")
            return None

        df.index = pd.to_datetime(df["date"])
        df = df[["open", "high", "low", "close", "volume"]].copy()
        df.columns = ["Open", "High", "Low", "Close", "Volume"]
        return df

    def save_ohlcv_bulk(self, data: dict[str, pd.DataFrame]) -> None:
        """Save multiple symbols' OHLCV in one transaction."""
        for symbol, df in data.items():
            self.save_ohlcv(symbol, df)

    def load_ohlcv_bulk(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        """Load cached OHLCV for multiple symbols. Returns only non-stale entries."""
        result = {}
        for sym in symbols:
            df = self.load_ohlcv(sym)
            if df is not None:
                result[sym] = df
        return result

    # ── Signal history ────────────────────────────────────────────────────────

    def save_signals(self, df: pd.DataFrame) -> None:
        """
        Save today's screener results to the signals table.
        This builds a historical database of all signals over time.
        """
        if df.empty:
            return
        scan_date = str(date.today())
        rows = []
        for _, row in df.iterrows():
            rows.append((
                scan_date,
                str(row.get("symbol", "")),
                str(row.get("company_name", "")),
                str(row.get("sector", "")),
                float(row.get("price", 0) or 0),
                float(row.get("change_pct", 0) or 0),
                float(row.get("rsi_14", 0) or 0),
                float(row.get("pe_ratio", 0) or 0),
                float(row.get("market_cap_cr", 0) or 0),
                float(row.get("delivery_pct", 0) or 0),
                float(row.get("fund_score", 0) or 0),
                str(row.get("signal", "")),
                int(bool(row.get("above_200dma", False))),
                int(row.get("macd_cross", 0) or 0),
            ))
        with self._conn() as con:
            con.executemany(
                "INSERT INTO signals "
                "(scan_date, symbol, company_name, sector, price, change_pct, "
                "rsi_14, pe_ratio, market_cap_cr, delivery_pct, fund_score, "
                "signal, above_200dma, macd_cross) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
        logger.info(f"Saved {len(rows)} signals for {scan_date}")

    def load_today_signals(self) -> pd.DataFrame:
        """Load today's cached signals (if already run today)."""
        with self._conn() as con:
            df = pd.read_sql(
                "SELECT * FROM signals WHERE scan_date=? ORDER BY fund_score DESC",
                con, params=(str(date.today()),),
            )
        return df

    def signal_history(self, symbol: str, days: int = 90) -> pd.DataFrame:
        """
        Fetch historical signals for a specific symbol.
        Useful for analysing how a stock's signals have changed over time.
        """
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with self._conn() as con:
            df = pd.read_sql(
                "SELECT scan_date, price, rsi_14, pe_ratio, delivery_pct, fund_score, signal "
                "FROM signals WHERE symbol=? AND scan_date>=? ORDER BY scan_date",
                con, params=(symbol.upper(), cutoff),
            )
        return df

    def signal_stats(self, days: int = 30) -> pd.DataFrame:
        """
        Aggregate signal statistics — how often each stock appears as Buy/Sell.
        Useful for finding consistently strong/weak stocks.
        """
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        with self._conn() as con:
            df = pd.read_sql("""
                SELECT symbol, company_name, sector,
                       COUNT(*) as appearances,
                       SUM(CASE WHEN signal IN ('Buy','Strong Buy') THEN 1 ELSE 0 END) as buy_count,
                       SUM(CASE WHEN signal IN ('Sell','Strong Sell') THEN 1 ELSE 0 END) as sell_count,
                       AVG(rsi_14) as avg_rsi,
                       AVG(fund_score) as avg_fund_score,
                       MAX(price) as high_price,
                       MIN(price) as low_price
                FROM signals WHERE scan_date >= ?
                GROUP BY symbol ORDER BY buy_count DESC
            """, con, params=(cutoff,))
        return df

    def clear_old_ohlcv(self, keep_days: int = 7) -> int:
        """Delete OHLCV cache entries older than keep_days. Returns rows deleted."""
        cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
        with self._conn() as con:
            cur = con.execute("DELETE FROM ohlcv WHERE cached_at < ?", (cutoff,))
        logger.info(f"Cleared {cur.rowcount} old OHLCV rows")
        return cur.rowcount

    def stats(self) -> dict:
        """Return database statistics."""
        with self._conn() as con:
            ohlcv_count   = con.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
            signal_count  = con.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
            signal_days   = con.execute("SELECT COUNT(DISTINCT scan_date) FROM signals").fetchone()[0]
            symbols_count = con.execute("SELECT COUNT(DISTINCT symbol) FROM signals").fetchone()[0]
        return {
            "db_path":       str(self.db_path),
            "ohlcv_rows":    ohlcv_count,
            "signal_rows":   signal_count,
            "signal_days":   signal_days,
            "tracked_stocks":symbols_count,
            "db_size_mb":    round(self.db_path.stat().st_size / 1e6, 2) if self.db_path.exists() else 0,
        }
