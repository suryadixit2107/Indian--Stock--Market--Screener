"""
backtest/fno_cost_model.py — Futures & Options cost model

WHY THIS EXISTS (DRAWBACKS.md item: "No F&O cost path"):
    The original `CostModel` in engine.py is priced for delivery (CNC)
    equity only. Applying those same rates to a futures/options strategy
    understates costs substantially, for two reasons:

      1. STT on options is charged on the *premium* for a sell order (not
         a symmetric buy/sell%), and on futures it's charged on sell-side
         turnover — structurally different from equity delivery.
      2. The July 2024 Union Budget raised STT on F&O: options STT (on
         sell) went from 0.0625% to 0.1% of premium, and futures STT (on
         sell) went from 0.0125% to 0.02% of traded value, effective
         October 2024. Anyone benchmarking an options strategy against
         pre-Oct-2024 rates is understating real costs going forward.

    Rates below are set to the post-October-2024 values. Confirm current
    rates with your broker/exchange before relying on this for real capital
    — STT rates are a matter of government notification and can change
    again in a future budget.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FnOCostModel:
    """
    Approximate cost model for NSE index/stock options and futures,
    post-October-2024 STT rates. All percentages are of notional/premium
    value unless noted.
    """
    # Options (charged on premium, sell side only)
    options_stt_sell_pct:   float = 0.001    # 0.1% of premium on sell (post Oct 2024, was 0.0625%)
    options_exchange_pct:   float = 0.0005   # NSE transaction charge (varies by moneyness in reality)
    options_sebi_pct:       float = 0.000001
    options_gst_pct:        float = 0.18     # on brokerage + exchange charges
    options_brokerage_flat: float = 20.0     # flat per-lot brokerage (Zerodha-style)

    # Futures (charged on sell-side traded value)
    futures_stt_sell_pct:   float = 0.0002   # 0.02% of sell value (post Oct 2024, was 0.0125%)
    futures_exchange_pct:   float = 0.0000173
    futures_sebi_pct:       float = 0.000001
    futures_gst_pct:        float = 0.18
    futures_brokerage_flat: float = 20.0

    def option_trade_cost(self, premium_value: float, side: str) -> float:
        """side: 'buy' or 'sell'. STT only applies on sell in the premium leg."""
        brok = min(self.options_brokerage_flat, premium_value)  # flat, capped at trade value
        exch = self.options_exchange_pct * premium_value
        sebi = self.options_sebi_pct * premium_value
        gst  = (brok + exch) * self.options_gst_pct
        stt  = self.options_stt_sell_pct * premium_value if side == "sell" else 0.0
        return brok + exch + sebi + gst + stt

    def future_trade_cost(self, notional_value: float, side: str) -> float:
        brok = min(self.futures_brokerage_flat, notional_value)
        exch = self.futures_exchange_pct * notional_value
        sebi = self.futures_sebi_pct * notional_value
        gst  = (brok + exch) * self.futures_gst_pct
        stt  = self.futures_stt_sell_pct * notional_value if side == "sell" else 0.0
        return brok + exch + sebi + gst + stt

    def round_trip_option_cost(self, entry_premium_value: float, exit_premium_value: float) -> float:
        """Buy to open, sell to close — the common retail options pattern."""
        return (
            self.option_trade_cost(entry_premium_value, "buy")
            + self.option_trade_cost(exit_premium_value, "sell")
        )

    def round_trip_future_cost(self, entry_value: float, exit_value: float) -> float:
        return (
            self.future_trade_cost(entry_value, "buy")
            + self.future_trade_cost(exit_value, "sell")
        )
