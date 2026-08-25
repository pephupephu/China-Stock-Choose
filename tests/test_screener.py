"""Rules-engine behaviour tests. Built against synthesised StockMetrics."""

from __future__ import annotations

from src.config import ScreenerRules
from src.metrics import StockMetrics
from src.screener import screen


def make_metrics(**overrides) -> StockMetrics:
    m = StockMetrics(symbol="600000", name="示例")
    # Defaults that satisfy every rule
    defaults = dict(
        is_st=False,
        has_non_standard_audit=False,
        pe_ttm=15.0,
        pb=2.0,
        pc_ratio=1.2,
        roe_ttm_pct=15.0,
        deducted_non_net_profit_positive_3y=True,
        debt_ratio_pct=45.0,
        payout_ratio_pct=55.0,
        operating_cash_flow_total=4000.0,
        cash_dividend_total=2000.0,
        operating_cash_flow_per_share=1.5,
        close_price=12.0,
        cash_dividend_per_share_history=[
            {"year": 2024, "cash_per_share": 0.6, "dividend_yield_pct_at_close": 5.0, "scheme": ""},
            {"year": 2023, "cash_per_share": 0.6, "dividend_yield_pct_at_close": 5.0, "scheme": ""},
        ],
        dividend_yield_pct_history=[
            {"year": 2024, "cash_per_share": 0.6, "dividend_yield_pct_at_close": 5.0},
            {"year": 2023, "cash_per_share": 0.6, "dividend_yield_pct_at_close": 5.0},
        ],
        main_revenue_yoy_pct_list=[5.0, 4.0, 6.0],
        is_one_time_dividend=False,
        industry_name="示例板块",
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(m, k, v)
    return m


def test_pass_all_conditions():
    m = make_metrics()
    rules = ScreenerRules()
    r = screen(m, rules)
    assert r.passes, r.hard_fail_reasons


def test_fail_pe_too_high():
    m = make_metrics(pe_ttm=45.0)
    r = screen(m, ScreenerRules())
    assert not r.passes
    assert any("PE" in x for x in r.hard_fail_reasons)


def test_fail_debt_ratio_too_high():
    m = make_metrics(debt_ratio_pct=80.0)
    r = screen(m, ScreenerRules())
    assert not r.passes
    assert any("负债率" in x for x in r.hard_fail_reasons)


def test_fail_dividend_yield_too_low():
    m = make_metrics(
        cash_dividend_per_share_history=[
            {"year": 2024, "cash_per_share": 0.2, "dividend_yield_pct_at_close": 1.7, "scheme": ""},
            {"year": 2023, "cash_per_share": 0.2, "dividend_yield_pct_at_close": 1.7, "scheme": ""},
        ],
        dividend_yield_pct_history=[
            {"year": 2024, "cash_per_share": 0.2, "dividend_yield_pct_at_close": 1.7},
            {"year": 2023, "cash_per_share": 0.2, "dividend_yield_pct_at_close": 1.7},
        ],
    )
    r = screen(m, ScreenerRules())
    assert not r.passes
    assert any("股息率" in x for x in r.hard_fail_reasons)


def test_fail_st():
    m = make_metrics(is_st=True)
    r = screen(m, ScreenerRules())
    assert not r.passes
    assert any("ST" in x for x in r.hard_fail_reasons)


def test_payout_over_100_warns_but_passes():
    m = make_metrics(payout_ratio_pct=120.0)
    r = screen(m, ScreenerRules())
    assert r.passes
    assert any("透支" in w for w in r.warnings)


def test_ocf_insufficient_blocks():
    m = make_metrics(
        operating_cash_flow_total=1000.0,
        cash_dividend_total=2000.0,
    )
    r = screen(m, ScreenerRules())
    assert not r.passes
    assert any("现金流" in x for x in r.hard_fail_reasons)