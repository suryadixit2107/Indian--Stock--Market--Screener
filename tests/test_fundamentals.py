import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from screener.fundamentals import FundamentalFilter, passes_filter, fundamental_score, piotroski_score, PRESETS

GOOD = {"pe_ratio":15.0,"pb_ratio":2.5,"roe":0.22,"debt_to_equity":0.4,
        "profit_margins":0.18,"revenue_growth":0.20,"dividend_yield":0.025,
        "market_cap":5_000_00_00_000,"sector":"IT & Technology","industry":"Software"}
BAD  = {"pe_ratio":90.0,"pb_ratio":12.0,"roe":0.02,"debt_to_equity":3.5,
        "profit_margins":0.01,"revenue_growth":-0.05,"dividend_yield":0.0,
        "market_cap":50_00_00_000,"sector":"Unknown","industry":"Unknown"}

def test_passes_good(): assert passes_filter(GOOD, FundamentalFilter(pe_max=25, roe_min=0.15, debt_equity_max=1.0))[0]
def test_fails_bad():   assert not passes_filter(BAD,  FundamentalFilter(pe_max=25, roe_min=0.15, debt_equity_max=1.0))[0]
def test_no_filter():   assert passes_filter(BAD, FundamentalFilter())[0]
def test_score_range():
    for s in [GOOD, BAD]: assert 0 <= fundamental_score(s) <= 100
def test_piotroski_range(): assert 0 <= piotroski_score(GOOD) <= 9
def test_good_scores_higher(): assert fundamental_score(GOOD) > fundamental_score(BAD)
def test_missing_fields_ok():
    assert passes_filter({"pe_ratio": None, "roe": None}, FundamentalFilter(pe_max=30, roe_min=0.10))[0]
