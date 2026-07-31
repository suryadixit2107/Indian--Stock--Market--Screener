"""
news.py — News sentiment filter for IndiaScreen

Fetches recent headlines for NSE stocks from Google News RSS (free, no API key)
and scores them for sentiment. Flags stocks with regulatory/fraud/results-miss news.

Usage:
    from screener.news import NewsSentiment

    ns = NewsSentiment()
    result = ns.analyse("RELIANCE", "Reliance Industries")
    print(result["sentiment"], result["score"])  # Positive, 2
    print(result["headlines"])
"""

import re
import time
import logging
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional

import feedparser
import pandas as pd

logger = logging.getLogger(__name__)

# ── Keyword scoring lists ─────────────────────────────────────────────────────

BEARISH_KEYWORDS = {
    # Regulatory / legal
    "fraud":       -3, "probe":        -3, "sebi":          -2,
    "investigation": -3, "arrested":  -3, "scam":           -3,
    "violation":   -2, "penalty":     -2, "ban":            -2,
    "delist":      -3, "suspension":  -2, "lawsuit":        -2,
    "fir":         -3, "cbi":         -3, "enforcement":    -2,
    # Financial
    "loss":        -2, "downgrade":   -2, "miss":           -2,
    "below estimates": -2, "disappoints": -2, "profit warning": -3,
    "debt":        -1, "default":     -3, "insolvency":     -3,
    "layoff":      -1, "job cuts":    -1, "restructuring":  -1,
    # Promoter
    "pledged":     -2, "promoter selling": -3, "insider selling": -2,
    "promoter stake sale": -3,
    # Market
    "crash":       -2, "fall":        -1, "decline":        -1,
    "correction":  -1, "bear":        -1, "sell-off":       -2,
}

BULLISH_KEYWORDS = {
    # Financial
    "profit":      2, "growth":       2, "record":         2,
    "beat":        2, "exceeds":      2, "strong results": 3,
    "dividend":    1, "buyback":      2, "bonus":          1,
    "expansion":   1, "acquisition":  1, "contract":       1,
    "order win":   2, "deal":         1,
    # Promoter
    "promoter buying": 3, "insider buying": 2,
    "promoter increases stake": 3,
    # Rating
    "upgrade":     2, "buy":          1, "outperform":     2,
    "target raised": 2,
    # Market
    "rally":       1, "surge":        2, "breakout":       1,
    "52-week high": 2, "all-time high": 3,
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"


def _score_headline(headline: str) -> int:
    """Score a single headline for sentiment. Returns negative/positive integer."""
    hl = headline.lower()
    score = 0
    for kw, val in BEARISH_KEYWORDS.items():
        if kw in hl:
            score += val
    for kw, val in BULLISH_KEYWORDS.items():
        if kw in hl:
            score += val
    return score


def _parse_date(entry) -> Optional[datetime]:
    """Parse publication date from a feedparser entry."""
    try:
        import email.utils
        return datetime(*email.utils.parsedate(entry.published)[:6])
    except Exception:
        return None


class NewsSentiment:
    """
    News sentiment analyser for NSE stocks.

    Args:
        lookback_days: Only consider news from the last N days (default 7)
        max_headlines: Max headlines to fetch per stock (default 15)
        delay:         Rate-limit delay between fetches in seconds
    """

    def __init__(
        self,
        lookback_days: int = 7,
        max_headlines: int = 15,
        delay: float = 1.0,
    ):
        self.lookback_days = lookback_days
        self.max_headlines = max_headlines
        self.delay         = delay
        self._cache: dict[str, dict] = {}

    def analyse(self, symbol: str, company_name: str = "") -> dict:
        """
        Analyse news sentiment for a single stock.

        Returns dict with:
            symbol, score, sentiment, signal,
            headlines (list of dicts with title, date, score),
            bearish_flags (list of negative headline titles),
            bullish_flags (list of positive headline titles)
        """
        cache_key = symbol.upper()
        if cache_key in self._cache:
            return self._cache[cache_key]

        query = company_name or symbol
        # Add India context to narrow results
        query_str = urllib.parse.quote(f"{query} NSE stock India")
        url       = GOOGLE_NEWS_RSS.format(query=query_str)

        try:
            feed = feedparser.parse(url)
            time.sleep(self.delay)
        except Exception as e:
            logger.error(f"News fetch failed for {symbol}: {e}")
            return self._empty_result(symbol)

        cutoff = datetime.now() - timedelta(days=self.lookback_days)

        headlines = []
        total_score = 0

        for entry in feed.entries[:self.max_headlines]:
            pub_date = _parse_date(entry)
            if pub_date and pub_date < cutoff:
                continue

            title = entry.get("title", "")
            score = _score_headline(title)
            total_score += score

            headlines.append({
                "title":  title,
                "date":   str(pub_date.date()) if pub_date else "Unknown",
                "score":  score,
                "url":    entry.get("link", ""),
                "source": entry.get("source", {}).get("title", ""),
            })

        bearish_flags = [h["title"] for h in headlines if h["score"] <= -2]
        bullish_flags = [h["title"] for h in headlines if h["score"] >= 2]

        if total_score >= 3:
            sentiment, signal = "Very Positive", "Strong Buy"
        elif total_score >= 1:
            sentiment, signal = "Positive", "Buy"
        elif total_score >= -1:
            sentiment, signal = "Neutral", "Hold"
        elif total_score >= -3:
            sentiment, signal = "Negative", "Sell"
        else:
            sentiment, signal = "Very Negative", "Strong Sell"

        result = {
            "symbol":        symbol.upper(),
            "company":       company_name,
            "score":         total_score,
            "sentiment":     sentiment,
            "signal":        signal,
            "headline_count":len(headlines),
            "headlines":     headlines[:10],
            "bearish_flags": bearish_flags,
            "bullish_flags": bullish_flags,
            "has_red_flag":  len(bearish_flags) > 0,
        }
        self._cache[cache_key] = result
        return result

    def analyse_bulk(
        self,
        stocks: list[tuple[str, str]],  # [(symbol, company_name), ...]
    ) -> dict[str, dict]:
        """
        Analyse news for multiple stocks.
        Returns dict: {symbol: sentiment_result}
        """
        results = {}
        for symbol, company_name in stocks:
            results[symbol] = self.analyse(symbol, company_name)
            logger.info(f"News: {symbol} → {results[symbol]['sentiment']} ({results[symbol]['score']})")
        return results

    def filter_clean(
        self,
        df: pd.DataFrame,
        symbol_col: str = "symbol",
        company_col: str = "company_name",
    ) -> pd.DataFrame:
        """
        Filter a screener results DataFrame to remove stocks with red flags.
        Adds 'news_score', 'news_sentiment', 'news_red_flag' columns.
        """
        if df.empty:
            return df

        stocks = list(zip(df[symbol_col], df.get(company_col, df[symbol_col])))
        sentiments = self.analyse_bulk(stocks)

        df = df.copy()
        df["news_score"]     = df[symbol_col].map(lambda s: sentiments.get(s, {}).get("score", 0))
        df["news_sentiment"] = df[symbol_col].map(lambda s: sentiments.get(s, {}).get("sentiment", "Neutral"))
        df["news_red_flag"]  = df[symbol_col].map(lambda s: sentiments.get(s, {}).get("has_red_flag", False))

        return df

    @staticmethod
    def _empty_result(symbol: str) -> dict:
        return {
            "symbol": symbol, "company": "", "score": 0,
            "sentiment": "Unknown", "signal": "Hold",
            "headline_count": 0, "headlines": [],
            "bearish_flags": [], "bullish_flags": [], "has_red_flag": False,
        }
