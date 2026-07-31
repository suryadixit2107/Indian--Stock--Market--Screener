import numpy as np
import pandas as pd
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backtest.engine import BacktestEngine, CostModel, Trade
from backtest.strategies import get_strategy, STRATEGIES

def make_ohlcv(n=300, trend=1, seed=42):
    np.random.seed(seed)
    r = np.random.randn(n)*0.01 + 0.001*trend
    close = pd.Series(1000*np.cumprod(1+r), index=pd.date_range("2023-01-01", periods=n, freq="B"))
    high = close*(1+abs(np.random.randn(n)*0.005))
    low  = close*(1-abs(np.random.randn(n)*0.005))
    vol  = pd.Series(np.random.randint(500_000,2_000_000,n), index=close.index)
    return pd.DataFrame({"Open":close,"High":high,"Low":low,"Close":close,"Volume":vol})

def make_data(syms=("TCS","INFY"), **kw):
    return {s: make_ohlcv(**kw) for s in syms}

def test_cost_model():
    cm = CostModel()
    assert 100 < cm.total_buy_cost(100_000) < 500
    assert 100 < cm.total_sell_cost(100_000) < 500

def test_all_strategies_run():
    data = make_data()
    for name in STRATEGIES:
        r = BacktestEngine(get_strategy(name), data, initial_capital=50_000).run()
        assert r.equity_curve is not None

def test_max_drawdown_nonpositive():
    r = BacktestEngine(get_strategy("rsi_bounce"), make_data()).run()
    assert r.max_drawdown_pct <= 0

def test_win_rate_range():
    r = BacktestEngine(get_strategy("supertrend"), make_data()).run()
    assert 0 <= r.win_rate <= 100

def test_trade_pnl():
    t = Trade(symbol="X", entry_date=pd.Timestamp("2024-01-01"),
              exit_date=pd.Timestamp("2024-06-01"),
              entry_price=100.0, exit_price=120.0,
              shares=100, entry_cost=50.0, exit_cost=50.0)
    assert abs(t.pnl - 1900.0) < 0.01
    assert t.is_winner

def test_summary_keys():
    r = BacktestEngine(get_strategy("breakout"), make_data()).run()
    for k in ["total_return","cagr","sharpe","win_rate","max_drawdown"]:
        assert k in r.summary()
