import numpy as np
import pandas as pd
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from screener.technicals import rsi, macd, sma, ema, bollinger_bands, compute_all

def make_ohlcv(n=300, seed=42):
    np.random.seed(seed)
    close = pd.Series(1000 + np.cumsum(np.random.randn(n) * 10))
    high  = close + abs(np.random.randn(n) * 5)
    low   = close - abs(np.random.randn(n) * 5)
    vol   = pd.Series(np.random.randint(100_000, 1_000_000, n))
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol})

def test_rsi_range():
    r = rsi(make_ohlcv()["Close"])
    valid = r.dropna()
    assert (valid >= 0).all() and (valid <= 100).all()

def test_sma_correctness():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    assert abs(sma(s, 3).iloc[-1] - 4.0) < 1e-9

def test_macd_keys():
    m = macd(make_ohlcv()["Close"])
    assert set(m.keys()) == {"macd", "signal", "histogram", "crossover"}

def test_macd_crossover_values():
    m = macd(make_ohlcv()["Close"])
    assert set(m["crossover"].dropna().unique()).issubset({-1, 0, 1})

def test_bollinger_upper_gt_lower():
    bb = bollinger_bands(make_ohlcv()["Close"])
    assert (bb["upper"].dropna() >= bb["lower"].dropna()).all()

def test_compute_all_columns():
    out = compute_all(make_ohlcv())
    for col in ["rsi_14", "sma_50", "sma_200", "macd", "above_200dma"]:
        assert col in out.columns
