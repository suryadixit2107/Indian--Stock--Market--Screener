"""
screener/providers.py — Resilient, multi-provider data layer for IndiaScreen v3

WHY THIS EXISTS (see /DRAWBACKS.md for the full analysis):
    v0.2 hit every symbol sequentially, against a single source, with a bare
    `except Exception` and no retry/backoff. In practice:
      - NSE's website (nseindia.com) now sits behind bot-detection that
        frequently 403s server/datacenter IPs and rotates cookie requirements
        without notice — a single failed request silently returned an empty
        DataFrame with no distinction between "no data" and "blocked".
      - yfinance (unofficial, scrapes Yahoo) regularly breaks for `.NS`/`.BO`
        tickers after renames, delistings, or Yahoo-side schema changes, and
        is itself increasingly rate-limited.
      - There was no fallback: if the one source failed, the caller got an
        empty frame and the screener silently dropped the stock.

WHAT'S DIFFERENT HERE:
    - A `Provider` interface with a priority-ordered fallback chain
      (NSE -> yfinance -> local cache), so a single source going down
      degrades the result quality instead of breaking the run.
    - Exponential backoff + jitter on retryable errors (429/5xx/timeouts),
      and a simple per-provider circuit breaker so a dead provider is
      skipped for a cooldown window instead of being retried on every symbol.
    - Async, bounded-concurrency bulk fetch (replaces the old sequential
      `for sym in symbols: ...; time.sleep(delay)` loop) — for 500 symbols
      this is the difference between ~3 minutes and ~15-20 minutes.
    - Explicit `FetchResult(data, source, is_stale, error)` objects so
      calling code (and the UI) can show "stale cache data — live fetch
      failed" instead of quietly presenting wrong numbers as if fresh.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── Result wrapper ──────────────────────────────────────────────────────────

@dataclass
class FetchResult:
    symbol: str
    data: Any                      # DataFrame, dict, or None
    source: Optional[str] = None   # which provider actually served this
    is_stale: bool = False         # True if served from cache after live failure
    error: Optional[str] = None    # last error message, if any provider failed
    fetched_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def ok(self) -> bool:
        if self.data is None:
            return False
        if isinstance(self.data, pd.DataFrame):
            return not self.data.empty
        if isinstance(self.data, dict):
            return len(self.data) > 0
        return True


# ── Circuit breaker ──────────────────────────────────────────────────────────

class CircuitBreaker:
    """
    Per-provider circuit breaker. After `fail_threshold` consecutive failures,
    the provider is considered "open" (skipped) for `cooldown_seconds`.

    This matters specifically for nseindia.com: once it starts 403-ing, every
    subsequent call in the same run is a wasted timeout unless we back off.
    """

    def __init__(self, fail_threshold: int = 4, cooldown_seconds: int = 120):
        self.fail_threshold = fail_threshold
        self.cooldown_seconds = cooldown_seconds
        self._fail_counts: dict[str, int] = {}
        self._opened_at: dict[str, float] = {}

    def is_open(self, provider_name: str) -> bool:
        opened = self._opened_at.get(provider_name)
        if opened is None:
            return False
        if time.monotonic() - opened > self.cooldown_seconds:
            # cooldown elapsed — allow a probe attempt
            self._opened_at.pop(provider_name, None)
            self._fail_counts[provider_name] = 0
            return False
        return True

    def record_success(self, provider_name: str) -> None:
        self._fail_counts[provider_name] = 0
        self._opened_at.pop(provider_name, None)

    def record_failure(self, provider_name: str) -> None:
        n = self._fail_counts.get(provider_name, 0) + 1
        self._fail_counts[provider_name] = n
        if n >= self.fail_threshold:
            self._opened_at[provider_name] = time.monotonic()
            logger.warning(
                f"Circuit breaker OPEN for '{provider_name}' "
                f"({n} consecutive failures) — cooling down "
                f"{self.cooldown_seconds}s before retrying."
            )


# ── Retry helper ─────────────────────────────────────────────────────────────

def retry_with_backoff(
    fn: Callable[[], Any],
    max_attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    retryable_exceptions: tuple = (Exception,),
) -> Any:
    """
    Synchronous retry with exponential backoff + jitter.
    Raises the last exception if all attempts fail.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except retryable_exceptions as e:
            last_exc = e
            if attempt == max_attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += random.uniform(0, delay * 0.25)
            logger.debug(f"Attempt {attempt}/{max_attempts} failed ({e}); retrying in {delay:.1f}s")
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ── Provider interface ───────────────────────────────────────────────────────

class Provider:
    """Base class for a data provider (NSE, yfinance, local cache, ...)."""

    name: str = "base"

    def fetch_ohlcv(self, symbol: str, period: str, interval: str) -> pd.DataFrame:
        raise NotImplementedError

    def fetch_quote(self, symbol: str) -> dict:
        raise NotImplementedError


class YFinanceProvider(Provider):
    name = "yfinance"

    def fetch_ohlcv(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        import yfinance as yf
        ticker = symbol if symbol.endswith((".NS", ".BO")) else f"{symbol}.NS"
        df = yf.download(ticker, period=period, interval=interval,
                          auto_adjust=True, progress=False, threads=False)
        if df is None or df.empty:
            raise ValueError(f"empty response for {ticker}")
        return df

    def fetch_quote(self, symbol: str) -> dict:
        import yfinance as yf
        ticker = symbol if symbol.endswith((".NS", ".BO")) else f"{symbol}.NS"
        fast = yf.Ticker(ticker).fast_info
        if not fast or fast.get("lastPrice") is None:
            raise ValueError(f"empty fast_info for {ticker}")
        return {
            "symbol": symbol,
            "ltp": fast.get("lastPrice"),
            "prev_close": fast.get("previousClose"),
            "source": self.name,
        }


class NSEProvider(Provider):
    """
    Thin wrapper around the existing screener.live / screener.fetch NSE calls.
    Kept separate so the circuit breaker can isolate NSE failures without
    touching yfinance, and vice versa.
    """
    name = "nse"

    def fetch_ohlcv(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        # NSE's public website does not expose long-history OHLCV cheaply;
        # this provider is intentionally quote/delivery-only. Raising here
        # means the fallback chain moves straight to yfinance for history.
        raise NotImplementedError("NSEProvider does not serve historical OHLCV")

    def fetch_quote(self, symbol: str) -> dict:
        from .live import get_live_quote
        q = get_live_quote(symbol)
        if "error" in q or not q.get("ltp"):
            raise ValueError(q.get("error", "no ltp in NSE response"))
        q["source"] = self.name
        return q


class CacheFallbackProvider(Provider):
    """Last resort: serve the most recent cached value, marked stale."""
    name = "cache"

    def __init__(self, cache):
        self.cache = cache

    def fetch_ohlcv(self, symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
        df = self.cache.load_ohlcv(symbol, ignore_ttl=True)
        if df is None or df.empty:
            raise ValueError(f"no cached OHLCV for {symbol}")
        return df

    def fetch_quote(self, symbol: str) -> dict:
        raise NotImplementedError("CacheFallbackProvider has no live quote store")


# ── Orchestrator ─────────────────────────────────────────────────────────────

class DataFeed:
    """
    Priority-ordered fallback chain across providers, with retry + circuit
    breaking, and an async bulk API with bounded concurrency.

    Example:
        feed = DataFeed()                       # defaults: yfinance, then cache
        result = feed.get_ohlcv("RELIANCE")
        if result.is_stale:
            print("warning: serving cached data, live fetch failed:", result.error)

        results = asyncio.run(feed.get_ohlcv_bulk(["RELIANCE", "TCS", "INFY"]))
    """

    def __init__(self, providers: Optional[list[Provider]] = None,
                 cache=None, max_concurrency: int = 8):
        if providers is None:
            providers = [YFinanceProvider()]
            if cache is not None:
                providers.append(CacheFallbackProvider(cache))
        self.providers = providers
        self.breaker = CircuitBreaker()
        self.max_concurrency = max_concurrency

    def get_ohlcv(self, symbol: str, period: str = "1y", interval: str = "1d") -> FetchResult:
        last_error = None
        for provider in self.providers:
            if self.breaker.is_open(provider.name):
                continue
            try:
                df = retry_with_backoff(
                    lambda: provider.fetch_ohlcv(symbol, period, interval),
                    max_attempts=2 if provider.name != "cache" else 1,
                )
                self.breaker.record_success(provider.name)
                return FetchResult(
                    symbol=symbol, data=df, source=provider.name,
                    is_stale=(provider.name == "cache"),
                )
            except NotImplementedError:
                continue
            except Exception as e:
                last_error = str(e)
                self.breaker.record_failure(provider.name)
                logger.debug(f"{provider.name} failed for {symbol}: {e}")
        return FetchResult(symbol=symbol, data=pd.DataFrame(), error=last_error)

    async def get_ohlcv_bulk(
        self, symbols: list[str], period: str = "1y", interval: str = "1d",
    ) -> dict[str, FetchResult]:
        """Bounded-concurrency async fetch — replaces sequential loop + sleep."""
        semaphore = asyncio.Semaphore(self.max_concurrency)
        loop = asyncio.get_event_loop()

        async def _one(sym: str) -> tuple[str, FetchResult]:
            async with semaphore:
                result = await loop.run_in_executor(
                    None, self.get_ohlcv, sym, period, interval
                )
                return sym, result

        pairs = await asyncio.gather(*[_one(s) for s in symbols])
        return dict(pairs)

    def get_quote(self, symbol: str) -> FetchResult:
        last_error = None
        for provider in self.providers:
            if self.breaker.is_open(provider.name):
                continue
            try:
                q = retry_with_backoff(lambda: provider.fetch_quote(symbol), max_attempts=2)
                self.breaker.record_success(provider.name)
                return FetchResult(symbol=symbol, data=q, source=provider.name)
            except NotImplementedError:
                continue
            except Exception as e:
                last_error = str(e)
                self.breaker.record_failure(provider.name)
        return FetchResult(symbol=symbol, data=None, error=last_error)
