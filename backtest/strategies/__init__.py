"""
strategies/__init__.py — Built-in trading strategies for IndiaScreen

Each strategy implements:
  should_enter(row, df) → bool
  should_exit(row, trade, df) → bool

Add your own by subclassing BaseStrategy.
"""

import numpy as np
import pandas as pd

from ..engine import Trade


class BaseStrategy:
    name = "Base"

    def should_enter(self, row: pd.Series, df: pd.DataFrame) -> bool:
        raise NotImplementedError

    def should_exit(self, row: pd.Series, trade: Trade, df: pd.DataFrame) -> bool:
        raise NotImplementedError


# ── RSI Oversold Bounce ───────────────────────────────────────────────────────

class RSIOversoldBounce(BaseStrategy):
    """
    Buy when RSI crosses above 30 from below (oversold bounce).
    Sell when RSI crosses above 65 or stop-loss at -8%.
    """
    name = "RSI Oversold Bounce"

    def __init__(self, rsi_entry: float = 30, rsi_exit: float = 65, stop_loss: float = -0.08):
        self.rsi_entry = rsi_entry
        self.rsi_exit  = rsi_exit
        self.stop_loss = stop_loss

    def should_enter(self, row, df):
        if "rsi_14" not in df.columns:
            return False
        idx  = df.index.get_loc(row.name)
        if idx < 1:
            return False
        prev_rsi = df.iloc[idx - 1]["rsi_14"]
        curr_rsi = row["rsi_14"]
        return prev_rsi < self.rsi_entry and curr_rsi >= self.rsi_entry

    def should_exit(self, row, trade, df):
        curr_rsi = row.get("rsi_14", 50)
        pnl_pct  = (row["Close"] - trade.entry_price) / trade.entry_price
        return curr_rsi > self.rsi_exit or pnl_pct <= self.stop_loss


# ── Golden Cross ──────────────────────────────────────────────────────────────

class GoldenCross(BaseStrategy):
    """
    Buy when 50 DMA crosses above 200 DMA (golden cross).
    Sell when 50 DMA crosses below 200 DMA (death cross).
    """
    name = "Golden Cross"

    def should_enter(self, row, df):
        if "sma_50" not in df.columns or "sma_200" not in df.columns:
            return False
        idx = df.index.get_loc(row.name)
        if idx < 1:
            return False
        prev = df.iloc[idx - 1]
        return (prev["sma_50"] <= prev["sma_200"]) and (row["sma_50"] > row["sma_200"])

    def should_exit(self, row, trade, df):
        if "sma_50" not in df.columns or "sma_200" not in df.columns:
            return False
        return row["sma_50"] < row["sma_200"]


# ── MACD Bullish Crossover ────────────────────────────────────────────────────

class MACDBullishCross(BaseStrategy):
    """
    Buy on MACD bullish crossover (MACD line crosses above signal line).
    Sell on MACD bearish crossover.
    """
    name = "MACD Bullish Crossover"

    def __init__(self, stop_loss: float = -0.07):
        self.stop_loss = stop_loss

    def should_enter(self, row, df):
        return row.get("macd_cross") == 1 and row.get("macd_hist", 0) > 0

    def should_exit(self, row, trade, df):
        pnl_pct = (row["Close"] - trade.entry_price) / trade.entry_price
        return row.get("macd_cross") == -1 or pnl_pct <= self.stop_loss


# ── 52-Week Breakout ──────────────────────────────────────────────────────────

class BreakoutStrategy(BaseStrategy):
    """
    Buy when price breaks above its 52-week high on high volume.
    Sell after a fixed 15% gain or 7% stop-loss.
    """
    name = "52-Week High Breakout"

    def __init__(self, target_pct: float = 0.15, stop_loss: float = -0.07, vol_threshold: float = 1.5):
        self.target    = target_pct
        self.stop_loss = stop_loss
        self.vol_thr   = vol_threshold

    def should_enter(self, row, df):
        return (
            bool(row.get("near_52w_high")) and
            row.get("vol_ratio", 1) >= self.vol_thr and
            bool(row.get("above_200dma"))
        )

    def should_exit(self, row, trade, df):
        pnl_pct = (row["Close"] - trade.entry_price) / trade.entry_price
        return pnl_pct >= self.target or pnl_pct <= self.stop_loss


# ── Supertrend Trend-Following ────────────────────────────────────────────────

class SupertrendFollower(BaseStrategy):
    """
    Buy when Supertrend flips to bullish (direction changes to +1).
    Sell when Supertrend flips to bearish.
    """
    name = "Supertrend Follower"

    def should_enter(self, row, df):
        idx = df.index.get_loc(row.name)
        if idx < 1:
            return False
        prev_dir = df.iloc[idx - 1].get("st_direction", 0)
        curr_dir = row.get("st_direction", 0)
        return prev_dir == -1 and curr_dir == 1

    def should_exit(self, row, trade, df):
        return row.get("st_direction") == -1


# ── Low P/E Value Strategy ────────────────────────────────────────────────────

class LowPEValueBuy(BaseStrategy):
    """
    Technical wrapper around a value screening approach:
    Enter when RSI is between 35–55 and above 200 DMA.
    Exit on RSI > 65 or trailing stop.
    (P/E filter is applied at screener level before backtesting.)
    """
    name = "Low P/E Value Buy"

    def __init__(self, rsi_entry_max: float = 55, rsi_exit: float = 65, stop_loss: float = -0.10):
        self.rsi_max   = rsi_entry_max
        self.rsi_exit  = rsi_exit
        self.stop_loss = stop_loss

    def should_enter(self, row, df):
        rsi   = row.get("rsi_14", 50)
        above = row.get("above_200dma", False)
        return 30 <= rsi <= self.rsi_max and above

    def should_exit(self, row, trade, df):
        rsi     = row.get("rsi_14", 50)
        pnl_pct = (row["Close"] - trade.entry_price) / trade.entry_price
        return rsi > self.rsi_exit or pnl_pct <= self.stop_loss


# ── Strategy registry ──────────────────────────────────────────────────────────

STRATEGIES = {
    "rsi_bounce":   RSIOversoldBounce,
    "golden_cross": GoldenCross,
    "macd_cross":   MACDBullishCross,
    "breakout":     BreakoutStrategy,
    "supertrend":   SupertrendFollower,
    "low_pe":       LowPEValueBuy,
}


def get_strategy(name: str, **kwargs) -> BaseStrategy:
    """Instantiate a strategy by name."""
    if name not in STRATEGIES:
        raise ValueError(f"Unknown strategy '{name}'. Available: {list(STRATEGIES.keys())}")
    return STRATEGIES[name](**kwargs)
