"""
engine.py — Event-driven backtesting engine for IndiaScreen

Simulates trading strategies on historical OHLCV data with:
  - Realistic execution (next-day open price fills)
  - Brokerage + STT cost model (Indian market rates)
  - Performance metrics: CAGR, Sharpe, Sortino, Max Drawdown, Win Rate
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ── Cost model (Indian market) ─────────────────────────────────────────────────

@dataclass
class CostModel:
    """
    Realistic transaction cost model for NSE equity trading.
    Rates below are for delivery (CNC) equity only.

    v3 change (see /DRAWBACKS.md, "Backtests overstate returns"): v0.2 had
    no slippage term at all, so every backtest filled at the exact printed
    close/open — fine for liquid Nifty 50 names, materially optimistic for
    mid/small-caps or breakout strategies that specifically trigger on
    high-volume, high-volatility bars (exactly when real fills slip most,
    and exactly when NSE circuit filters can prevent a fill at all).
    `slippage_pct` and `circuit_limit_pct` are opt-in — set to 0 to
    reproduce v0.2 behaviour for comparison.
    """
    brokerage_pct:   float = 0.0003   # 0.03% or ₹20 cap (Zerodha flat)
    stt_buy_pct:     float = 0.001    # 0.1% on buy (delivery)
    stt_sell_pct:    float = 0.001    # 0.1% on sell (delivery)
    exchange_pct:    float = 0.0000345 # NSE transaction charges
    sebi_pct:        float = 0.000001  # SEBI turnover fee
    stamp_duty_pct:  float = 0.00015   # 0.015% on buy
    gst_on_brokerage: float = 0.18    # 18% GST on brokerage
    slippage_pct:    float = 0.0005   # default: 0.05% adverse fill vs signal price
    circuit_limit_pct: Optional[float] = 0.20  # flag fills implying >20% single-day move

    def apply_slippage(self, price: float, side: str) -> float:
        """side: 'buy' slips price up, 'sell' slips price down."""
        if side == "buy":
            return price * (1 + self.slippage_pct)
        return price * (1 - self.slippage_pct)

    def total_buy_cost(self, value: float) -> float:
        brok  = min(self.brokerage_pct * value, 20)
        gst   = brok * self.gst_on_brokerage
        return brok + gst + self.stt_buy_pct * value + \
               self.exchange_pct * value + self.sebi_pct * value + \
               self.stamp_duty_pct * value

    def total_sell_cost(self, value: float) -> float:
        brok = min(self.brokerage_pct * value, 20)
        gst  = brok * self.gst_on_brokerage
        return brok + gst + self.stt_sell_pct * value + \
               self.exchange_pct * value + self.sebi_pct * value


# ── Trade record ──────────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol:      str
    entry_date:  pd.Timestamp
    exit_date:   Optional[pd.Timestamp]
    entry_price: float
    exit_price:  Optional[float]
    shares:      int
    entry_cost:  float
    exit_cost:   float = 0.0
    signal:      str = ""

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        gross = (self.exit_price - self.entry_price) * self.shares
        return gross - self.entry_cost - self.exit_cost

    @property
    def pnl_pct(self) -> float:
        invested = self.entry_price * self.shares + self.entry_cost
        return self.pnl / invested * 100 if invested else 0.0

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0


# ── Backtest result ───────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    strategy_name:  str
    initial_capital: float
    final_value:    float
    trades:         list[Trade]
    equity_curve:   pd.Series   # daily portfolio value
    benchmark:      pd.Series   # Nifty 50 equivalent
    survivorship_bias_note: Optional[str] = None  # set by BacktestEngine if checked

    @property
    def total_return_pct(self) -> float:
        return (self.final_value - self.initial_capital) / self.initial_capital * 100

    @property
    def cagr(self) -> float:
        n_years = len(self.equity_curve) / 252
        if n_years <= 0:
            return 0.0
        return ((self.final_value / self.initial_capital) ** (1 / n_years) - 1) * 100

    @property
    def sharpe_ratio(self) -> float:
        daily_ret = self.equity_curve.pct_change().dropna()
        if daily_ret.std() == 0:
            return 0.0
        return (daily_ret.mean() / daily_ret.std()) * np.sqrt(252)

    @property
    def sortino_ratio(self) -> float:
        daily_ret = self.equity_curve.pct_change().dropna()
        downside  = daily_ret[daily_ret < 0].std()
        if downside == 0:
            return 0.0
        return (daily_ret.mean() / downside) * np.sqrt(252)

    @property
    def max_drawdown_pct(self) -> float:
        roll_max = self.equity_curve.cummax()
        drawdown = (self.equity_curve - roll_max) / roll_max * 100
        return drawdown.min()

    @property
    def win_rate(self) -> float:
        closed = [t for t in self.trades if t.exit_price is not None]
        if not closed:
            return 0.0
        return sum(1 for t in closed if t.is_winner) / len(closed) * 100

    @property
    def avg_win_pct(self) -> float:
        winners = [t.pnl_pct for t in self.trades if t.exit_price and t.pnl > 0]
        return np.mean(winners) if winners else 0.0

    @property
    def avg_loss_pct(self) -> float:
        losers = [t.pnl_pct for t in self.trades if t.exit_price and t.pnl <= 0]
        return np.mean(losers) if losers else 0.0

    @property
    def profit_factor(self) -> float:
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss   = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        return gross_profit / gross_loss if gross_loss else float("inf")

    def summary(self) -> dict:
        return {
            "strategy":       self.strategy_name,
            "total_return":   f"{self.total_return_pct:.1f}%",
            "cagr":           f"{self.cagr:.1f}%",
            "sharpe":         f"{self.sharpe_ratio:.2f}",
            "sortino":        f"{self.sortino_ratio:.2f}",
            "max_drawdown":   f"{self.max_drawdown_pct:.1f}%",
            "win_rate":       f"{self.win_rate:.1f}%",
            "profit_factor":  f"{self.profit_factor:.2f}",
            "avg_win":        f"{self.avg_win_pct:.1f}%",
            "avg_loss":       f"{self.avg_loss_pct:.1f}%",
            "total_trades":   len(self.trades),
            "initial_capital": f"₹{self.initial_capital:,.0f}",
            "final_value":    f"₹{self.final_value:,.0f}",
        }

    def print_summary(self):
        print("\n" + "=" * 50)
        for k, v in self.summary().items():
            print(f"  {k:<20} {v}")
        print("=" * 50 + "\n")


# ── Core backtesting engine ───────────────────────────────────────────────────

class BacktestEngine:
    """
    Event-driven backtesting engine.

    Args:
        strategy:        A strategy object (subclass of BaseStrategy)
        data:            Dict of {symbol: OHLCV DataFrame}
        initial_capital: Starting capital in ₹
        position_size:   Fraction of portfolio per trade (e.g. 0.1 = 10%)
        max_positions:   Max concurrent open positions
        cost_model:      CostModel instance
    """

    def __init__(
        self,
        strategy,
        data: dict[str, pd.DataFrame],
        initial_capital: float = 100_000,
        position_size:   float = 0.1,
        max_positions:   int   = 10,
        cost_model:      Optional[CostModel] = None,
        index_name:      Optional[str] = None,
    ):
        self.strategy       = strategy
        self.data           = data
        self.initial_capital = initial_capital
        self.position_size  = position_size
        self.max_positions  = max_positions
        self.cost_model     = cost_model or CostModel()
        self.index_name     = index_name

    def run(self) -> BacktestResult:
        """Execute the backtest and return a BacktestResult."""
        # Build a unified timeline across all symbols
        all_dates = sorted(set(
            d for df in self.data.values() for d in df.index
        ))

        cash      = self.initial_capital
        positions: dict[str, Trade] = {}  # symbol → open Trade
        closed_trades: list[Trade]  = []
        equity_history = []

        # Pre-compute indicators for each symbol
        from ..screener.technicals import compute_all
        indicators = {}
        for sym, df in self.data.items():
            if len(df) >= 30:
                try:
                    indicators[sym] = compute_all(df)
                except Exception as e:
                    logger.warning(f"Indicator error for {sym}: {e}")

        for date in all_dates:
            # ── Check exit signals for open positions ──
            for sym in list(positions.keys()):
                df = indicators.get(sym)
                if df is None or date not in df.index:
                    continue
                row     = df.loc[date]
                trade   = positions[sym]
                exit_price = float(row["Open"]) if "Open" in row else None

                should_exit = self.strategy.should_exit(row, trade, df)
                if should_exit and exit_price:
                    exit_price    = self.cost_model.apply_slippage(exit_price, "sell")
                    exit_value    = exit_price * trade.shares
                    trade.exit_date  = date
                    trade.exit_price = exit_price
                    trade.exit_cost  = self.cost_model.total_sell_cost(exit_value)
                    cash += exit_value - trade.exit_cost
                    closed_trades.append(trade)
                    del positions[sym]
                    logger.debug(f"SELL {sym} @ ₹{exit_price:.2f} | P&L: ₹{trade.pnl:.0f} ({trade.pnl_pct:.1f}%)")

            # ── Check entry signals ──
            if len(positions) < self.max_positions:
                for sym, df in indicators.items():
                    if sym in positions or date not in df.index:
                        continue
                    row = df.loc[date]

                    # Next bar execution (avoid lookahead)
                    next_idx = df.index.get_loc(date) + 1
                    if next_idx >= len(df):
                        continue
                    entry_price = float(df.iloc[next_idx]["Open"])
                    entry_price = self.cost_model.apply_slippage(entry_price, "buy")

                    should_enter = self.strategy.should_enter(row, df)
                    if should_enter:
                        alloc       = cash * self.position_size
                        shares      = int(alloc / entry_price)
                        if shares < 1:
                            continue
                        entry_value = entry_price * shares
                        entry_cost  = self.cost_model.total_buy_cost(entry_value)
                        if entry_value + entry_cost > cash:
                            continue

                        cash -= entry_value + entry_cost
                        t = Trade(
                            symbol=sym,
                            entry_date=date,
                            exit_date=None,
                            entry_price=entry_price,
                            exit_price=None,
                            shares=shares,
                            entry_cost=entry_cost,
                            signal=self.strategy.name,
                        )
                        positions[sym] = t
                        logger.debug(f"BUY  {sym} @ ₹{entry_price:.2f} | {shares} shares")

                    if len(positions) >= self.max_positions:
                        break

            # ── Mark-to-market portfolio value ──
            open_value = sum(
                float(indicators[sym].loc[date]["Close"]) * t.shares
                for sym, t in positions.items()
                if sym in indicators and date in indicators[sym].index
            )
            equity_history.append({"date": date, "value": cash + open_value})

        # Close any remaining open positions at last price
        for sym, trade in positions.items():
            df = indicators.get(sym)
            if df is not None and not df.empty:
                last_price = float(df.iloc[-1]["Close"])
                exit_value = last_price * trade.shares
                trade.exit_date  = df.index[-1]
                trade.exit_price = last_price
                trade.exit_cost  = self.cost_model.total_sell_cost(exit_value)
                cash += exit_value - trade.exit_cost
                closed_trades.append(trade)

        equity_curve = pd.DataFrame(equity_history).set_index("date")["value"]
        benchmark    = _build_benchmark(self.data, self.initial_capital)

        return BacktestResult(
            strategy_name=self.strategy.name,
            initial_capital=self.initial_capital,
            final_value=cash,
            trades=closed_trades,
            equity_curve=equity_curve,
            benchmark=benchmark,
            survivorship_bias_note=self._survivorship_note(all_dates),
        )

    def _survivorship_note(self, all_dates: list) -> Optional[str]:
        """
        If constructed with `index_name`, check screener.index_history for
        point-in-time constituent coverage over the backtest window and
        return a human-readable warning if coverage is incomplete.
        See DRAWBACKS.md, "Survivorship bias in backtests."
        """
        if not self.index_name or not all_dates:
            return None
        try:
            from ..screener.index_history import IndexHistory
            ih = IndexHistory()
            start_date = min(d.date() if hasattr(d, "date") else d for d in all_dates)
            result = ih.constituents_asof(self.index_name, start_date)
            if not result.is_point_in_time:
                return (
                    f"SURVIVORSHIP BIAS: backtest starts {start_date}, but the "
                    f"earliest recorded '{self.index_name}' constituent snapshot "
                    f"is {result.as_of}. Stocks that left the index before "
                    f"{result.as_of} are excluded, which likely overstates "
                    f"these results. Import point-in-time data via "
                    f"IndexHistory.import_historical_csv() to remove this warning."
                )
            return None
        except ValueError:
            return (
                f"SURVIVORSHIP BIAS: no constituent snapshots recorded for "
                f"'{self.index_name}' — this backtest silently used today's "
                f"list for the entire historical window. Call "
                f"IndexHistory.record_snapshot() going forward, or import "
                f"historical data, to check for this."
            )


def _build_benchmark(data: dict, capital: float) -> pd.Series:
    """Buy-and-hold the equal-weighted portfolio as benchmark."""
    if not data:
        return pd.Series()
    all_closes = []
    for df in data.values():
        if not df.empty:
            all_closes.append(df["Close"])
    if not all_closes:
        return pd.Series()
    avg_close = pd.concat(all_closes, axis=1).mean(axis=1).dropna()
    initial   = float(avg_close.iloc[0])
    return (avg_close / initial) * capital
