"""Apply the merged screening rules against pre-computed metrics.

Every rule lives as a clearly-named predicate returning (passed, reason).
Final picks are sorted by ``score`` descending (dividend_yield + ROE - debt).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from .config import ScreenerRules
from .metrics import StockMetrics


@dataclass
class ScreeningResult:
    metrics: StockMetrics
    passes: bool
    score: float = 0.0
    hard_fail_reasons: list[str] = field(default_factory=list)
    soft_fail_reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _positive(value: Optional[float]) -> bool:
    return value is not None and value > 0


def _pct_min(value: Optional[float], min_pct: float) -> bool:
    return value is not None and value >= min_pct


def _pct_max(value: Optional[float], max_pct: float) -> bool:
    return value is not None and value <= max_pct


# ---------------------------------------------------------------------------
# individual rule predicates -- each accepts metrics + rules and returns
# (passed_bool, reason_str_or_none)
# ---------------------------------------------------------------------------
def _rule_div_yield(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    yields = [
        d.get("dividend_yield_pct_at_close")
        for d in (m.dividend_yield_pct_history or [])
        if d.get("dividend_yield_pct_at_close") is not None
    ]
    if not yields:
        return False, "无近2年分红数据"
    if all(y >= r.min_dividend_yield_pct for y in yields):
        return True, None
    return False, f"近2年股息率均 < {r.min_dividend_yield_pct}%"


def _rule_pe(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if m.pe_ttm is None:
        return False, "PE TTM 缺失"
    if m.pe_ttm <= r.min_pe_ttm:
        return False, f"TTM PE <= {r.min_pe_ttm}"
    if m.pe_ttm >= r.max_pe_ttm:
        return False, f"TTM PE > {r.max_pe_ttm}"
    return True, None


def _rule_roe(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if m.roe_ttm_pct is None:
        return False, "ROE TTM 缺失"
    if m.roe_ttm_pct < r.min_roe_pct:
        return False, f"ROE TTM {m.roe_ttm_pct:.1f}% < {r.min_roe_pct}%"
    return True, None


def _rule_debt(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if m.debt_ratio_pct is None:
        return False, "负债率缺失"
    if m.debt_ratio_pct > r.max_debt_ratio_pct:
        return False, f"负债率 {m.debt_ratio_pct:.1f}% > {r.max_debt_ratio_pct}%"
    return True, None


def _rule_payout(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if m.payout_ratio_pct is None:
        return False, "分红率缺失"
    if m.payout_ratio_pct < r.min_payout_ratio_pct:
        return False, f"分红率 {m.payout_ratio_pct:.1f}% < {r.min_payout_ratio_pct}%"
    # > 100 is allowed but warned elsewhere; do not fail it.
    return True, None


def _rule_ocf_dividend(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if r.require_ocf_covers_dividend:
        if m.operating_cash_flow_total is None or m.cash_dividend_total is None:
            return False, "OCF/分红数据缺失"
        if m.operating_cash_flow_total < m.cash_dividend_total:
            return False, "经营性现金流 < 当年分红总额"
    return True, None


def _rule_cf_per_share(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if r.require_positive_cf_per_share:
        if m.operating_cash_flow_per_share is None:
            return False, "每股OCF缺失"
        if m.operating_cash_flow_per_share <= 0:
            return False, f"每股OCF {m.operating_cash_flow_per_share:.2f} <= 0"
    return True, None


def _rule_revenue_decline(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if not m.main_revenue_yoy_pct_list:
        return True, None
    threshold = -abs(r.max_revenue_decline_pct)
    if min(m.main_revenue_yoy_pct_list) < threshold:
        return False, (
            f"主营收入最大跌幅 "
            f"{min(m.main_revenue_yoy_pct_list):.1f}% "
            f"超过 {r.max_revenue_decline_pct}%"
        )
    return True, None


def _rule_deducted_profit(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if m.deducted_non_net_profit_positive_3y is False:
        return False, "近3年扣非净利润非全正"
    return True, None


def _rule_st(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if r.exclude_st and m.is_st is True:
        return False, "属于 ST / *ST"
    return True, None


def _rule_qualified(m: StockMetrics, r: ScreenerRules) -> tuple[bool, Optional[str]]:
    if r.exclude_qualified and m.has_non_standard_audit:
        return False, "非标审计意见"
    return True, None


# ---------------------------------------------------------------------------
# warnings (independent of build_metrics so manual metrics work too)
# ---------------------------------------------------------------------------
def _derive_warnings(m: StockMetrics, r: ScreenerRules) -> list[str]:
    out: list[str] = list(m.warnings or [])
    if m.payout_ratio_pct is not None and m.payout_ratio_pct > 100:
        msg = "分红支付率 > 100%，透支式分红，可持续性差"
        if msg not in out:
            out.append(msg)
    if (
        m.operating_cash_flow_total is not None
        and m.cash_dividend_total is not None
        and m.operating_cash_flow_total < m.cash_dividend_total
    ):
        msg = "经营性现金流 < 当年分红总额，现金流支撑不足"
        if msg not in out:
            out.append(msg)
    if m.debt_ratio_pct is not None and m.debt_ratio_pct > r.warn_debt_ratio_pct:
        msg = f"负债率 {m.debt_ratio_pct:.1f}% > {r.warn_debt_ratio_pct:g}%"
        if msg not in out:
            out.append(msg)
    if m.main_revenue_yoy_pct_list:
        worst = min(m.main_revenue_yoy_pct_list)
        if worst < -abs(r.max_revenue_decline_pct):
            msg = f"主业收入最大跌幅 {worst:.1f}% 超过 {r.max_revenue_decline_pct:g}%"
            if msg not in out:
                out.append(msg)
    return out


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def _score(m: StockMetrics, r: ScreenerRules) -> float:
    parts: list[tuple[float, float]] = []
    if m.dividend_yield_pct_history:
        avg_yield = sum(
            d.get("dividend_yield_pct_at_close", 0) or 0
            for d in m.dividend_yield_pct_history
        ) / len(m.dividend_yield_pct_history)
        parts.append((0.5, avg_yield))
    if m.roe_ttm_pct is not None:
        parts.append((0.3, m.roe_ttm_pct))
    if m.debt_ratio_pct is not None:
        parts.append((0.1, -m.debt_ratio_pct / 100.0 * 50.0))
    if m.payout_ratio_pct is not None:
        parts.append((0.1, min(m.payout_ratio_pct, 100)))
    score = sum(w * v for w, v in parts)
    if m.is_one_time_dividend:
        score -= 15
    return float(score)


# ---------------------------------------------------------------------------
# public
# ---------------------------------------------------------------------------
HardRule = Callable[[StockMetrics, ScreenerRules], tuple[bool, Optional[str]]]


HARD_RULES: list[tuple[str, HardRule]] = [
    ("ST/*ST", _rule_st),
    ("non-standard audit", _rule_qualified),
    ("近2年股息率≥4%", _rule_div_yield),
    ("TTM 市盈率 0<PE<30", _rule_pe),
    ("近3年扣非净利润 > 0", _rule_deducted_profit),
    ("近3年 ROE > 10%", _rule_roe),
    ("负债率 < 70%", _rule_debt),
    ("分红率 ≥ 40%", _rule_payout),
    ("OCF 覆盖分红", _rule_ocf_dividend),
    ("每股经营现金流 > 0", _rule_cf_per_share),
    ("主营收入跌幅 < 20%", _rule_revenue_decline),
]


def screen(metrics: StockMetrics, rules: ScreenerRules) -> ScreeningResult:
    """Run the full rule set against one stock."""
    res = ScreeningResult(metrics=metrics, passes=True)
    for label, predicate in HARD_RULES:
        ok, reason = predicate(metrics, rules)
        if not ok:
            res.passes = False
            res.hard_fail_reasons.append(f"[{label}] {reason}")
    res.warnings = _derive_warnings(metrics, rules)
    res.score = _score(metrics, rules)
    return res


def sort_picks(results: list[ScreeningResult]) -> list[ScreeningResult]:
    return sorted(
        results,
        key=lambda r: (r.passes, r.score),
        reverse=True,
    )