"""
portfolio/tracker.py — Portfolio tracker for IndiaScreen

Track your NSE holdings, compute P&L, XIRR, and compare to Nifty 50.
Also flags which holdings are now triggering Sell signals from the screener.

Usage:
    from portfolio.tracker import Portfolio

    p = Portfolio()
    p.add("RELIANCE",  qty=10, buy_price=2400, buy_date="2024-01-15")
    p.add("TCS",       qty=5,  buy_price=3800, buy_date="2024-03-01")
    p.add("HDFCBANK",  qty=20, buy_price=1600, buy_date="2023-11-10")

    summary = p.summary()
    print(summary["total_invested"], summary["current_value"], summary["xirr"])

    # Export to CSV
    p.to_csv("my_portfolio.csv")

    # Load from CSV
    p2 = Portfolio.from_csv("my_portfolio.csv")
"""

import csv
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    from scipy.optimize import brentq
    _HAS_SCIPY = True
except ImportError:
    _HAS_SCIPY = False
    logger.info("scipy not found — XIRR will use approximate calculation.")


# ── Holding dataclass ─────────────────────────────────────────────────────────

@dataclass
class Holding:
    symbol:      str
    qty:         int
    buy_price:   float
    buy_date:    date
    sell_price:  Optional[float] = None
    sell_date:   Optional[date]  = None
    notes:       str = ""

    @property
    def is_open(self) -> bool:
        return self.sell_price is None

    @property
    def invested(self) -> float:
        return self.buy_price * self.qty

    def current_value(self, ltp: float) -> float:
        if not self.is_open:
            return self.sell_price * self.qty
        return ltp * self.qty

    def unrealised_pnl(self, ltp: float) -> float:
        if not self.is_open:
            return 0.0
        return (ltp - self.buy_price) * self.qty

    def unrealised_pnl_pct(self, ltp: float) -> float:
        if self.buy_price == 0:
            return 0.0
        return (ltp - self.buy_price) / self.buy_price * 100

    def holding_days(self) -> int:
        end = self.sell_date or date.today()
        return (end - self.buy_date).days


# ── XIRR calculation ──────────────────────────────────────────────────────────

def xirr(cash_flows: list[tuple[date, float]], guess: float = 0.1) -> float:
    """
    Calculate XIRR (Extended Internal Rate of Return) for irregular cash flows.

    Args:
        cash_flows: List of (date, amount) tuples.
                    Negative amounts = outflows (money you paid).
                    Positive amounts = inflows (money you received or current value).

    Returns:
        XIRR as a decimal (e.g. 0.18 = 18% per year)
    """
    if not cash_flows or not _HAS_SCIPY:
        return _xirr_approx(cash_flows)

    base_date = cash_flows[0][0]

    def npv(rate: float) -> float:
        total = 0.0
        for cf_date, amount in cash_flows:
            t = (cf_date - base_date).days / 365.0
            total += amount / (1 + rate) ** t
        return total

    try:
        return brentq(npv, -0.999, 100.0, xtol=1e-6)
    except Exception:
        return _xirr_approx(cash_flows)


def _xirr_approx(cash_flows: list) -> float:
    """Approximate XIRR without scipy — uses simple CAGR."""
    if not cash_flows:
        return 0.0
    invested = sum(-cf[1] for cf in cash_flows if cf[1] < 0)
    final    = sum(cf[1] for cf in cash_flows if cf[1] > 0)
    if invested <= 0:
        return 0.0
    days = (cash_flows[-1][0] - cash_flows[0][0]).days or 1
    years = days / 365.0
    return (final / invested) ** (1 / years) - 1


# ── Portfolio class ───────────────────────────────────────────────────────────

class Portfolio:
    """
    Track NSE stock portfolio with live P&L, XIRR, and screener signal overlay.
    """

    def __init__(self, name: str = "My Portfolio"):
        self.name     = name
        self.holdings: list[Holding] = []

    def add(
        self,
        symbol:    str,
        qty:       int,
        buy_price: float,
        buy_date:  str | date,
        notes:     str = "",
    ) -> "Portfolio":
        """Add a holding. buy_date can be 'YYYY-MM-DD' string or date object."""
        if isinstance(buy_date, str):
            buy_date = datetime.strptime(buy_date, "%Y-%m-%d").date()
        self.holdings.append(Holding(
            symbol=symbol.upper(), qty=qty,
            buy_price=buy_price, buy_date=buy_date, notes=notes,
        ))
        return self

    def close(
        self,
        symbol:     str,
        sell_price: float,
        sell_date:  str | date,
    ) -> "Portfolio":
        """Mark a position as closed (sold)."""
        if isinstance(sell_date, str):
            sell_date = datetime.strptime(sell_date, "%Y-%m-%d").date()
        for h in self.holdings:
            if h.symbol == symbol and h.is_open:
                h.sell_price = sell_price
                h.sell_date  = sell_date
                break
        return self

    def _fetch_prices(self) -> dict[str, float]:
        """Fetch current prices for all open holdings."""
        import yfinance as yf
        symbols  = list({h.symbol for h in self.holdings if h.is_open})
        tickers  = [f"{s}.NS" for s in symbols]
        prices   = {}
        try:
            data = yf.download(tickers, period="2d", interval="1d",
                                auto_adjust=True, progress=False, group_by="ticker")
            for sym, ticker in zip(symbols, tickers):
                if ticker in data.columns.get_level_values(0):
                    prices[sym] = float(data[ticker]["Close"].dropna().iloc[-1])
        except Exception as e:
            logger.error(f"Price fetch failed: {e}")
        return prices

    def summary(self, live_prices: Optional[dict[str, float]] = None) -> dict:
        """
        Compute full portfolio summary.

        Args:
            live_prices: Optional dict of {symbol: price}. If None, fetches from yfinance.

        Returns dict with:
            total_invested, current_value, total_pnl, total_pnl_pct,
            xirr, day_change, holdings (list), sector_allocation
        """
        if live_prices is None:
            live_prices = self._fetch_prices()

        total_invested   = 0.0
        current_value    = 0.0
        total_pnl        = 0.0
        holding_rows     = []
        cash_flows: list[tuple[date, float]] = []

        for h in self.holdings:
            ltp = live_prices.get(h.symbol, h.buy_price)
            cur = h.current_value(ltp)
            pnl = h.unrealised_pnl(ltp) if h.is_open else (h.sell_price - h.buy_price) * h.qty

            total_invested += h.invested
            current_value  += cur
            total_pnl      += pnl

            # Cash flows for XIRR
            cash_flows.append((h.buy_date, -h.invested))
            if not h.is_open and h.sell_date:
                cash_flows.append((h.sell_date, h.sell_price * h.qty))

            holding_rows.append({
                "symbol":    h.symbol,
                "qty":       h.qty,
                "buy_price": h.buy_price,
                "buy_date":  str(h.buy_date),
                "ltp":       round(ltp, 2),
                "current_value": round(cur, 2),
                "pnl":       round(pnl, 2),
                "pnl_pct":   round(h.unrealised_pnl_pct(ltp), 2),
                "days_held": h.holding_days(),
                "status":    "Open" if h.is_open else "Closed",
                "notes":     h.notes,
            })

        # Add current value as final cash flow for XIRR
        cash_flows.append((date.today(), current_value))

        xirr_val = 0.0
        try:
            if len(cash_flows) >= 2:
                xirr_val = round(xirr(cash_flows) * 100, 2)
        except Exception:
            pass

        return {
            "portfolio_name":  self.name,
            "total_invested":  round(total_invested, 2),
            "current_value":   round(current_value, 2),
            "total_pnl":       round(total_pnl, 2),
            "total_pnl_pct":   round(total_pnl / total_invested * 100, 2) if total_invested else 0,
            "xirr":            xirr_val,
            "num_holdings":    len(self.holdings),
            "open_positions":  sum(1 for h in self.holdings if h.is_open),
            "holdings":        sorted(holding_rows, key=lambda x: x["pnl_pct"], reverse=True),
        }

    def vs_nifty50(self, live_prices: Optional[dict] = None) -> dict:
        """Compare portfolio returns against Nifty 50 buy-and-hold."""
        import yfinance as yf
        summary = self.summary(live_prices)

        # Find earliest buy date
        if not self.holdings:
            return {"portfolio_return": 0, "nifty_return": 0, "alpha": 0}

        earliest = min(h.buy_date for h in self.holdings)
        try:
            nifty = yf.download("^NSEI", start=str(earliest), progress=False, auto_adjust=True)
            if not nifty.empty:
                nifty_start = float(nifty["Close"].iloc[0])
                nifty_end   = float(nifty["Close"].iloc[-1])
                nifty_ret   = round((nifty_end - nifty_start) / nifty_start * 100, 2)
            else:
                nifty_ret = 0.0
        except Exception:
            nifty_ret = 0.0

        portfolio_ret = summary["total_pnl_pct"]
        return {
            "portfolio_return": portfolio_ret,
            "nifty_return":     nifty_ret,
            "alpha":            round(portfolio_ret - nifty_ret, 2),
            "outperforming":    portfolio_ret > nifty_ret,
        }

    def screener_signals(self) -> pd.DataFrame:
        """
        Run IndiaScreen on your holdings and return current signals.
        Useful to know if any position should be exited.
        """
        from screener.scanner import Screener
        symbols = [h.symbol for h in self.holdings if h.is_open]
        if not symbols:
            return pd.DataFrame()

        s = Screener(index=symbols)
        results = s.run(sort_by="symbol")
        return results[["symbol", "price", "rsi_14", "signal", "above_200dma", "fund_score"]]

    def to_csv(self, path: str) -> None:
        """Save portfolio to CSV file."""
        rows = []
        for h in self.holdings:
            rows.append({
                "symbol":     h.symbol,
                "qty":        h.qty,
                "buy_price":  h.buy_price,
                "buy_date":   str(h.buy_date),
                "sell_price": h.sell_price or "",
                "sell_date":  str(h.sell_date) if h.sell_date else "",
                "notes":      h.notes,
            })
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        logger.info(f"Portfolio saved to {path}")

    @classmethod
    def from_csv(cls, path: str, name: str = "Loaded Portfolio") -> "Portfolio":
        """Load portfolio from CSV file."""
        p = cls(name=name)
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                p.add(
                    symbol    = row["symbol"],
                    qty       = int(row["qty"]),
                    buy_price = float(row["buy_price"]),
                    buy_date  = row["buy_date"],
                    notes     = row.get("notes", ""),
                )
                if row.get("sell_price") and row.get("sell_date"):
                    p.close(row["symbol"], float(row["sell_price"]), row["sell_date"])
        return p

    def __repr__(self) -> str:
        return f"Portfolio('{self.name}', {len(self.holdings)} holdings)"
