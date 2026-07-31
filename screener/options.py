"""
options.py — NSE Option Chain Analyser for IndiaScreen

Fetches live option chain data from NSE's free public API and computes:
  - PCR (Put/Call Ratio)    — sentiment indicator
  - Max Pain                — strike where most options expire worthless
  - OI buildup              — where big money is positioning
  - Change in OI            — new bets being placed or unwound
  - IV surface              — implied volatility by strike

Works for both equity options (RELIANCE, TCS, etc.) and index options (NIFTY, BANKNIFTY).

Usage:
    from screener.options import OptionChain

    oc = OptionChain("NIFTY")
    print(oc.pcr())          # 1.12 (bullish)
    print(oc.max_pain())     # 22000
    print(oc.oi_buildup())   # DataFrame of key strikes
"""

import time
import logging
from typing import Optional

import pandas as pd
import requests
import numpy as np

logger = logging.getLogger(__name__)

NSE_OPTION_EQUITY_URL = "https://www.nseindia.com/api/option-chain-equities?symbol={sym}"
NSE_OPTION_INDEX_URL  = "https://www.nseindia.com/api/option-chain-indices?symbol={sym}"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
}

INDEX_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"}


class OptionChain:
    """
    NSE Option Chain analyser.

    Args:
        symbol:  NSE symbol (e.g. "NIFTY", "RELIANCE", "TCS")
        expiry:  Expiry date string "DD-Mon-YYYY". If None, uses nearest expiry.
    """

    def __init__(self, symbol: str, expiry: Optional[str] = None):
        self.symbol = symbol.upper()
        self.expiry = expiry
        self._data: Optional[dict] = None
        self._df: Optional[pd.DataFrame] = None

    def _fetch(self) -> dict:
        """Download raw option chain from NSE."""
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        time.sleep(0.5)

        if self.symbol in INDEX_SYMBOLS:
            url = NSE_OPTION_INDEX_URL.format(sym=self.symbol)
        else:
            url = NSE_OPTION_EQUITY_URL.format(sym=self.symbol)

        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def load(self) -> "OptionChain":
        """Fetch and parse the option chain. Call this before any analysis."""
        try:
            raw = self._fetch()
            self._data = raw
            self._df   = self._parse(raw)
            logger.info(f"Option chain loaded for {self.symbol}: {len(self._df)} strikes")
        except Exception as e:
            logger.error(f"Option chain fetch failed for {self.symbol}: {e}")
            self._df = pd.DataFrame()
        return self

    def _parse(self, raw: dict) -> pd.DataFrame:
        """Convert raw NSE JSON into a tidy DataFrame."""
        records = []
        expiry_filter = self.expiry

        for item in raw.get("records", {}).get("data", []):
            expiry = item.get("expiryDate", "")
            if expiry_filter and expiry != expiry_filter:
                continue

            strike = item.get("strikePrice", 0)

            ce = item.get("CE", {})
            pe = item.get("PE", {})

            records.append({
                "expiry":          expiry,
                "strike":          strike,
                # Call side
                "ce_oi":           ce.get("openInterest", 0),
                "ce_change_oi":    ce.get("changeinOpenInterest", 0),
                "ce_volume":       ce.get("totalTradedVolume", 0),
                "ce_iv":           ce.get("impliedVolatility", 0),
                "ce_ltp":          ce.get("lastPrice", 0),
                "ce_bid":          ce.get("bidprice", 0),
                "ce_ask":          ce.get("askPrice", 0),
                # Put side
                "pe_oi":           pe.get("openInterest", 0),
                "pe_change_oi":    pe.get("changeinOpenInterest", 0),
                "pe_volume":       pe.get("totalTradedVolume", 0),
                "pe_iv":           pe.get("impliedVolatility", 0),
                "pe_ltp":          pe.get("lastPrice", 0),
                "pe_bid":          pe.get("bidprice", 0),
                "pe_ask":          pe.get("askPrice", 0),
            })

        df = pd.DataFrame(records)
        if not df.empty and expiry_filter is None:
            # Use nearest expiry by default
            nearest = df["expiry"].iloc[0] if "expiry" in df.columns else None
            if nearest:
                df = df[df["expiry"] == nearest]
        return df.sort_values("strike").reset_index(drop=True)

    @property
    def df(self) -> pd.DataFrame:
        if self._df is None:
            self.load()
        return self._df

    def underlying_price(self) -> float:
        """Get the underlying spot price from the option chain metadata."""
        if self._data:
            return self._data.get("records", {}).get("underlyingValue", 0.0)
        return 0.0

    def expiries(self) -> list[str]:
        """List all available expiry dates."""
        if self._data:
            return self._data.get("records", {}).get("expiryDates", [])
        return []

    # ── PCR (Put/Call Ratio) ──────────────────────────────────────────────────

    def pcr(self, method: str = "oi") -> float:
        """
        Put/Call Ratio.

        Args:
            method: "oi" (open interest) or "volume"

        Returns:
            PCR value. Interpretation:
              > 1.2  = strong bullish sentiment (hedgers buying puts = market going up)
              0.7–1.2 = neutral
              < 0.7  = bearish (calls outnumber puts = overconfidence)

        Note: PCR is a contrarian indicator — very high PCR can mean excessive fear (bullish),
        very low can mean excessive greed (bearish).
        """
        if self.df.empty:
            return 0.0

        if method == "oi":
            total_put_oi  = self.df["pe_oi"].sum()
            total_call_oi = self.df["ce_oi"].sum()
        else:
            total_put_oi  = self.df["pe_volume"].sum()
            total_call_oi = self.df["ce_volume"].sum()

        return round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 0.0

    def pcr_interpretation(self) -> dict:
        """PCR with human-readable interpretation."""
        pcr_val = self.pcr()
        if pcr_val > 1.5:
            sentiment = "Extreme Fear — Contrarian Bullish"
            signal    = "Strong Buy"
        elif pcr_val > 1.2:
            sentiment = "Bullish Sentiment"
            signal    = "Buy"
        elif pcr_val > 0.9:
            sentiment = "Neutral"
            signal    = "Hold"
        elif pcr_val > 0.7:
            sentiment = "Bearish Sentiment"
            signal    = "Sell"
        else:
            sentiment = "Extreme Greed — Contrarian Bearish"
            signal    = "Strong Sell"

        return {"pcr": pcr_val, "sentiment": sentiment, "signal": signal}

    # ── Max Pain ─────────────────────────────────────────────────────────────

    def max_pain(self) -> float:
        """
        Max Pain Strike — the price at which options buyers collectively lose the most money.

        Theory: Market makers (who sold the options) benefit most when price expires at max pain.
        Many traders use this as a price magnet, especially near expiry.

        Algorithm: For each strike, calculate total loss to all options buyers if price expires there.
        The strike with minimum total pain = max pain.
        """
        if self.df.empty:
            return 0.0

        strikes = self.df["strike"].values
        total_pain = []

        for target_price in strikes:
            # Pain to call buyers (calls expire worthless below strike)
            call_pain = sum(
                max(target_price - s, 0) * oi
                for s, oi in zip(self.df["strike"], self.df["ce_oi"])
            )
            # Pain to put buyers (puts expire worthless above strike)
            put_pain = sum(
                max(s - target_price, 0) * oi
                for s, oi in zip(self.df["strike"], self.df["pe_oi"])
            )
            total_pain.append(call_pain + put_pain)

        min_idx = int(np.argmin(total_pain))
        return float(strikes[min_idx])

    # ── OI Buildup ────────────────────────────────────────────────────────────

    def oi_buildup(self, top_n: int = 10) -> pd.DataFrame:
        """
        Identify key strikes with highest OI — these act as support/resistance.

        Returns DataFrame with top_n strikes ranked by combined OI,
        along with OI change (new positions added/removed).
        """
        if self.df.empty:
            return pd.DataFrame()

        df = self.df.copy()
        df["total_oi"]        = df["ce_oi"] + df["pe_oi"]
        df["total_change_oi"] = df["ce_change_oi"] + df["pe_change_oi"]
        df["pcr_strike"]      = (df["pe_oi"] / df["ce_oi"].replace(0, np.nan)).round(2)

        top = df.nlargest(top_n, "total_oi")[
            ["strike", "ce_oi", "pe_oi", "total_oi",
             "ce_change_oi", "pe_change_oi", "total_change_oi",
             "ce_iv", "pe_iv", "pcr_strike"]
        ].reset_index(drop=True)

        return top

    def key_levels(self) -> dict:
        """
        Compute key support and resistance levels from OI.

        Support = strike with highest PE OI (puts act as floor — market makers defend it)
        Resistance = strike with highest CE OI (calls act as ceiling)
        """
        if self.df.empty:
            return {}

        max_pe_idx = self.df["pe_oi"].idxmax()
        max_ce_idx = self.df["ce_oi"].idxmax()
        spot       = self.underlying_price()

        return {
            "spot":       spot,
            "support":    float(self.df.loc[max_pe_idx, "strike"]),
            "resistance": float(self.df.loc[max_ce_idx, "strike"]),
            "max_pain":   self.max_pain(),
            "pcr":        self.pcr(),
            "pcr_signal": self.pcr_interpretation()["signal"],
        }

    def iv_surface(self) -> pd.DataFrame:
        """
        Implied Volatility surface — IV by strike for both calls and puts.
        Useful for detecting IV skew (options market pricing in asymmetric risk).
        """
        if self.df.empty:
            return pd.DataFrame()

        return self.df[["strike", "ce_iv", "pe_iv"]].copy()

    def summary(self) -> dict:
        """Full option chain summary in one call."""
        return {
            "symbol":      self.symbol,
            "spot":        self.underlying_price(),
            "expiry":      self.expiry or (self.expiries()[0] if self.expiries() else "N/A"),
            "pcr":         self.pcr_interpretation(),
            "max_pain":    self.max_pain(),
            "key_levels":  self.key_levels(),
            "oi_buildup":  self.oi_buildup(5).to_dict(orient="records"),
        }
