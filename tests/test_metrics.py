"""Unit tests for the financial metrics parser.

Reproduces the real Sina layout (rows = periods, columns = line items,
first column = report date) without hitting the network.
"""

from __future__ import annotations

import datetime as _dt

import pandas as pd

from src.metrics import (
    normalise_sina,
    _values_by_item,
    _last_n_positive,
    _avg_equity_pair,
    _annual_yoy_pct,
)


def _toy_sina_bs() -> pd.DataFrame:
    """Real Sina layout: column 0 = report date, column 1+ = line items."""
    return pd.DataFrame(
        {
            "报告日": ["20221231", "20231231", "20241231"],
            "资产总计": [800.0, 850.0, 920.0],
            "负债合计": [400.0, 420.0, 460.0],
            "归属于母公司股东权益合计": [350.0, 380.0, 410.0],
        }
    )


def _approx(v, rel=1e-3):
    class _A:
        def __init__(self, target, rel):
            self.target = target
            self.rel = rel
        def __eq__(self, other):
            try:
                return abs(self.target - other) <= self.rel * max(abs(self.target), abs(other))
            except Exception:
                return False
    return _A(v, rel)


def test_normalise_sina_long_form():
    df = _toy_sina_bs()
    long = normalise_sina(df)
    assert not long.empty
    assert "资产总计" in set(long["item"])
    assert long["period"].min() == _dt.date(2022, 12, 31)
    series = _values_by_item(long, "asset_total")
    assert series.iloc[-1] == 920.0


def test_value_by_item_specific_period():
    long = normalise_sina(_toy_sina_bs())
    series = _values_by_item(long, "asset_total")
    s = series.sort_index()
    assert s.loc[_dt.date(2023, 12, 31)] == 850.0


def test_last_n_positive():
    s = pd.Series([1.0, 2.0, 3.0, 4.0], index=pd.to_datetime(["2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]))
    assert _last_n_positive(s, n=3) is True
    s.iloc[-1] = -1
    assert _last_n_positive(s, n=3) is False


def test_avg_equity_pair():
    s = pd.Series([100.0, 110.0, 121.0], index=pd.to_datetime(["2023-12-31", "2024-12-31", "2025-12-31"]))
    assert _avg_equity_pair(s) == _approx(115.5)


def test_annual_yoy_pct():
    s = pd.Series([100.0, 110.0, 121.0, 133.1], index=pd.to_datetime(["2023-12-31", "2024-12-31", "2025-12-31", "2026-12-31"]))
    out = _annual_yoy_pct(s)
    assert len(out) == 3
    assert all(_approx(10.0) == v for v in out)


def test_normalise_sina_handles_empty():
    assert normalise_sina(pd.DataFrame()).empty


def test_alias_resolution():
    """Both Sina variants of the equity line item should resolve."""
    df = pd.DataFrame({
        "报告日": ["20251231"],
        "归属于母公司股东权益合计": [1000.0],
        "资产总计": [2000.0],
        "负债合计": [500.0],
    })
    long = normalise_sina(df)
    assert _values_by_item(long, "equity_attrib").iloc[-1] == 1000.0
    assert _values_by_item(long, "asset_total").iloc[-1] == 2000.0