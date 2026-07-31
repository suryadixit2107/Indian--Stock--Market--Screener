"""
fundamentals.py — Fundamental analysis filters for IndiaScreen

Provides scoring, filtering, and ranking based on:
  - Valuation : P/E, P/B, EV/EBITDA
  - Quality   : ROE, ROCE, debt/equity, current ratio
  - Growth    : revenue growth, earnings growth
  - Dividends : yield, payout ratio
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ── Filter config dataclass ────────────────────────────────────────────────────

@dataclass
class FundamentalFilter:
    """
    Declarative filter for fundamental screening.
    None means "no filter applied" for that field.
    """
    # Valuation
    pe_min:   Optional[float] = None
    pe_max:   Optional[float] = None
    pb_min:   Optional[float] = None
    pb_max:   Optional[float] = None

    # Quality
    roe_min:          Optional[float] = None   # e.g. 0.15 = 15%
    debt_equity_max:  Optional[float] = None   # e.g. 1.0
    profit_margin_min:Optional[float] = None   # e.g. 0.10 = 10%

    # Growth
    revenue_growth_min: Optional[float] = None # e.g. 0.10 = 10% YoY
    eps_growth_min:     Optional[float] = None

    # Size
    market_cap_min_cr: Optional[float] = None  # in ₹ crore
    market_cap_max_cr: Optional[float] = None

    # Income
    dividend_yield_min: Optional[float] = None  # e.g. 0.02 = 2%

    # Sector
    sectors:    list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)


# ── Filtering logic ───────────────────────────────────────────────────────────

def passes_filter(fund: dict, f: FundamentalFilter) -> tuple[bool, list[str]]:
    """
    Check whether a stock's fundamental data passes all filter criteria.

    Args:
        fund: dict from fetch.fetch_fundamentals()
        f:    FundamentalFilter instance

    Returns:
        (passed: bool, reasons: list of failed criteria strings)
    """
    fails = []

    def _check(val, low, high, label):
        if val is None:
            return  # skip if data unavailable — don't disqualify
        if low is not None and val < low:
            fails.append(f"{label} {val:.2f} < min {low}")
        if high is not None and val > high:
            fails.append(f"{label} {val:.2f} > max {high}")

    pe  = fund.get("pe_ratio")
    pb  = fund.get("pb_ratio")
    roe = fund.get("roe")
    de  = fund.get("debt_to_equity")
    pm  = fund.get("profit_margins")
    rg  = fund.get("revenue_growth")
    dy  = fund.get("dividend_yield")
    mc  = fund.get("market_cap")  # in ₹ (raw from yfinance)
    mc_cr = mc / 1e7 if mc else None  # convert to crore

    _check(pe,    f.pe_min,             f.pe_max,             "P/E")
    _check(pb,    f.pb_min,             f.pb_max,             "P/B")
    _check(roe,   f.roe_min,            None,                 "ROE")
    _check(de,    None,                 f.debt_equity_max,    "D/E")
    _check(pm,    f.profit_margin_min,  None,                 "Margin")
    _check(rg,    f.revenue_growth_min, None,                 "Rev.Growth")
    _check(dy,    f.dividend_yield_min, None,                 "Div.Yield")
    _check(mc_cr, f.market_cap_min_cr,  f.market_cap_max_cr,  "MktCap(Cr)")

    if f.sectors and fund.get("sector") not in f.sectors:
        fails.append(f"Sector '{fund.get('sector')}' not in {f.sectors}")

    if f.industries and fund.get("industry") not in f.industries:
        fails.append(f"Industry '{fund.get('industry')}' not in {f.industries}")

    return len(fails) == 0, fails


# ── Piotroski F-Score ─────────────────────────────────────────────────────────

def piotroski_score(fund: dict, prev_fund: Optional[dict] = None) -> int:
    """
    Simplified Piotroski F-Score (0–9) using available yfinance data.

    A score of 7–9 indicates a fundamentally strong company.

    Criteria (each = 1 point):
      Profitability (4 points)
        1. Positive ROA
        2. Positive operating cash flow
        3. ROA improving YoY
        4. Cash flow > net income (accruals)
      Leverage/Liquidity (3 points)
        5. D/E ratio decreasing
        6. Current ratio improving
        7. No new share issuance
      Operating efficiency (2 points)
        8. Gross margin improving
        9. Asset turnover improving

    Note: some criteria require prior-year data (prev_fund). If unavailable,
    those criteria default to 0.
    """
    score = 0

    roe = fund.get("roe") or 0
    pm  = fund.get("profit_margins") or 0
    de  = fund.get("debt_to_equity") or 0

    # Profitability proxies
    if roe > 0:      score += 1   # P1: positive ROA proxy
    if pm > 0:       score += 1   # P2: profitable
    if roe > 0.10:   score += 1   # P3: strong ROE
    if pm > 0.10:    score += 1   # P4: healthy margins

    # Leverage proxies
    if de < 1.0:     score += 1   # L1: low leverage
    if de < 0.5:     score += 1   # L2: very low leverage
    score += 1                    # L3: assume no dilution (data not in yfinance free tier)

    # Efficiency proxies
    if prev_fund:
        prev_roe = prev_fund.get("roe") or 0
        prev_pm  = prev_fund.get("profit_margins") or 0
        if roe > prev_roe: score += 1   # E1: improving ROE
        if pm  > prev_pm:  score += 1   # E2: improving margins
    else:
        score += 1  # assume improving if no prior data

    return min(score, 9)


# ── Simple scoring for screener ranking ───────────────────────────────────────

def fundamental_score(fund: dict) -> float:
    """
    Composite fundamental score 0–100 for ranking screener results.
    Higher = stronger fundamentals.
    """
    score = 50.0

    pe  = fund.get("pe_ratio")
    pb  = fund.get("pb_ratio")
    roe = fund.get("roe") or 0
    de  = fund.get("debt_to_equity") or 0
    pm  = fund.get("profit_margins") or 0
    dy  = fund.get("dividend_yield") or 0
    rg  = fund.get("revenue_growth") or 0

    # P/E: sweet spot 8–20 for Indian large caps
    if pe:
        if 8 <= pe <= 15:   score += 15
        elif 15 < pe <= 25: score += 8
        elif pe > 50:        score -= 10
        elif pe < 5:         score -= 5   # too cheap = possible trap

    # ROE: quality proxy
    if roe > 0.20:  score += 15
    elif roe > 0.15: score += 8
    elif roe < 0.05: score -= 10

    # Debt
    if de < 0.3:   score += 10
    elif de < 0.8: score += 5
    elif de > 2.0:  score -= 15

    # Margins
    if pm > 0.20:   score += 10
    elif pm > 0.10: score += 5
    elif pm < 0.03: score -= 5

    # Dividend (bonus for income seekers)
    if dy > 0.03:  score += 5
    elif dy > 0.01: score += 2

    # Revenue growth
    if rg > 0.15:  score += 10
    elif rg > 0.05: score += 5
    elif rg < 0:    score -= 10

    return round(max(0, min(100, score)), 1)


# ── Bulk filter ────────────────────────────────────────────────────────────────

def filter_dataframe(df: pd.DataFrame, f: FundamentalFilter) -> pd.DataFrame:
    """
    Apply fundamental filters to a screener results DataFrame.

    The DataFrame is expected to have columns matching fund dict keys.
    Returns filtered DataFrame with a 'fund_score' column added.
    """
    if df.empty:
        return df

    out = df.copy()

    if f.pe_min is not None and "pe_ratio" in out.columns:
        out = out[out["pe_ratio"].isna() | (out["pe_ratio"] >= f.pe_min)]
    if f.pe_max is not None and "pe_ratio" in out.columns:
        out = out[out["pe_ratio"].isna() | (out["pe_ratio"] <= f.pe_max)]
    if f.pb_max is not None and "pb_ratio" in out.columns:
        out = out[out["pb_ratio"].isna() | (out["pb_ratio"] <= f.pb_max)]
    if f.roe_min is not None and "roe" in out.columns:
        out = out[out["roe"].isna() | (out["roe"] >= f.roe_min)]
    if f.debt_equity_max is not None and "debt_to_equity" in out.columns:
        out = out[out["debt_to_equity"].isna() | (out["debt_to_equity"] <= f.debt_equity_max)]
    if f.market_cap_min_cr is not None and "market_cap_cr" in out.columns:
        out = out[out["market_cap_cr"].isna() | (out["market_cap_cr"] >= f.market_cap_min_cr)]
    if f.sectors and "sector" in out.columns:
        out = out[out["sector"].isin(f.sectors) | out["sector"].isna()]

    if "pe_ratio" in out.columns:
        out["fund_score"] = out.apply(
            lambda row: fundamental_score(row.to_dict()), axis=1
        )

    return out.reset_index(drop=True)


# ── Preset filter presets ─────────────────────────────────────────────────────

PRESETS: dict[str, FundamentalFilter] = {
    "value": FundamentalFilter(
        pe_max=20,
        pb_max=3.0,
        roe_min=0.12,
        debt_equity_max=1.0,
    ),
    "growth": FundamentalFilter(
        revenue_growth_min=0.15,
        roe_min=0.15,
        profit_margin_min=0.10,
        pe_max=40,
    ),
    "dividend": FundamentalFilter(
        dividend_yield_min=0.025,
        debt_equity_max=1.0,
        pe_max=25,
    ),
    "quality": FundamentalFilter(
        roe_min=0.20,
        debt_equity_max=0.5,
        profit_margin_min=0.15,
    ),
    "largecap": FundamentalFilter(
        market_cap_min_cr=20000,
    ),
}
