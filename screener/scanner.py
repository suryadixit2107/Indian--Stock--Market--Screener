"""
scanner.py — Core screening pipeline for IndiaScreen

Orchestrates data fetching, technical analysis, and fundamental filtering
into a single Screener class with a clean Python API and CLI interface.

Usage (Python):
    from indiascreen import Screener
    s = Screener(index="nifty50")
    results = s.run(rsi_max=50, pe_max=25, above_200dma=True)
    print(results[["symbol", "price", "rsi_14", "pe_ratio", "signal"]])

Usage (CLI):
    indiascreen run --index nifty50 --rsi-max 50 --pe-max 25 --above-200dma
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .fetch import (
    get_index_symbols,
    fetch_bulk,
    fetch_fundamentals,
    get_delivery_pct,
)
from .technicals import compute_all, latest_signals
from .fundamentals import FundamentalFilter, passes_filter, fundamental_score

logger = logging.getLogger(__name__)


# ── Signal generation ─────────────────────────────────────────────────────────

def _generate_signal(tech: dict, fund: dict, delivery_pct: Optional[float]) -> str:
    """
    Generate a composite signal string from technical + fundamental data.

    Returns one of: "Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"
    """
    score = 0

    # Technical signals
    rsi = tech.get("rsi_14", 50)
    if rsi < 30:      score += 2
    elif rsi < 45:    score += 1
    elif rsi > 70:    score -= 2
    elif rsi > 60:    score -= 1

    macd_cross = tech.get("macd_cross", 0)
    if macd_cross == 1:   score += 2   # bullish crossover
    elif macd_cross == -1: score -= 2  # bearish crossover

    macd_hist = tech.get("macd_hist", 0)
    if macd_hist > 0:  score += 1
    elif macd_hist < 0: score -= 1

    if tech.get("above_200dma"):  score += 1
    if tech.get("st_direction") == 1:  score += 1
    elif tech.get("st_direction") == -1: score -= 1

    # Fundamental signals
    pe = fund.get("pe_ratio")
    if pe:
        if pe < 12:       score += 2
        elif pe < 20:     score += 1
        elif pe > 50:     score -= 2
        elif pe > 35:     score -= 1

    roe = fund.get("roe") or 0
    if roe > 0.20:    score += 1
    elif roe < 0.05:  score -= 1

    de = fund.get("debt_to_equity") or 0
    if de > 2.0:      score -= 1

    rg = fund.get("revenue_growth") or 0
    if rg > 0.15:     score += 1
    elif rg < 0:      score -= 1

    # Delivery percentage (high delivery = genuine demand, not intraday noise)
    if delivery_pct is not None:
        if delivery_pct >= 60:   score += 1
        elif delivery_pct < 25:  score -= 1

    # Map score to signal
    if score >= 6:    return "Strong Buy"
    elif score >= 3:  return "Buy"
    elif score >= -2: return "Hold"
    elif score >= -5: return "Sell"
    else:             return "Strong Sell"


# ── Screener class ─────────────────────────────────────────────────────────────

class Screener:
    """
    Main screener class for IndiaScreen.

    Args:
        index:    "nifty50", "nifty500", "sensex", or list of NSE symbols
        period:   Historical data period for technical indicators (default "1y")
        exchange: "NSE" or "BSE"

    Example:
        s = Screener(index="nifty50")
        results = s.run(rsi_max=50, pe_max=25)
        results.to_csv("signals.csv", index=False)
    """

    def __init__(
        self,
        index: str | list[str] = "nifty50",
        period: str = "1y",
        exchange: str = "NSE",
    ):
        if isinstance(index, list):
            self.symbols = index
        else:
            self.symbols = get_index_symbols(index)
        self.period   = period
        self.exchange = exchange
        self._ohlcv_cache: dict[str, pd.DataFrame] = {}
        logger.info(f"Screener initialised: {len(self.symbols)} symbols, {period} data")

    def run(
        self,
        # Technical filters
        rsi_min:          Optional[float] = None,
        rsi_max:          Optional[float] = None,
        above_200dma:     Optional[bool]  = None,
        macd_bullish:     Optional[bool]  = None,
        macd_bearish:     Optional[bool]  = None,
        supertrend_up:    Optional[bool]  = None,
        near_52w_high:    Optional[bool]  = None,
        vol_ratio_min:    Optional[float] = None,   # e.g. 1.5 = 50% above avg volume
        delivery_min:     Optional[float] = None,   # % e.g. 40.0
        # Fundamental filters
        pe_min:           Optional[float] = None,
        pe_max:           Optional[float] = None,
        pb_max:           Optional[float] = None,
        roe_min:          Optional[float] = None,
        debt_equity_max:  Optional[float] = None,
        market_cap_min_cr:Optional[float] = None,
        sectors:          Optional[list]  = None,
        fundamental_preset: Optional[str] = None,  # "value","growth","quality","dividend"
        # Output options
        signals:          Optional[list]  = None,   # ["Buy","Strong Buy"]
        top_n:            Optional[int]   = None,
        sort_by:          str             = "fund_score",
        ascending:        bool            = False,
    ) -> pd.DataFrame:
        """
        Run the screen and return a sorted DataFrame of results.

        All filter arguments are optional — pass only what you want to filter on.

        Returns DataFrame with columns:
            symbol, company_name, sector, price, change_pct,
            rsi_14, macd_cross, above_200dma, dma200_pct, st_direction,
            vol_ratio, near_52w_high,
            pe_ratio, pb_ratio, roe, debt_to_equity, market_cap_cr,
            dividend_yield, revenue_growth, delivery_pct,
            fund_score, signal
        """
        # 1. Fetch OHLCV (bulk, cached)
        logger.info("Fetching OHLCV data...")
        if not self._ohlcv_cache:
            self._ohlcv_cache = fetch_bulk(self.symbols, period=self.period,
                                           exchange=self.exchange)

        # 2. Fetch delivery percentages (today's bhavcopy)
        logger.info("Fetching delivery data from NSE Bhavcopy...")
        delivery_map = get_delivery_pct(self.symbols)

        # 3. Build result rows
        rows = []
        for sym in self.symbols:
            df = self._ohlcv_cache.get(sym, pd.DataFrame())
            if df.empty or len(df) < 30:
                logger.debug(f"Skipping {sym}: insufficient data ({len(df)} rows)")
                continue

            # Compute technicals
            try:
                df_ind = compute_all(df)
                tech = latest_signals(df_ind)
            except Exception as e:
                logger.warning(f"Indicator error for {sym}: {e}")
                continue

            # Fetch fundamentals (with small delay to avoid rate limits)
            try:
                fund = fetch_fundamentals(sym, self.exchange)
                time.sleep(0.1)
            except Exception as e:
                logger.warning(f"Fundamentals error for {sym}: {e}")
                fund = {}

            delivery_pct = delivery_map.get(sym)

            # Apply technical filters
            if rsi_min is not None and tech.get("rsi_14", 50) < rsi_min:
                continue
            if rsi_max is not None and tech.get("rsi_14", 50) > rsi_max:
                continue
            if above_200dma is True  and not tech.get("above_200dma"):
                continue
            if above_200dma is False and tech.get("above_200dma"):
                continue
            if macd_bullish is True  and tech.get("macd_cross") != 1:
                continue
            if macd_bearish is True  and tech.get("macd_cross") != -1:
                continue
            if supertrend_up is True  and tech.get("st_direction") != 1:
                continue
            if near_52w_high is True  and not tech.get("near_52w_high"):
                continue
            if vol_ratio_min is not None and tech.get("vol_ratio", 1) < vol_ratio_min:
                continue
            if delivery_min is not None and (delivery_pct or 0) < delivery_min:
                continue

            # Apply fundamental filters
            from .fundamentals import PRESETS
            f = PRESETS.get(fundamental_preset, FundamentalFilter()) if fundamental_preset else FundamentalFilter()
            # Override with explicit args
            if pe_min is not None:           f.pe_min = pe_min
            if pe_max is not None:           f.pe_max = pe_max
            if pb_max is not None:           f.pb_max = pb_max
            if roe_min is not None:          f.roe_min = roe_min
            if debt_equity_max is not None:  f.debt_equity_max = debt_equity_max
            if market_cap_min_cr is not None: f.market_cap_min_cr = market_cap_min_cr
            if sectors:                      f.sectors = sectors

            passed, fail_reasons = passes_filter(fund, f)
            if not passed:
                logger.debug(f"{sym} failed fundamental filter: {fail_reasons}")
                continue

            # Build row
            mc = fund.get("market_cap")
            prev_close = float(df["Close"].iloc[-2]) if len(df) > 1 else None
            curr_price = tech.get("price", 0)
            chg_pct = ((curr_close := curr_price) - prev_close) / prev_close * 100 \
                      if prev_close else 0

            row = {
                "symbol":       sym,
                "company_name": fund.get("company_name", sym),
                "sector":       fund.get("sector", "Unknown"),
                "price":        curr_price,
                "change_pct":   round(chg_pct, 2),
                # Technicals
                "rsi_14":       tech.get("rsi_14"),
                "macd_cross":   tech.get("macd_cross"),
                "macd_hist":    tech.get("macd_hist"),
                "above_200dma": tech.get("above_200dma"),
                "dma200_pct":   tech.get("dma200_pct"),
                "st_direction": tech.get("st_direction"),
                "vol_ratio":    tech.get("vol_ratio"),
                "near_52w_high":tech.get("near_52w_high"),
                "bb_pct_b":     tech.get("bb_pct_b"),
                # Fundamentals
                "pe_ratio":       fund.get("pe_ratio"),
                "pb_ratio":       fund.get("pb_ratio"),
                "roe":            round(fund.get("roe", 0) * 100, 1) if fund.get("roe") else None,
                "debt_to_equity": fund.get("debt_to_equity"),
                "market_cap_cr":  round(mc / 1e7, 0) if mc else None,
                "dividend_yield": round(fund.get("dividend_yield", 0) * 100, 2) if fund.get("dividend_yield") else None,
                "revenue_growth": round(fund.get("revenue_growth", 0) * 100, 1) if fund.get("revenue_growth") else None,
                "profit_margin":  round(fund.get("profit_margins", 0) * 100, 1) if fund.get("profit_margins") else None,
                "52w_high":       fund.get("52w_high"),
                "52w_low":        fund.get("52w_low"),
                # Delivery (unique to Indian markets)
                "delivery_pct":   delivery_pct,
                # Scores & signal
                "fund_score":     fundamental_score(fund),
                "signal":         _generate_signal(tech, fund, delivery_pct),
            }
            rows.append(row)

        if not rows:
            logger.warning("No stocks passed all filters.")
            return pd.DataFrame()

        result = pd.DataFrame(rows)

        # Filter by signal
        if signals:
            result = result[result["signal"].isin(signals)]

        # Sort
        if sort_by in result.columns:
            result = result.sort_values(sort_by, ascending=ascending)

        # Top N
        if top_n:
            result = result.head(top_n)

        logger.info(f"Screen complete: {len(result)} stocks passed.")
        return result.reset_index(drop=True)

    def refresh(self):
        """Clear the OHLCV cache and re-fetch on next run()."""
        self._ohlcv_cache.clear()
        logger.info("Cache cleared.")


# ── CLI entry point ───────────────────────────────────────────────────────────

def cli():
    """
    Command-line interface for IndiaScreen.

    Usage:
        indiascreen run --index nifty50 --rsi-max 50 --pe-max 25
        indiascreen run --index nifty500 --above-200dma --signals "Buy,Strong Buy"
        indiascreen run --sector "Banking & Finance" --fundamental-preset value
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="indiascreen",
        description="🇮🇳 Free, open-source NSE/BSE stock screener",
    )
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="Run the screener")

    run.add_argument("--index",     default="nifty50",
                     help="nifty50 | nifty500 | sensex (default: nifty50)")
    run.add_argument("--period",    default="1y",
                     help="Historical data period: 6mo | 1y | 2y (default: 1y)")
    run.add_argument("--rsi-min",   type=float)
    run.add_argument("--rsi-max",   type=float)
    run.add_argument("--pe-min",    type=float)
    run.add_argument("--pe-max",    type=float)
    run.add_argument("--pb-max",    type=float)
    run.add_argument("--roe-min",   type=float, help="Minimum ROE as decimal e.g. 0.15")
    run.add_argument("--de-max",    type=float, help="Max debt/equity ratio")
    run.add_argument("--cap-min",   type=float, help="Min market cap in ₹ crore")
    run.add_argument("--delivery-min", type=float, help="Min delivery % e.g. 40")
    run.add_argument("--above-200dma", action="store_true")
    run.add_argument("--macd-bullish", action="store_true")
    run.add_argument("--supertrend-up", action="store_true")
    run.add_argument("--near-52w-high", action="store_true")
    run.add_argument("--vol-ratio-min", type=float)
    run.add_argument("--sector",    help="Filter by sector name")
    run.add_argument("--signals",   help="Comma-separated: 'Buy,Strong Buy'")
    run.add_argument("--preset",    help="Fundamental preset: value|growth|quality|dividend")
    run.add_argument("--top",       type=int, help="Return top N results")
    run.add_argument("--sort-by",   default="fund_score")
    run.add_argument("--output",    help="Save to CSV path e.g. output.csv")
    run.add_argument("--verbose",   action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    print(f"\n🔍 Running IndiaScreen on {args.index.upper()}...\n")

    s = Screener(index=args.index, period=args.period)
    results = s.run(
        rsi_min=args.rsi_min,
        rsi_max=args.rsi_max,
        pe_min=args.pe_min,
        pe_max=args.pe_max,
        pb_max=args.pb_max,
        roe_min=args.roe_min,
        debt_equity_max=args.de_max,
        market_cap_min_cr=args.cap_min,
        delivery_min=args.delivery_min,
        above_200dma=True if args.above_200dma else None,
        macd_bullish=True if args.macd_bullish else None,
        supertrend_up=True if args.supertrend_up else None,
        near_52w_high=True if args.near_52w_high else None,
        vol_ratio_min=args.vol_ratio_min,
        sectors=[args.sector] if args.sector else None,
        signals=args.signals.split(",") if args.signals else None,
        fundamental_preset=args.preset,
        top_n=args.top,
        sort_by=args.sort_by,
    )

    if results.empty:
        print("❌ No stocks matched the filters.")
        return

    display_cols = [
        "symbol", "price", "change_pct", "rsi_14",
        "pe_ratio", "roe", "market_cap_cr", "delivery_pct", "signal",
    ]
    display_cols = [c for c in display_cols if c in results.columns]

    print(results[display_cols].to_string(index=False))
    print(f"\n✅ {len(results)} stocks matched.\n")

    if args.output:
        results.to_csv(args.output, index=False)
        print(f"💾 Saved to {args.output}")


if __name__ == "__main__":
    cli()
