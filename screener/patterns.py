"""
patterns.py — Candlestick pattern detection for IndiaScreen

Detects the patterns most commonly used by Indian retail traders.
All functions accept a pandas DataFrame with OHLCV columns.

Each detection function returns a pandas Series:
  +100 = bullish pattern detected
  -100 = bearish pattern detected
     0 = no pattern

Usage:
    from screener.patterns import detect_all_patterns, PatternSummary

    df = compute_all(ohlcv_df)        # adds OHLC indicators
    df = detect_all_patterns(df)      # adds ~15 pattern columns
    summary = PatternSummary(df).latest()
"""

import logging
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import pandas_ta as ta
    _HAS_TA = True
except ImportError:
    _HAS_TA = False
    logger.info("pandas-ta not found — using manual pattern implementations.")


# ── Helper utilities ──────────────────────────────────────────────────────────

def _body(o: pd.Series, c: pd.Series) -> pd.Series:
    """Absolute candle body size."""
    return (c - o).abs()


def _upper_wick(o: pd.Series, h: pd.Series, c: pd.Series) -> pd.Series:
    """Upper wick (shadow) length."""
    return h - pd.concat([o, c], axis=1).max(axis=1)


def _lower_wick(o: pd.Series, l: pd.Series, c: pd.Series) -> pd.Series:
    """Lower wick (shadow) length."""
    return pd.concat([o, c], axis=1).min(axis=1) - l


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range for body size normalisation."""
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - df["Close"].shift(1)).abs(),
        (df["Low"]  - df["Close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def _is_bullish(o: pd.Series, c: pd.Series) -> pd.Series:
    return c > o


def _is_bearish(o: pd.Series, c: pd.Series) -> pd.Series:
    return c < o


# ── Single-candle patterns ────────────────────────────────────────────────────

def doji(df: pd.DataFrame, threshold: float = 0.05) -> pd.Series:
    """
    Doji — open and close nearly the same price.
    Signals indecision; context determines direction.

    threshold: max body/range ratio to qualify as doji.
    """
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body  = _body(o, c)
    range_ = (h - l).replace(0, np.nan)
    is_doji = body / range_ <= threshold
    return is_doji.map({True: 100, False: 0}).fillna(0).astype(int)


def hammer(df: pd.DataFrame) -> pd.Series:
    """
    Hammer (bullish) — appears after a downtrend.
    Small body at top, long lower wick (>= 2× body), tiny upper wick.
    """
    if _HAS_TA:
        result = ta.cdl_pattern(df["Open"], df["High"], df["Low"], df["Close"], name="hammer")
        if result is not None:
            return result.fillna(0).astype(int)

    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body  = _body(o, c)
    lower = _lower_wick(o, l, c)
    upper = _upper_wick(o, h, c)
    is_h  = (lower >= 2 * body) & (upper <= 0.1 * body) & (body > 0)
    return is_h.map({True: 100, False: 0}).astype(int)


def hanging_man(df: pd.DataFrame) -> pd.Series:
    """
    Hanging Man (bearish) — same shape as hammer but appears after an uptrend.
    """
    if _HAS_TA:
        result = ta.cdl_pattern(df["Open"], df["High"], df["Low"], df["Close"], name="hangingman")
        if result is not None:
            return result.fillna(0).astype(int)

    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body  = _body(o, c)
    lower = _lower_wick(o, l, c)
    upper = _upper_wick(o, h, c)
    is_hm = (lower >= 2 * body) & (upper <= 0.1 * body) & (body > 0)
    return is_hm.map({True: -100, False: 0}).astype(int)


def shooting_star(df: pd.DataFrame) -> pd.Series:
    """
    Shooting Star (bearish) — small body at bottom, long upper wick (>= 2× body).
    Appears after uptrend, signals reversal.
    """
    if _HAS_TA:
        result = ta.cdl_pattern(df["Open"], df["High"], df["Low"], df["Close"], name="shootingstar")
        if result is not None:
            return result.fillna(0).astype(int)

    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body  = _body(o, c)
    upper = _upper_wick(o, h, c)
    lower = _lower_wick(o, l, c)
    is_ss = (upper >= 2 * body) & (lower <= 0.1 * body) & (body > 0)
    return is_ss.map({True: -100, False: 0}).astype(int)


def marubozu_bullish(df: pd.DataFrame, wick_threshold: float = 0.02) -> pd.Series:
    """
    Bullish Marubozu — large green candle with no (or tiny) wicks.
    Strong momentum candle; wick_threshold is max wick/body ratio.
    """
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body  = _body(o, c)
    upper = _upper_wick(o, h, c)
    lower = _lower_wick(o, l, c)
    is_m  = _is_bullish(o, c) & (upper / body.replace(0, np.nan) <= wick_threshold) \
                                & (lower / body.replace(0, np.nan) <= wick_threshold)
    return is_m.fillna(False).map({True: 100, False: 0}).astype(int)


def marubozu_bearish(df: pd.DataFrame, wick_threshold: float = 0.02) -> pd.Series:
    """Bearish Marubozu — large red candle with no wicks."""
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body  = _body(o, c)
    upper = _upper_wick(o, h, c)
    lower = _lower_wick(o, l, c)
    is_m  = _is_bearish(o, c) & (upper / body.replace(0, np.nan) <= wick_threshold) \
                                & (lower / body.replace(0, np.nan) <= wick_threshold)
    return is_m.fillna(False).map({True: -100, False: 0}).astype(int)


def spinning_top(df: pd.DataFrame) -> pd.Series:
    """
    Spinning Top — small body, wicks longer than body on both sides.
    Signals indecision; watch next candle for direction.
    """
    o, h, l, c = df["Open"], df["High"], df["Low"], df["Close"]
    body  = _body(o, c)
    upper = _upper_wick(o, h, c)
    lower = _lower_wick(o, l, c)
    is_st = (upper > body) & (lower > body)
    return is_st.map({True: 100, False: 0}).astype(int)


# ── Two-candle patterns ───────────────────────────────────────────────────────

def bullish_engulfing(df: pd.DataFrame) -> pd.Series:
    """
    Bullish Engulfing — large green candle completely engulfs previous red candle.
    Strong reversal signal after downtrend.
    """
    if _HAS_TA:
        result = ta.cdl_pattern(df["Open"], df["High"], df["Low"], df["Close"], name="engulfing")
        if result is not None:
            return result.clip(lower=0).fillna(0).astype(int)

    o, c = df["Open"], df["Close"]
    prev_bear = _is_bearish(o.shift(1), c.shift(1))
    curr_bull = _is_bullish(o, c)
    engulfs   = (c > o.shift(1)) & (o < c.shift(1))
    return (prev_bear & curr_bull & engulfs).map({True: 100, False: 0}).astype(int)


def bearish_engulfing(df: pd.DataFrame) -> pd.Series:
    """
    Bearish Engulfing — large red candle engulfs previous green candle.
    Strong reversal signal after uptrend.
    """
    if _HAS_TA:
        result = ta.cdl_pattern(df["Open"], df["High"], df["Low"], df["Close"], name="engulfing")
        if result is not None:
            return result.clip(upper=0).fillna(0).astype(int)

    o, c = df["Open"], df["Close"]
    prev_bull = _is_bullish(o.shift(1), c.shift(1))
    curr_bear = _is_bearish(o, c)
    engulfs   = (c < o.shift(1)) & (o > c.shift(1))
    return (prev_bull & curr_bear & engulfs).map({True: -100, False: 0}).astype(int)


def piercing_line(df: pd.DataFrame) -> pd.Series:
    """
    Piercing Line (bullish) — green candle opens below prev low, 
    closes above midpoint of previous red candle.
    """
    o, l, c = df["Open"], df["Low"], df["Close"]
    prev_mid  = (o.shift(1) + c.shift(1)) / 2
    prev_bear = _is_bearish(o.shift(1), c.shift(1))
    curr_bull = _is_bullish(o, c)
    pierce    = (o < l.shift(1)) & (c > prev_mid) & (c < o.shift(1))
    return (prev_bear & curr_bull & pierce).map({True: 100, False: 0}).astype(int)


def dark_cloud_cover(df: pd.DataFrame) -> pd.Series:
    """
    Dark Cloud Cover (bearish) — opposite of piercing line.
    Red candle opens above prev high, closes below midpoint of previous green.
    """
    o, h, c = df["Open"], df["High"], df["Close"]
    prev_mid  = (o.shift(1) + c.shift(1)) / 2
    prev_bull = _is_bullish(o.shift(1), c.shift(1))
    curr_bear = _is_bearish(o, c)
    dark      = (o > h.shift(1)) & (c < prev_mid) & (c > o.shift(1))
    return (prev_bull & curr_bear & dark).map({True: -100, False: 0}).astype(int)


# ── Three-candle patterns ─────────────────────────────────────────────────────

def morning_star(df: pd.DataFrame) -> pd.Series:
    """
    Morning Star (bullish) — 3-candle reversal:
    1. Large red candle
    2. Small-body candle (gap down)
    3. Large green candle that closes into 1st candle's body
    """
    if _HAS_TA:
        result = ta.cdl_pattern(df["Open"], df["High"], df["Low"], df["Close"], name="morningstar")
        if result is not None:
            return result.fillna(0).astype(int)

    o, c = df["Open"], df["Close"]
    body1 = _body(o.shift(2), c.shift(2))
    body2 = _body(o.shift(1), c.shift(1))
    body3 = _body(o, c)
    midpoint1 = (o.shift(2) + c.shift(2)) / 2

    cond = (
        _is_bearish(o.shift(2), c.shift(2)) &  # 1st: red
        (body2 < body1 * 0.3) &                 # 2nd: small body
        _is_bullish(o, c) &                      # 3rd: green
        (c > midpoint1)                          # 3rd closes above 1st midpoint
    )
    return cond.map({True: 100, False: 0}).astype(int)


def evening_star(df: pd.DataFrame) -> pd.Series:
    """
    Evening Star (bearish) — mirror of morning star, signals top reversal.
    """
    if _HAS_TA:
        result = ta.cdl_pattern(df["Open"], df["High"], df["Low"], df["Close"], name="eveningstar")
        if result is not None:
            return result.fillna(0).astype(int)

    o, c = df["Open"], df["Close"]
    body1    = _body(o.shift(2), c.shift(2))
    body2    = _body(o.shift(1), c.shift(1))
    midpoint1 = (o.shift(2) + c.shift(2)) / 2

    cond = (
        _is_bullish(o.shift(2), c.shift(2)) &
        (body2 < body1 * 0.3) &
        _is_bearish(o, c) &
        (c < midpoint1)
    )
    return cond.map({True: -100, False: 0}).astype(int)


def three_white_soldiers(df: pd.DataFrame) -> pd.Series:
    """
    Three White Soldiers (bullish) — 3 consecutive strong green candles,
    each closing near high. Very strong bullish continuation.
    """
    o, c = df["Open"], df["Close"]
    cond = (
        _is_bullish(o, c) &
        _is_bullish(o.shift(1), c.shift(1)) &
        _is_bullish(o.shift(2), c.shift(2)) &
        (c > c.shift(1)) &
        (c.shift(1) > c.shift(2)) &
        (o > o.shift(1)) &
        (o.shift(1) > o.shift(2))
    )
    return cond.map({True: 100, False: 0}).astype(int)


def three_black_crows(df: pd.DataFrame) -> pd.Series:
    """
    Three Black Crows (bearish) — 3 consecutive strong red candles.
    Very strong bearish continuation.
    """
    o, c = df["Open"], df["Close"]
    cond = (
        _is_bearish(o, c) &
        _is_bearish(o.shift(1), c.shift(1)) &
        _is_bearish(o.shift(2), c.shift(2)) &
        (c < c.shift(1)) &
        (c.shift(1) < c.shift(2)) &
        (o < o.shift(1)) &
        (o.shift(1) < o.shift(2))
    )
    return cond.map({True: -100, False: 0}).astype(int)


# ── Composite: detect all ─────────────────────────────────────────────────────

PATTERN_FUNCTIONS = {
    "cdl_doji":               doji,
    "cdl_hammer":             hammer,
    "cdl_hanging_man":        hanging_man,
    "cdl_shooting_star":      shooting_star,
    "cdl_marubozu_bull":      marubozu_bullish,
    "cdl_marubozu_bear":      marubozu_bearish,
    "cdl_spinning_top":       spinning_top,
    "cdl_bull_engulfing":     bullish_engulfing,
    "cdl_bear_engulfing":     bearish_engulfing,
    "cdl_piercing":           piercing_line,
    "cdl_dark_cloud":         dark_cloud_cover,
    "cdl_morning_star":       morning_star,
    "cdl_evening_star":       evening_star,
    "cdl_3_soldiers":         three_white_soldiers,
    "cdl_3_crows":            three_black_crows,
}


def detect_all_patterns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Run all pattern detectors and add columns to the DataFrame.
    Returns the original df with ~15 new cdl_* columns.
    """
    out = df.copy()
    for col, fn in PATTERN_FUNCTIONS.items():
        try:
            out[col] = fn(df)
        except Exception as e:
            logger.warning(f"Pattern {col} failed: {e}")
            out[col] = 0
    return out


# ── Pattern summary ───────────────────────────────────────────────────────────

class PatternSummary:
    """
    Summarise detected patterns for a stock's latest candle.

    Usage:
        ps = PatternSummary(df_with_patterns)
        summary = ps.latest()
        # returns: {"bullish": [...], "bearish": [...], "neutral": [...], "score": int}
    """

    PATTERN_LABELS = {
        "cdl_doji":           ("Doji", "neutral"),
        "cdl_hammer":         ("Hammer", "bullish"),
        "cdl_hanging_man":    ("Hanging Man", "bearish"),
        "cdl_shooting_star":  ("Shooting Star", "bearish"),
        "cdl_marubozu_bull":  ("Bullish Marubozu", "bullish"),
        "cdl_marubozu_bear":  ("Bearish Marubozu", "bearish"),
        "cdl_spinning_top":   ("Spinning Top", "neutral"),
        "cdl_bull_engulfing": ("Bullish Engulfing", "bullish"),
        "cdl_bear_engulfing": ("Bearish Engulfing", "bearish"),
        "cdl_piercing":       ("Piercing Line", "bullish"),
        "cdl_dark_cloud":     ("Dark Cloud Cover", "bearish"),
        "cdl_morning_star":   ("Morning Star", "bullish"),
        "cdl_evening_star":   ("Evening Star", "bearish"),
        "cdl_3_soldiers":     ("Three White Soldiers", "bullish"),
        "cdl_3_crows":        ("Three Black Crows", "bearish"),
    }

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def latest(self) -> dict:
        if self.df.empty:
            return {"bullish": [], "bearish": [], "neutral": [], "score": 0}

        row = self.df.iloc[-1]
        bullish, bearish, neutral = [], [], []
        score = 0

        for col, (label, direction) in self.PATTERN_LABELS.items():
            val = row.get(col, 0)
            if val > 0:
                if direction == "bullish":
                    bullish.append(label)
                    score += 1
                else:
                    neutral.append(label)
            elif val < 0:
                if direction == "bearish":
                    bearish.append(label)
                    score -= 1
                else:
                    neutral.append(label)

        return {
            "bullish": bullish,
            "bearish": bearish,
            "neutral": neutral,
            "score":   score,
            "summary": "Bullish" if score > 0 else ("Bearish" if score < 0 else "Neutral"),
        }

    def history(self, days: int = 30) -> list[dict]:
        """Return pattern detections for the last N days."""
        results = []
        df_tail = self.df.tail(days)
        for date, row in df_tail.iterrows():
            day_patterns = []
            for col, (label, direction) in self.PATTERN_LABELS.items():
                if row.get(col, 0) != 0:
                    day_patterns.append({"pattern": label, "direction": direction, "signal": row[col]})
            if day_patterns:
                results.append({"date": str(date.date()), "patterns": day_patterns})
        return results
