"""
promoters.py — Promoter & institutional shareholding tracker for IndiaScreen

Scrapes quarterly shareholding pattern data from NSE's public filing API.
Tracks promoter buying/selling, pledge levels, and FII/DII changes.

Key signals:
  - Promoter increasing stake = strong conviction
  - Promoter pledging shares  = financial stress warning
  - FII buying + DII buying   = broad institutional interest

Usage:
    from screener.promoters import ShareholdingTracker

    tracker = ShareholdingTracker()
    data = tracker.get("RELIANCE")
    print(data["promoter_pct"], data["promoter_trend"])
"""

import time
import logging
from typing import Optional
from datetime import datetime

import requests
import pandas as pd

logger = logging.getLogger(__name__)

NSE_SHAREHOLDING_URL = (
    "https://www.nseindia.com/api/corporate-share-holdings-master?"
    "index=shareHolding&symbol={sym}&period={period}"
)
NSE_PLEDGE_URL = (
    "https://www.nseindia.com/api/corporate-share-pledgedata?"
    "symbol={sym}&series=EQ"
)
NSE_BULK_DEALS_URL = "https://www.nseindia.com/api/bulk-deal-archives?from={from_date}&to={to_date}"

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept":     "application/json, */*",
    "Referer":    "https://www.nseindia.com/",
}


def _get_session() -> requests.Session:
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        time.sleep(0.5)
    except Exception:
        pass
    return session


class ShareholdingTracker:
    """
    Tracks promoter, FII, DII, and retail shareholding over time.

    Args:
        quarters: Number of past quarters to fetch (default 4 = 1 year)
    """

    PERIOD_MAP = {1: "Quarterly", 2: "Half-Yearly", 3: "Annual"}

    def __init__(self, quarters: int = 4):
        self.quarters = quarters
        self._session: Optional[requests.Session] = None
        self._cache: dict[str, dict] = {}

    def _sess(self) -> requests.Session:
        if self._session is None:
            self._session = _get_session()
        return self._session

    def get(self, symbol: str) -> dict:
        """
        Fetch complete shareholding picture for a symbol.

        Returns dict with:
            symbol, latest_quarter,
            promoter_pct, promoter_pledge_pct,
            fii_pct, dii_pct, retail_pct,
            promoter_trend ("increasing" | "decreasing" | "stable"),
            pledge_risk ("high" | "medium" | "low" | "none"),
            history (list of quarterly dicts),
            signals (list of alert strings)
        """
        sym = symbol.upper()
        if sym in self._cache:
            return self._cache[sym]

        history = self._fetch_history(sym)
        pledge  = self._fetch_pledge(sym)

        if not history:
            return self._empty(sym)

        latest  = history[0]
        result  = self._build_result(sym, history, latest, pledge)
        self._cache[sym] = result
        return result

    def _fetch_history(self, symbol: str) -> list[dict]:
        """Fetch shareholding pattern history from NSE."""
        url = NSE_SHAREHOLDING_URL.format(sym=symbol, period="Quarterly")
        try:
            resp = self._sess().get(url, headers=NSE_HEADERS, timeout=12)
            resp.raise_for_status()
            data = resp.json()

            records = []
            for item in data[:self.quarters]:
                rec = self._parse_quarter(item)
                if rec:
                    records.append(rec)
            return records

        except Exception as e:
            logger.error(f"Shareholding fetch failed for {symbol}: {e}")
            return []

    def _parse_quarter(self, item: dict) -> Optional[dict]:
        """Parse a single quarterly shareholding record."""
        try:
            sh = item.get("shareHoldingList", [])

            # Find category percentages
            promoter_pct = 0.0
            fii_pct      = 0.0
            dii_pct      = 0.0
            retail_pct   = 0.0

            for entry in sh:
                cat = entry.get("category", "").upper()
                pct = float(entry.get("shareHoldingPercentage", 0) or 0)

                if "PROMOTER" in cat:
                    promoter_pct += pct
                elif "FII" in cat or "FOREIGN" in cat:
                    fii_pct += pct
                elif "DII" in cat or "MUTUAL FUND" in cat or "INSURANCE" in cat:
                    dii_pct += pct
                elif "PUBLIC" in cat or "RETAIL" in cat:
                    retail_pct += pct

            return {
                "quarter":      item.get("quarter", ""),
                "promoter_pct": round(promoter_pct, 2),
                "fii_pct":      round(fii_pct, 2),
                "dii_pct":      round(dii_pct, 2),
                "retail_pct":   round(retail_pct, 2),
            }
        except Exception:
            return None

    def _fetch_pledge(self, symbol: str) -> float:
        """Fetch promoter pledge percentage."""
        url = NSE_PLEDGE_URL.format(sym=symbol)
        try:
            resp = self._sess().get(url, headers=NSE_HEADERS, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            if data:
                latest = data[0]
                total_shares  = float(latest.get("totSharesHeld", 1) or 1)
                pledged       = float(latest.get("totSharesPledged", 0) or 0)
                return round(pledged / total_shares * 100, 2)
        except Exception as e:
            logger.debug(f"Pledge fetch failed for {symbol}: {e}")
        return 0.0

    def _build_result(
        self,
        symbol: str,
        history: list[dict],
        latest: dict,
        pledge_pct: float,
    ) -> dict:
        """Build the final result dict with trend analysis and signals."""
        signals = []

        # Promoter trend
        promoter_trend = "stable"
        if len(history) >= 2:
            delta = latest["promoter_pct"] - history[-1]["promoter_pct"]
            if delta > 0.5:
                promoter_trend = "increasing"
                signals.append(f"Promoter increased stake by {delta:.1f}% last quarter — BULLISH 🟢")
            elif delta < -0.5:
                promoter_trend = "decreasing"
                signals.append(f"Promoter reduced stake by {abs(delta):.1f}% last quarter — BEARISH 🔴")

        # Pledge risk
        if pledge_pct > 50:
            pledge_risk = "high"
            signals.append(f"High promoter pledge: {pledge_pct:.1f}% shares pledged — HIGH RISK 🔴")
        elif pledge_pct > 20:
            pledge_risk = "medium"
            signals.append(f"Moderate pledge: {pledge_pct:.1f}% shares pledged — WATCH 🟡")
        elif pledge_pct > 0:
            pledge_risk = "low"
        else:
            pledge_risk = "none"

        # FII trend
        if len(history) >= 2:
            fii_delta = latest["fii_pct"] - history[-1]["fii_pct"]
            if fii_delta > 1:
                signals.append(f"FII increased holding by {fii_delta:.1f}% — BULLISH 🟢")
            elif fii_delta < -1:
                signals.append(f"FII reduced holding by {abs(fii_delta):.1f}% — BEARISH 🔴")

        # Promoter majority
        if latest["promoter_pct"] > 60:
            signals.append(f"Promoter holds {latest['promoter_pct']:.1f}% — strong majority control")

        return {
            "symbol":              symbol,
            "latest_quarter":      latest.get("quarter", ""),
            "promoter_pct":        latest["promoter_pct"],
            "promoter_pledge_pct": pledge_pct,
            "fii_pct":             latest["fii_pct"],
            "dii_pct":             latest["dii_pct"],
            "retail_pct":          latest["retail_pct"],
            "promoter_trend":      promoter_trend,
            "pledge_risk":         pledge_risk,
            "history":             history,
            "signals":             signals,
            "bullish_signals":     [s for s in signals if "BULLISH" in s],
            "bearish_signals":     [s for s in signals if "BEARISH" in s or "RISK" in s],
        }

    @staticmethod
    def _empty(symbol: str) -> dict:
        return {
            "symbol": symbol, "latest_quarter": "", "promoter_pct": 0,
            "promoter_pledge_pct": 0, "fii_pct": 0, "dii_pct": 0, "retail_pct": 0,
            "promoter_trend": "unknown", "pledge_risk": "unknown",
            "history": [], "signals": [], "bullish_signals": [], "bearish_signals": [],
        }

    def filter_dataframe(
        self,
        df: pd.DataFrame,
        promoter_min: Optional[float] = None,
        promoter_trend_up: bool = False,
        pledge_max: Optional[float] = None,
        symbol_col: str = "symbol",
    ) -> pd.DataFrame:
        """
        Add shareholding columns to a screener results DataFrame and optionally filter.

        Args:
            df:                 Screener results DataFrame
            promoter_min:       Min promoter holding % (e.g. 40.0)
            promoter_trend_up:  If True, keep only stocks where promoters are buying
            pledge_max:         Max pledge % allowed (e.g. 20.0)

        Returns DataFrame with added columns:
            promoter_pct, fii_pct, dii_pct, promoter_trend, pledge_risk, promoter_signals
        """
        if df.empty:
            return df

        out = df.copy()
        for col in ["promoter_pct", "fii_pct", "dii_pct", "promoter_trend",
                    "pledge_risk", "promoter_signals"]:
            out[col] = None

        for idx, row in out.iterrows():
            sym  = row[symbol_col]
            data = self.get(sym)
            out.at[idx, "promoter_pct"]     = data["promoter_pct"]
            out.at[idx, "fii_pct"]          = data["fii_pct"]
            out.at[idx, "dii_pct"]          = data["dii_pct"]
            out.at[idx, "promoter_trend"]   = data["promoter_trend"]
            out.at[idx, "pledge_risk"]      = data["pledge_risk"]
            out.at[idx, "promoter_signals"] = "; ".join(data["signals"])
            time.sleep(0.3)

        if promoter_min is not None:
            out = out[out["promoter_pct"] >= promoter_min]
        if promoter_trend_up:
            out = out[out["promoter_trend"] == "increasing"]
        if pledge_max is not None:
            out = out[
                out.apply(lambda r: self.get(r[symbol_col]).get("promoter_pledge_pct", 0) <= pledge_max, axis=1)
            ]

        return out.reset_index(drop=True)


def get_bulk_deals(from_date: str, to_date: str) -> pd.DataFrame:
    """
    Fetch NSE bulk deal data for a date range.

    Args:
        from_date: "01-01-2025"
        to_date:   "31-01-2025"

    Returns DataFrame with bulk deal transactions (large trades that must be disclosed).
    """
    session = _get_session()
    url = NSE_BULK_DEALS_URL.format(from_date=from_date, to_date=to_date)
    try:
        resp = session.get(url, headers=NSE_HEADERS, timeout=12)
        resp.raise_for_status()
        data = resp.json()
        return pd.DataFrame(data.get("data", []))
    except Exception as e:
        logger.error(f"Bulk deals fetch failed: {e}")
        return pd.DataFrame()
