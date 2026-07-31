"""
screener/nl_screener.py — Natural Language Screener for IndiaScreen

Converts plain English queries into structured screener filters using Claude API.
No need to remember CLI flags — just describe what you want.

Examples:
    "Show me profitable banking stocks with low debt that are oversold"
    "Find Nifty 500 IT companies with P/E below 20 and strong growth"
    "Midcap pharma stocks above 200 DMA with high delivery"
    "Dividend-paying large caps with ROE above 15%"

Usage:
    from screener.nl_screener import NLScreener

    nls = NLScreener()
    results = nls.run("Show me oversold banking stocks with low P/E")
    print(results[["symbol", "price", "rsi_14", "signal"]])

    # Or just parse the filters without running
    filters = nls.parse("IT stocks with RSI below 40 and P/E under 25")
    print(filters)  # {'sectors': ['IT & Technology'], 'rsi_max': 40, 'pe_max': 25}
"""

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Filter schema for Claude to populate ────────────────────────────────────

FILTER_SCHEMA = {
    "index":           "string: one of nifty50 | nifty500 | sensex (default nifty50)",
    "rsi_min":         "number or null: minimum RSI value",
    "rsi_max":         "number or null: maximum RSI value",
    "pe_min":          "number or null: minimum P/E ratio",
    "pe_max":          "number or null: maximum P/E ratio",
    "pb_max":          "number or null: maximum P/B ratio",
    "roe_min":         "number or null: minimum ROE as decimal (e.g. 0.15 for 15%)",
    "debt_equity_max": "number or null: maximum debt/equity ratio",
    "market_cap_min_cr": "number or null: minimum market cap in crore",
    "delivery_min":    "number or null: minimum delivery percentage (0-100)",
    "above_200dma":    "boolean or null: true = only stocks above 200-day moving average",
    "macd_bullish":    "boolean or null: true = only bullish MACD crossover stocks",
    "supertrend_up":   "boolean or null: true = only uptrend Supertrend stocks",
    "near_52w_high":   "boolean or null: true = only stocks near 52-week high",
    "fundamental_preset": "string or null: one of value | growth | quality | dividend",
    "sectors":         "list of strings or null: e.g. ['Banking & Finance', 'IT & Technology']",
    "signals":         "list of strings or null: e.g. ['Buy', 'Strong Buy']",
    "top_n":           "integer or null: maximum number of results",
    "sort_by":         "string: one of fund_score | rsi_14 | pe_ratio | market_cap_cr (default fund_score)",
}

VALID_SECTORS = [
    "Banking & Finance", "IT & Technology", "Pharma & Healthcare",
    "FMCG", "Auto", "Energy & Power", "Infrastructure", "Telecom",
    "Metals & Mining", "Consumer", "Chemicals", "Realty",
]

SYSTEM_PROMPT = f"""You are a stock screener filter parser for Indian NSE/BSE stocks.

The user will describe what stocks they want to find in plain English.
Parse their description and return ONLY a valid JSON object with these fields:

{json.dumps(FILTER_SCHEMA, indent=2)}

Rules:
1. Return ONLY JSON — no explanation, no markdown, no code blocks
2. Use null for any filter not mentioned or implied
3. "oversold" → rsi_max: 35
4. "overbought" → rsi_min: 65
5. "cheap" or "value" → pe_max: 20, fundamental_preset: "value"
6. "growth" stocks → fundamental_preset: "growth", revenue_growth_min: 0.15
7. "quality" → fundamental_preset: "quality"
8. "dividend" → fundamental_preset: "dividend"
9. "large cap" → market_cap_min_cr: 20000
10. "midcap" → market_cap_min_cr: 5000, market_cap_max: 20000
11. "uptrend" or "trending up" → above_200dma: true, supertrend_up: true
12. "low debt" → debt_equity_max: 0.5
13. "strong ROE" or "profitable" → roe_min: 0.15
14. "high delivery" → delivery_min: 50
15. Sector keywords: "banking/bank/HDFC/ICICI" → "Banking & Finance",
    "IT/tech/software/TCS/Infosys" → "IT & Technology",
    "pharma/healthcare/medicine" → "Pharma & Healthcare",
    "FMCG/consumer goods/HUL" → "FMCG",
    "auto/automobile/cars" → "Auto",
    "energy/power/oil/gas" → "Energy & Power"

Valid sectors: {VALID_SECTORS}
"""


def _call_claude(query: str, api_key: str) -> dict:
    """Call Claude API to parse the natural language query."""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model      = "claude-haiku-4-5-20251001",
            max_tokens = 500,
            system     = SYSTEM_PROMPT,
            messages   = [{"role": "user", "content": query}],
        )
        raw = message.content[0].text.strip()
        # Strip any accidental markdown fences
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except ImportError:
        logger.error("anthropic package not installed. Run: pip install anthropic")
        return {}
    except json.JSONDecodeError as e:
        logger.error(f"Claude returned invalid JSON: {e}")
        return {}
    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        return {}


def _call_simple_parser(query: str) -> dict:
    """
    Fallback parser when Claude API is not available.
    Keyword-based, handles the most common cases.
    """
    q = query.lower()
    filters: dict[str, Any] = {"index": "nifty50"}

    # RSI
    if any(w in q for w in ["oversold", "rsi below 30", "low rsi"]):
        filters["rsi_max"] = 35
    if any(w in q for w in ["overbought", "rsi above 70"]):
        filters["rsi_min"] = 65

    # P/E
    import re
    pe_match = re.search(r"p[/\s]*e\s*(below|under|less than|<)\s*(\d+)", q)
    if pe_match:
        filters["pe_max"] = int(pe_match.group(2))

    # Sectors
    sector_map = {
        "bank":    "Banking & Finance",
        "it ":     "IT & Technology",
        "tech":    "IT & Technology",
        "pharma":  "Pharma & Healthcare",
        "health":  "Pharma & Healthcare",
        "fmcg":    "FMCG",
        "auto":    "Auto",
        "energy":  "Energy & Power",
        "infra":   "Infrastructure",
    }
    matched_sectors = []
    for kw, sector in sector_map.items():
        if kw in q and sector not in matched_sectors:
            matched_sectors.append(sector)
    if matched_sectors:
        filters["sectors"] = matched_sectors

    # Trend
    if any(w in q for w in ["uptrend", "above 200", "trending up"]):
        filters["above_200dma"] = True

    # Cap
    if any(w in q for w in ["large cap", "largecap"]):
        filters["market_cap_min_cr"] = 20000
    elif any(w in q for w in ["mid cap", "midcap"]):
        filters["market_cap_min_cr"] = 5000

    # Presets
    if any(w in q for w in ["value", "cheap", "low pe", "low p/e"]):
        filters["fundamental_preset"] = "value"
    elif "growth" in q:
        filters["fundamental_preset"] = "growth"
    elif "dividend" in q:
        filters["fundamental_preset"] = "dividend"
    elif "quality" in q:
        filters["fundamental_preset"] = "quality"

    # Delivery
    if "delivery" in q:
        filters["delivery_min"] = 40

    # Signals
    if any(w in q for w in ["buy", "strong buy"]):
        filters["signals"] = ["Buy", "Strong Buy"]

    return filters


class NLScreener:
    """
    Natural Language interface to IndiaScreen.

    Converts plain English to filter parameters, then runs the screener.

    Args:
        api_key: Anthropic API key. If None, reads from ANTHROPIC_API_KEY env var.
                 If not available, uses a simple keyword-based fallback parser.
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY")

    def parse(self, query: str) -> dict:
        """
        Parse a natural language query into screener filter parameters.

        Args:
            query: Plain English description, e.g.
                   "Show me oversold banking stocks with low P/E"

        Returns:
            dict of filter parameters compatible with Screener.run()
        """
        if self.api_key:
            filters = _call_claude(query, self.api_key)
            logger.info(f"NL parse (Claude): {query!r} → {filters}")
        else:
            logger.info("No API key found — using keyword parser")
            filters = _call_simple_parser(query)
            logger.info(f"NL parse (keywords): {query!r} → {filters}")

        return filters

    def run(self, query: str) -> "pd.DataFrame":
        """
        Parse query and run the screener in one step.

        Args:
            query: Plain English, e.g. "Find profitable IT stocks above 200 DMA"

        Returns:
            Screener results DataFrame
        """
        import pandas as pd
        from screener.scanner import Screener

        print(f"🤖 Parsing: \"{query}\"")
        filters = self.parse(query)

        # Extract index separately (not a run() param in the same way)
        index = filters.pop("index", "nifty50")
        print(f"📋 Filters: {filters}")
        print(f"🔍 Scanning {index.upper()}...")

        s = Screener(index=index)

        # Map parsed filters to Screener.run() params
        run_params = {
            k: v for k, v in filters.items()
            if v is not None and k in {
                "rsi_min", "rsi_max", "pe_min", "pe_max", "pb_max",
                "roe_min", "debt_equity_max", "market_cap_min_cr",
                "delivery_min", "above_200dma", "macd_bullish",
                "supertrend_up", "near_52w_high", "fundamental_preset",
                "sectors", "signals", "top_n", "sort_by",
            }
        }

        results = s.run(**run_params)
        print(f"✅ {len(results)} stocks found")
        return results

    def suggest_queries(self) -> list[str]:
        """Return example natural language queries."""
        return [
            "Show me oversold Nifty 50 banking stocks with P/E below 15",
            "Find quality IT stocks in long-term uptrend with high delivery",
            "Midcap pharma stocks with MACD bullish crossover",
            "Dividend-paying large caps with low debt and strong ROE",
            "Growth stocks near 52-week high with rising volume",
            "Value stocks in Nifty 500 with P/E below 12 and ROE above 15%",
            "Oversold FMCG stocks above 200 DMA",
            "Nifty 50 stocks with strong buy signal and low P/B ratio",
        ]
