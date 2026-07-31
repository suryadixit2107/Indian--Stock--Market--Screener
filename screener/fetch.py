"""
fetch.py — Data acquisition layer for IndiaScreen
Sources:
  - yfinance         : OHLCV, fundamentals (P/E, market cap, etc.)
  - NSE Bhavcopy     : EOD delivery data (free, official)
  - NSE FII/DII      : Institutional flow data (free, official)
"""

import io
import time
import zipfile
import logging
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

NSE_BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
)
NSE_DELIVERY_URL = (
    "https://archives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
)
NSE_FII_DII_URL = (
    "https://www.nseindia.com/api/fiidiiTradeReact"
)
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.nseindia.com/",
}

# Nifty 50 symbols (append .NS for yfinance)
NIFTY50_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "BHARTIARTL", "ICICIBANK",
    "INFY", "SBIN", "LICI", "HINDUNILVR", "ITC",
    "BAJFINANCE", "LT", "HCLTECH", "SUNPHARMA", "KOTAKBANK",
    "MARUTI", "TITAN", "WIPRO", "ULTRACEMCO", "ASIANPAINT",
    "AXISBANK", "NESTLEIND", "TECHM", "POWERGRID", "NTPC",
    "TATAMOTORS", "ADANIENT", "ADANIPORTS", "COALINDIA", "JSWSTEEL",
    "TATASTEEL", "DRREDDY", "BAJAJFINSV", "SBILIFE", "HDFCLIFE",
    "BRITANNIA", "EICHERMOT", "CIPLA", "HINDALCO", "GRASIM",
    "DIVISLAB", "BPCL", "ONGC", "M&M", "HEROMOTOCO",
    "APOLLOHOSP", "BAJAJ-AUTO", "TRENT", "BEL", "SHRIRAMFIN",
]

NIFTY500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
SENSEX_SYMBOLS = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "BHARTIARTL",
    "INFY", "SBIN", "BAJFINANCE", "HINDUNILVR", "LT",
    "HCLTECH", "SUNPHARMA", "MARUTI", "KOTAKBANK", "AXISBANK",
    "TITAN", "WIPRO", "ULTRACEMCO", "ASIANPAINT", "NESTLEIND",
    "NTPC", "POWERGRID", "TATAMOTORS", "ADANIENT", "COALINDIA",
    "M&M", "BAJAJFINSV", "JSWSTEEL", "TATASTEEL", "DRREDDY",
]

INDEX_MAP = {
    "nifty50": NIFTY50_SYMBOLS,
    "sensex": SENSEX_SYMBOLS,
}


# ── Symbol utilities ───────────────────────────────────────────────────────────

def to_yf_symbol(symbol: str, exchange: str = "NSE") -> str:
    """Convert NSE/BSE symbol to yfinance ticker (e.g. RELIANCE → RELIANCE.NS)."""
    suffix = ".NS" if exchange.upper() == "NSE" else ".BO"
    clean = symbol.replace(" ", "-").replace("&", "%26")
    return f"{clean}{suffix}"


def get_index_symbols(index: str = "nifty50") -> list[str]:
    """Return list of NSE symbols for a given index."""
    index = index.lower().replace(" ", "").replace("-", "")
    if index in INDEX_MAP:
        return INDEX_MAP[index]
    if index in ("nifty500", "nifty 500"):
        return _fetch_nifty500_symbols()
    logger.warning(f"Unknown index '{index}', falling back to Nifty 50.")
    return NIFTY50_SYMBOLS


def _fetch_nifty500_symbols() -> list[str]:
    """Download Nifty 500 symbol list from NSE archives."""
    try:
        df = pd.read_csv(NIFTY500_URL)
        return df["Symbol"].dropna().tolist()
    except Exception as e:
        logger.error(f"Could not fetch Nifty 500 list: {e}")
        return NIFTY50_SYMBOLS


# ── Price & fundamental data (yfinance) ───────────────────────────────────────

def fetch_ohlcv(
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
    exchange: str = "NSE",
) -> pd.DataFrame:
    """
    Fetch OHLCV data for a single symbol.

    Args:
        symbol:   NSE symbol, e.g. "RELIANCE"
        period:   yfinance period string: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y
        interval: 1d, 1wk, 1mo
        exchange: NSE or BSE

    Returns:
        DataFrame with columns: Open, High, Low, Close, Volume
    """
    import yfinance as yf

    ticker = to_yf_symbol(symbol, exchange)
    try:
        df = yf.download(ticker, period=period, interval=interval,
                         auto_adjust=True, progress=False)
        if df.empty:
            logger.warning(f"No data returned for {ticker}")
        return df
    except Exception as e:
        logger.error(f"yfinance error for {ticker}: {e}")
        return pd.DataFrame()


def fetch_fundamentals(symbol: str, exchange: str = "NSE") -> dict:
    """
    Fetch fundamental data for a symbol via yfinance.

    Returns dict with keys:
        pe_ratio, pb_ratio, market_cap, dividend_yield,
        eps, roe, debt_to_equity, revenue_growth,
        sector, industry, company_name
    """
    import yfinance as yf

    ticker = to_yf_symbol(symbol, exchange)
    try:
        info = yf.Ticker(ticker).info
        return {
            "company_name":   info.get("longName", symbol),
            "sector":         info.get("sector", "Unknown"),
            "industry":       info.get("industry", "Unknown"),
            "market_cap":     info.get("marketCap", None),          # in ₹
            "pe_ratio":       info.get("trailingPE", None),
            "pb_ratio":       info.get("priceToBook", None),
            "eps":            info.get("trailingEps", None),
            "roe":            info.get("returnOnEquity", None),      # decimal, e.g. 0.18
            "debt_to_equity": info.get("debtToEquity", None),
            "dividend_yield": info.get("dividendYield", None),       # decimal
            "revenue_growth": info.get("revenueGrowth", None),       # decimal
            "profit_margins": info.get("profitMargins", None),       # decimal
            "current_price":  info.get("currentPrice", None),
            "52w_high":       info.get("fiftyTwoWeekHigh", None),
            "52w_low":        info.get("fiftyTwoWeekLow", None),
        }
    except Exception as e:
        logger.error(f"Fundamentals error for {ticker}: {e}")
        return {}


def fetch_bulk(
    symbols: list[str],
    period: str = "1y",
    exchange: str = "NSE",
    delay: float = 0.3,
) -> dict[str, pd.DataFrame]:
    """
    Fetch OHLCV for multiple symbols with rate-limit delay.

    Returns:
        dict of {symbol: DataFrame}
    """
    import yfinance as yf

    tickers = [to_yf_symbol(s, exchange) for s in symbols]
    result = {}

    # yfinance supports bulk download — much faster than one-by-one
    try:
        raw = yf.download(
            tickers, period=period, interval="1d",
            auto_adjust=True, progress=False, group_by="ticker",
            threads=True,
        )
        for sym, ticker in zip(symbols, tickers):
            if ticker in raw.columns.get_level_values(0):
                result[sym] = raw[ticker].dropna()
            else:
                result[sym] = pd.DataFrame()
    except Exception as e:
        logger.error(f"Bulk download failed: {e}. Falling back to individual.")
        for sym in symbols:
            result[sym] = fetch_ohlcv(sym, period=period, exchange=exchange)
            time.sleep(delay)

    return result


# ── NSE Bhavcopy (delivery data) ──────────────────────────────────────────────

def fetch_bhavcopy(trade_date: Optional[date] = None) -> pd.DataFrame:
    """
    Download NSE EOD Bhavcopy CSV which includes delivery quantity.
    
    NSE publishes this file by ~7 PM on each trading day.
    URL format: sec_bhavdata_full_DDMMYYYY.csv
    
    Returns DataFrame with columns:
        SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, LAST, PREVCLOSE,
        TOTTRDQTY, TOTTRDVAL, TIMESTAMP, TOTALTRADES, ISIN,
        DELIV_QTY, DELIV_PER (delivery percentage)
    """
    if trade_date is None:
        # Try today, fall back to yesterday if not yet published
        trade_date = date.today()
        if datetime.now().hour < 20:
            trade_date -= timedelta(days=1)

    # Skip weekends
    while trade_date.weekday() >= 5:
        trade_date -= timedelta(days=1)

    date_str = trade_date.strftime("%d%m%Y")
    url = NSE_BHAVCOPY_URL.format(date=date_str)

    try:
        session = requests.Session()
        # NSE requires a cookie from the homepage first
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        time.sleep(1)

        resp = session.get(url, headers=NSE_HEADERS, timeout=15)
        resp.raise_for_status()

        df = pd.read_csv(io.StringIO(resp.text))
        df.columns = df.columns.str.strip()
        df = df[df["SERIES"] == "EQ"].copy()

        # Calculate delivery percentage if not already present
        if "DELIV_PER" not in df.columns and "DELIV_QTY" in df.columns:
            df["DELIV_PER"] = (df["DELIV_QTY"] / df["TOTTRDQTY"] * 100).round(2)

        df["SYMBOL"] = df["SYMBOL"].str.strip()
        logger.info(f"Bhavcopy fetched for {trade_date}: {len(df)} EQ records")
        return df

    except Exception as e:
        logger.error(f"Bhavcopy fetch failed for {trade_date}: {e}")
        return pd.DataFrame()


def get_delivery_pct(symbols: list[str], trade_date: Optional[date] = None) -> dict[str, float]:
    """
    Get delivery percentage for a list of symbols.
    Returns dict: {symbol: delivery_pct}
    """
    df = fetch_bhavcopy(trade_date)
    if df.empty or "DELIV_PER" not in df.columns:
        return {}
    filtered = df[df["SYMBOL"].isin(symbols)]
    return dict(zip(filtered["SYMBOL"], filtered["DELIV_PER"]))


# ── NSE FII/DII data ───────────────────────────────────────────────────────────

def fetch_fii_dii(days: int = 30) -> pd.DataFrame:
    """
    Fetch FII and DII buy/sell data from NSE.

    Returns DataFrame with columns:
        date, fii_buy, fii_sell, fii_net, dii_buy, dii_sell, dii_net
    All values in ₹ crore.
    """
    try:
        session = requests.Session()
        session.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
        time.sleep(1)

        resp = session.get(NSE_FII_DII_URL, headers=NSE_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        records = []
        for entry in data:
            try:
                records.append({
                    "date":      pd.to_datetime(entry["date"], dayfirst=True),
                    "fii_buy":   float(str(entry.get("buyValue", 0)).replace(",", "")),
                    "fii_sell":  float(str(entry.get("sellValue", 0)).replace(",", "")),
                    "fii_net":   float(str(entry.get("netValue", 0)).replace(",", "")),
                    "dii_buy":   float(str(entry.get("diiBuyValue", 0)).replace(",", "")),
                    "dii_sell":  float(str(entry.get("diiSellValue", 0)).replace(",", "")),
                    "dii_net":   float(str(entry.get("diiNetValue", 0)).replace(",", "")),
                })
            except (KeyError, ValueError):
                continue

        df = pd.DataFrame(records).sort_values("date").tail(days)
        logger.info(f"FII/DII data fetched: {len(df)} days")
        return df

    except Exception as e:
        logger.error(f"FII/DII fetch failed: {e}")
        return pd.DataFrame()
