"""
live.py — Real-time NSE price feed (no API key required)

Uses NSE's own website endpoints — the same calls their web app makes.
Rate-limited to stay respectful. Works during market hours (9:15 AM – 3:30 PM IST).

Usage:
    from screener.live import get_live_quote, get_live_quotes_bulk, is_market_open

    quote = get_live_quote("RELIANCE")
    print(quote["ltp"], quote["change_pct"])
"""

import time
import logging
from datetime import datetime, time as dtime
from typing import Optional
import pytz

import requests

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN  = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.nseindia.com/",
    "Connection":      "keep-alive",
}

NSE_QUOTE_URL       = "https://www.nseindia.com/api/quote-equity?symbol={sym}"
NSE_MARKET_STATUS   = "https://www.nseindia.com/api/marketStatus"
NSE_GAINERS_URL     = "https://www.nseindia.com/api/live-analysis-variations?index=gainers"
NSE_LOSERS_URL      = "https://www.nseindia.com/api/live-analysis-variations?index=loosers"
NSE_MOST_ACTIVE_URL = "https://www.nseindia.com/api/live-analysis-variations?index=active"


def _get_session() -> requests.Session:
    """Create a session with NSE cookies (required for their API)."""
    session = requests.Session()
    try:
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        time.sleep(0.5)
    except Exception as e:
        logger.warning(f"NSE session init failed: {e}")
    return session


def is_market_open() -> bool:
    """Return True if NSE market is currently open."""
    now = datetime.now(IST).time()
    today = datetime.now(IST).weekday()
    if today >= 5:  # Saturday / Sunday
        return False
    return MARKET_OPEN <= now <= MARKET_CLOSE


def get_live_quote(symbol: str, session: Optional[requests.Session] = None) -> dict:
    """
    Fetch live quote for a single NSE symbol.

    Returns dict with keys:
        symbol, company_name, ltp (last traded price),
        open, high, low, prev_close,
        change, change_pct,
        volume, total_buy_qty, total_sell_qty,
        upper_circuit, lower_circuit,
        week_52_high, week_52_low,
        timestamp
    """
    if session is None:
        session = _get_session()

    url = NSE_QUOTE_URL.format(sym=symbol.upper().replace("&", "%26"))
    try:
        resp = session.get(url, headers=NSE_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        info  = data.get("info", {})
        price = data.get("priceInfo", {})
        limit = data.get("priceInfo", {}).get("intraDayHighLow", {})
        week  = data.get("priceInfo", {}).get("weekHighLow", {})

        return {
            "symbol":         symbol.upper(),
            "company_name":   info.get("companyName", symbol),
            "ltp":            price.get("lastPrice", 0),
            "open":           price.get("open", 0),
            "high":           price.get("intraDayHighLow", {}).get("max", 0),
            "low":            price.get("intraDayHighLow", {}).get("min", 0),
            "prev_close":     price.get("previousClose", 0),
            "change":         price.get("change", 0),
            "change_pct":     price.get("pChange", 0),
            "volume":         data.get("marketDeptOrderBook", {}).get("totalTradedVolume", 0),
            "total_buy_qty":  data.get("marketDeptOrderBook", {}).get("totalBuyQuantity", 0),
            "total_sell_qty": data.get("marketDeptOrderBook", {}).get("totalSellQuantity", 0),
            "upper_circuit":  price.get("upperCP", None),
            "lower_circuit":  price.get("lowerCP", None),
            "week_52_high":   week.get("max", None),
            "week_52_low":    week.get("min", None),
            "timestamp":      datetime.now(IST).isoformat(),
        }
    except Exception as e:
        logger.error(f"Live quote failed for {symbol}: {e}")
        return {"symbol": symbol, "error": str(e)}


def get_live_quotes_bulk(
    symbols: list[str],
    delay: float = 0.4,
) -> dict[str, dict]:
    """
    Fetch live quotes for multiple symbols with rate-limit delay.

    Returns dict: {symbol: quote_dict}
    """
    session = _get_session()
    results = {}
    for sym in symbols:
        quote = get_live_quote(sym, session)
        results[sym] = quote
        time.sleep(delay)
    return results


def get_market_status() -> dict:
    """
    Check if NSE market is open and get market-wide stats.
    Returns dict with market status information.
    """
    session = _get_session()
    try:
        resp = session.get(NSE_MARKET_STATUS, headers=NSE_HEADERS, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Market status fetch failed: {e}")
        return {"isOpen": is_market_open(), "error": str(e)}


def get_top_gainers(session: Optional[requests.Session] = None) -> list[dict]:
    """Fetch top Nifty gainers for the day."""
    if session is None:
        session = _get_session()
    try:
        resp = session.get(NSE_GAINERS_URL, headers=NSE_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("NIFTY", {}).get("data", [])
    except Exception as e:
        logger.error(f"Gainers fetch failed: {e}")
        return []


def get_top_losers(session: Optional[requests.Session] = None) -> list[dict]:
    """Fetch top Nifty losers for the day."""
    if session is None:
        session = _get_session()
    try:
        resp = session.get(NSE_LOSERS_URL, headers=NSE_HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data.get("NIFTY", {}).get("data", [])
    except Exception as e:
        logger.error(f"Losers fetch failed: {e}")
        return []
