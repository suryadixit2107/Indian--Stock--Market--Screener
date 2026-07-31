"""
technicals.py — Technical indicator calculations for IndiaScreen

All functions accept a pandas DataFrame with OHLCV columns and return
either a Series (single indicator) or a dict of Series (multi-output).

We use pandas-ta where available and fall back to pure pandas/numpy
so the library still works even if pandas-ta is not installed.
"""

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Try importing pandas-ta for richer indicators
try:
    import pandas_ta as ta
    _HAS_TA = True
except ImportError:
    _HAS_TA = False
    logger.info("pandas-ta not found — using built-in indicator implementations.")


# ── Moving averages ────────────────────────────────────────────────────────────

def sma(close: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average."""
    return close.rolling(window=period).mean()


def ema(close: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average."""
    return close.ewm(span=period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """
    Volume-Weighted Average Price (resets daily).
    Requires columns: High, Low, Close, Volume.
    """
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    cum_tp_vol = (typical * df["Volume"]).cumsum()
    cum_vol = df["Volume"].cumsum()
    return cum_tp_vol / cum_vol


# ── RSI ────────────────────────────────────────────────────────────────────────

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """
    Relative Strength Index (Wilder's smoothing).

    Returns Series of values 0–100.
    Convention:
      > 70 → overbought
      < 30 → oversold
    """
    if _HAS_TA:
        result = ta.rsi(close, length=period)
        return result if result is not None else _rsi_manual(close, period)
    return _rsi_manual(close, period)


def _rsi_manual(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# ── MACD ───────────────────────────────────────────────────────────────────────

def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict[str, pd.Series]:
    """
    MACD (Moving Average Convergence Divergence).

    Returns dict with keys:
      macd      — MACD line (fast EMA − slow EMA)
      signal    — Signal line (EMA of MACD)
      histogram — MACD − Signal
      crossover — +1 = bullish cross, -1 = bearish cross, 0 = no cross
    """
    if _HAS_TA:
        result = ta.macd(close, fast=fast, slow=slow, signal=signal)
        if result is not None and not result.empty:
            macd_line = result.iloc[:, 0]
            sig_line  = result.iloc[:, 1]
            hist      = result.iloc[:, 2]
            return {
                "macd":      macd_line,
                "signal":    sig_line,
                "histogram": hist,
                "crossover": _macd_crossover(macd_line, sig_line),
            }
    return _macd_manual(close, fast, slow, signal)


def _macd_manual(close, fast, slow, signal):
    macd_line = ema(close, fast) - ema(close, slow)
    sig_line  = ema(macd_line, signal)
    hist      = macd_line - sig_line
    return {
        "macd":      macd_line,
        "signal":    sig_line,
        "histogram": hist,
        "crossover": _macd_crossover(macd_line, sig_line),
    }


def _macd_crossover(macd_line: pd.Series, signal_line: pd.Series) -> pd.Series:
    """Detect MACD crossovers: +1 bullish, -1 bearish, 0 none."""
    above = (macd_line > signal_line).astype(int)
    cross = above.diff()
    return cross.fillna(0).astype(int)


# ── Bollinger Bands ────────────────────────────────────────────────────────────

def bollinger_bands(
    close: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> dict[str, pd.Series]:
    """
    Bollinger Bands.

    Returns dict with keys: upper, middle (SMA), lower, bandwidth, %b
    """
    if _HAS_TA:
        result = ta.bbands(close, length=period, std=std_dev)
        if result is not None and not result.empty:
            cols = result.columns.tolist()
            return {
                "lower":     result[cols[0]],
                "middle":    result[cols[1]],
                "upper":     result[cols[2]],
                "bandwidth": result[cols[3]],
                "pct_b":     result[cols[4]],
            }
    return _bb_manual(close, period, std_dev)


def _bb_manual(close, period, std_dev):
    middle = sma(close, period)
    std    = close.rolling(period).std()
    upper  = middle + std_dev * std
    lower  = middle - std_dev * std
    bandwidth = (upper - lower) / middle
    pct_b     = (close - lower) / (upper - lower)
    return {"upper": upper, "middle": middle, "lower": lower,
            "bandwidth": bandwidth, "pct_b": pct_b}


# ── Supertrend ────────────────────────────────────────────────────────────────

def supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> dict[str, pd.Series]:
    """
    Supertrend indicator — widely used in Indian retail trading.

    Returns dict with keys:
      supertrend  — Series of support/resistance values
      direction   — +1 = uptrend, -1 = downtrend
      buy_signal  — True where trend flips from down to up
      sell_signal — True where trend flips from up to down
    """
    if _HAS_TA:
        result = ta.supertrend(df["High"], df["Low"], df["Close"],
                               length=period, multiplier=multiplier)
        if result is not None and not result.empty:
            st_col  = [c for c in result.columns if c.startswith("SUPERT_") and "d" not in c][0]
            dir_col = [c for c in result.columns if "SUPERTd" in c][0]
            st      = result[st_col]
            dirs    = result[dir_col]
            return {
                "supertrend":  st,
                "direction":   dirs,
                "buy_signal":  (dirs == 1) & (dirs.shift(1) == -1),
                "sell_signal": (dirs == -1) & (dirs.shift(1) == 1),
            }
    return _supertrend_manual(df, period, multiplier)


def _supertrend_manual(df, period, multiplier):
    high, low, close = df["High"], df["Low"], df["Close"]
    hl2 = (high + low) / 2

    # Average True Range
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()

    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    supertrend = pd.Series(index=close.index, dtype=float)
    direction  = pd.Series(index=close.index, dtype=int)

    for i in range(1, len(close)):
        idx, prev_idx = close.index[i], close.index[i - 1]

        # Adjust bands
        ub = upper_band.iloc[i]
        lb = lower_band.iloc[i]
        if ub < upper_band.iloc[i - 1] or close.iloc[i - 1] > upper_band.iloc[i - 1]:
            upper_band.iloc[i] = ub
        else:
            upper_band.iloc[i] = upper_band.iloc[i - 1]

        if lb > lower_band.iloc[i - 1] or close.iloc[i - 1] < lower_band.iloc[i - 1]:
            lower_band.iloc[i] = lb
        else:
            lower_band.iloc[i] = lower_band.iloc[i - 1]

        prev_dir = direction.iloc[i - 1] if i > 1 else 1
        if prev_dir == -1 and close.iloc[i] > upper_band.iloc[i]:
            direction.iloc[i] = 1
        elif prev_dir == 1 and close.iloc[i] < lower_band.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = prev_dir

        supertrend.iloc[i] = lower_band.iloc[i] if direction.iloc[i] == 1 else upper_band.iloc[i]

    return {
        "supertrend":  supertrend,
        "direction":   direction,
        "buy_signal":  (direction == 1) & (direction.shift(1) == -1),
        "sell_signal": (direction == -1) & (direction.shift(1) == 1),
    }


# ── Volume indicators ─────────────────────────────────────────────────────────

def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """On-Balance Volume."""
    direction = np.sign(close.diff()).fillna(0)
    return (direction * volume).cumsum()


def volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Today's volume vs N-day average (useful for breakout detection)."""
    return volume / volume.rolling(period).mean()


# ── Pattern detection ─────────────────────────────────────────────────────────

def near_52w_high(close: pd.Series, pct_threshold: float = 2.0) -> pd.Series:
    """True where price is within pct_threshold % of its 52-week high."""
    high_52w = close.rolling(252).max()
    return (high_52w - close) / high_52w * 100 <= pct_threshold


def above_dma(close: pd.Series, period: int = 200) -> pd.Series:
    """True where close is above N-day moving average."""
    return close > sma(close, period)


def dma_distance_pct(close: pd.Series, period: int = 200) -> pd.Series:
    """% distance of close from N-day moving average (+ve = above, -ve = below)."""
    ma = sma(close, period)
    return (close - ma) / ma * 100


# ── Composite signal ──────────────────────────────────────────────────────────

def compute_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all indicators for a single stock's OHLCV DataFrame.

    Returns the original DataFrame with additional columns:
      sma_50, sma_200, ema_20,
      rsi_14,
      macd, macd_signal, macd_hist, macd_cross,
      bb_upper, bb_lower, bb_pct_b,
      supertrend, st_direction,
      obv, vol_ratio,
      above_200dma, dma200_pct,
      near_52w_high
    """
    close  = df["Close"]
    volume = df["Volume"]

    out = df.copy()

    # Moving averages
    out["sma_50"]  = sma(close, 50)
    out["sma_200"] = sma(close, 200)
    out["ema_20"]  = ema(close, 20)

    # RSI
    out["rsi_14"] = rsi(close, 14)

    # MACD
    m = macd(close)
    out["macd"]       = m["macd"]
    out["macd_signal"]= m["signal"]
    out["macd_hist"]  = m["histogram"]
    out["macd_cross"] = m["crossover"]

    # Bollinger
    bb = bollinger_bands(close)
    out["bb_upper"] = bb["upper"]
    out["bb_lower"] = bb["lower"]
    out["bb_pct_b"] = bb["pct_b"]

    # Supertrend
    st = supertrend(df)
    out["supertrend"]   = st["supertrend"]
    out["st_direction"] = st["direction"]

    # Volume
    out["obv"]       = obv(close, volume)
    out["vol_ratio"] = volume_ratio(volume)

    # Price vs DMA
    out["above_200dma"] = above_dma(close, 200)
    out["dma200_pct"]   = dma_distance_pct(close, 200)

    # 52-week high proximity
    out["near_52w_high"] = near_52w_high(close)

    return out


def latest_signals(df: pd.DataFrame) -> dict:
    """
    Extract the most recent row of computed indicators as a flat dict.
    Useful for screener row generation.
    """
    if df.empty:
        return {}
    row = df.iloc[-1]
    return {
        "price":         round(float(row.get("Close", 0)), 2),
        "sma_50":        round(float(row.get("sma_50", 0)), 2),
        "sma_200":       round(float(row.get("sma_200", 0)), 2),
        "ema_20":        round(float(row.get("ema_20", 0)), 2),
        "rsi_14":        round(float(row.get("rsi_14", 50)), 1),
        "macd":          round(float(row.get("macd", 0)), 3),
        "macd_signal":   round(float(row.get("macd_signal", 0)), 3),
        "macd_hist":     round(float(row.get("macd_hist", 0)), 3),
        "macd_cross":    int(row.get("macd_cross", 0)),
        "bb_pct_b":      round(float(row.get("bb_pct_b", 0.5)), 3),
        "above_200dma":  bool(row.get("above_200dma", False)),
        "dma200_pct":    round(float(row.get("dma200_pct", 0)), 2),
        "near_52w_high": bool(row.get("near_52w_high", False)),
        "st_direction":  int(row.get("st_direction", 0)),
        "vol_ratio":     round(float(row.get("vol_ratio", 1)), 2),
    }
