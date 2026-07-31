"""
sectors.py — Sector Rotation Tracker for IndiaScreen

Tracks performance of all NSE sector indices and identifies:
  - Which sectors are leading (outperforming Nifty 50)
  - Which are lagging (underperforming)
  - Rotation signals (money moving between sectors)

Based on the Relative Rotation Graph (RRG) concept used by institutional traders.

Usage:
    from screener.sectors import SectorTracker

    st = SectorTracker()
    df = st.performance_table()
    print(df)  # 1W, 1M, 3M, 6M, 1Y returns for all sectors

    rotation = st.rotation_signals()
    print(rotation["leading"])   # Sectors gaining momentum
    print(rotation["lagging"])   # Sectors losing momentum
"""

import logging
from typing import Optional

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# ── NSE Sector Index Tickers (yfinance format) ────────────────────────────────
SECTOR_INDICES = {
    "Nifty 50":        "^NSEI",
    "Nifty Bank":      "^NSEBANK",
    "Nifty IT":        "^CNXIT",
    "Nifty Pharma":    "^CNXPHARMA",
    "Nifty FMCG":      "^CNXFMCG",
    "Nifty Auto":      "^CNXAUTO",
    "Nifty Metal":     "^CNXMETAL",
    "Nifty Energy":    "^CNXENERGY",
    "Nifty Infra":     "^CNXINFRA",
    "Nifty Realty":    "^CNXREALTY",
    "Nifty Media":     "^CNXMEDIA",
    "Nifty PSU Bank":  "^CNXPSUBANK",
    "Nifty Midcap 100":"^CNXMIDCAP",
    "Nifty Smallcap":  "^CNXSC",
}

PERIOD_DAYS = {
    "1W": 5,
    "1M": 21,
    "3M": 63,
    "6M": 126,
    "1Y": 252,
}


class SectorTracker:
    """
    Tracks and compares NSE sector index performance.

    Args:
        period: Data download period for yfinance (default "1y")
    """

    def __init__(self, period: str = "1y"):
        self.period = period
        self._data: Optional[dict[str, pd.DataFrame]] = None

    def _load(self):
        """Download sector index data from yfinance."""
        import yfinance as yf
        tickers = list(SECTOR_INDICES.values())
        try:
            raw = yf.download(
                tickers, period=self.period, interval="1d",
                auto_adjust=True, progress=False, group_by="ticker",
            )
            self._data = {}
            for name, ticker in SECTOR_INDICES.items():
                if ticker in raw.columns.get_level_values(0):
                    df = raw[ticker][["Close"]].dropna()
                    df.columns = [name]
                    self._data[name] = df
            logger.info(f"Sector data loaded: {len(self._data)} indices")
        except Exception as e:
            logger.error(f"Sector data fetch failed: {e}")
            self._data = {}

    def _returns(self, name: str, days: int) -> Optional[float]:
        """Calculate N-day return for a sector index."""
        if self._data is None:
            self._load()
        df = self._data.get(name)
        if df is None or len(df) < days + 1:
            return None
        series = df.iloc[:, 0]
        ret    = (series.iloc[-1] - series.iloc[-days]) / series.iloc[-days] * 100
        return round(float(ret), 2)

    def performance_table(self) -> pd.DataFrame:
        """
        Build a table of returns for all sectors across multiple timeframes.

        Returns DataFrame with columns: Sector, 1W%, 1M%, 3M%, 6M%, 1Y%
        Sectors sorted by 1M performance.
        """
        if self._data is None:
            self._load()

        rows = []
        for name in SECTOR_INDICES:
            row = {"Sector": name}
            for period_label, days in PERIOD_DAYS.items():
                row[period_label] = self._returns(name, days)
            rows.append(row)

        df = pd.DataFrame(rows)
        df = df.sort_values("1M", ascending=False).reset_index(drop=True)
        return df

    def vs_nifty50(self, period_label: str = "1M") -> pd.DataFrame:
        """
        Compare each sector's return vs Nifty 50 for a given period.
        Positive = outperforming, Negative = underperforming.
        """
        perf = self.performance_table()
        days = PERIOD_DAYS.get(period_label, 21)

        nifty_ret = self._returns("Nifty 50", days) or 0
        perf["vs_Nifty50"] = (perf[period_label] - nifty_ret).round(2)
        perf["status"]     = perf["vs_Nifty50"].apply(
            lambda x: "Leading" if x > 1 else ("Lagging" if x < -1 else "Inline")
        )
        return perf[["Sector", period_label, "vs_Nifty50", "status"]]

    def rotation_signals(self) -> dict:
        """
        Identify rotation signals — sectors gaining or losing momentum.

        Compares 1-month vs 3-month performance:
          - Improving (1M > 3M/3): gaining momentum → rotation IN
          - Deteriorating (1M < 3M/3): losing momentum → rotation OUT

        Returns dict with keys: leading, improving, lagging, deteriorating
        """
        if self._data is None:
            self._load()

        leading, improving, lagging, deteriorating = [], [], [], []

        nifty_1m = self._returns("Nifty 50", 21) or 0
        nifty_3m = self._returns("Nifty 50", 63) or 0

        for name in SECTOR_INDICES:
            if name == "Nifty 50":
                continue
            ret_1m = self._returns(name, 21)
            ret_3m = self._returns(name, 63)

            if ret_1m is None or ret_3m is None:
                continue

            avg_3m = ret_3m / 3  # Monthly equivalent of 3M return
            above_nifty = ret_1m > nifty_1m

            if above_nifty:
                leading.append({"sector": name, "1M": ret_1m, "vs_nifty": round(ret_1m - nifty_1m, 2)})
            else:
                lagging.append({"sector": name, "1M": ret_1m, "vs_nifty": round(ret_1m - nifty_1m, 2)})

            # Momentum direction
            if ret_1m > avg_3m + 0.5:
                improving.append({"sector": name, "1M": ret_1m, "3M_monthly_avg": round(avg_3m, 2),
                                   "momentum_gain": round(ret_1m - avg_3m, 2)})
            elif ret_1m < avg_3m - 0.5:
                deteriorating.append({"sector": name, "1M": ret_1m, "3M_monthly_avg": round(avg_3m, 2),
                                       "momentum_loss": round(avg_3m - ret_1m, 2)})

        return {
            "leading":      sorted(leading,      key=lambda x: x["1M"], reverse=True),
            "lagging":      sorted(lagging,       key=lambda x: x["1M"]),
            "improving":    sorted(improving,     key=lambda x: x["momentum_gain"], reverse=True),
            "deteriorating":sorted(deteriorating, key=lambda x: x["momentum_loss"], reverse=True),
            "nifty_50_1m":  round(nifty_1m, 2),
        }

    def heatmap_data(self) -> pd.DataFrame:
        """
        Returns a pivot-style DataFrame ready for a Streamlit/Plotly heatmap.
        Rows = sectors, Columns = time periods, Values = % returns.
        """
        perf = self.performance_table()
        return perf.set_index("Sector")[list(PERIOD_DAYS.keys())]

    def top_sectors(self, period: str = "1M", n: int = 3) -> list[str]:
        """Return top N sector names by return for a given period."""
        perf = self.performance_table()
        return perf.nlargest(n, period)["Sector"].tolist()

    def bottom_sectors(self, period: str = "1M", n: int = 3) -> list[str]:
        """Return bottom N sector names by return for a given period."""
        perf = self.performance_table()
        return perf.nsmallest(n, period)["Sector"].tolist()
