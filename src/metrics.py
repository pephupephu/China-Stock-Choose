"""Compute per-stock financial metrics used by the screener.

Provides both a per-stock builder that takes DataFetcher-style payload
dicts (used by parallel runs) and direct fetcher-based helpers
(used by tests / smoke runs).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _to_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
        if pd.isna(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _parse_date(s: str) -> Optional[date]:
    s = str(s).strip()
    m = re.match(r"(20\d{2})(\d{2})(\d{2})", s)
    if m:
        y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return date(y, mo, d)
    m2 = re.match(r"(20\d{2})-(\d{2})-(\d{2})", s)
    if m2:
        y, mo, d = (int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
        return date(y, mo, d)
    return None


def normalise_sina(df: Optional[pd.DataFrame]) -> pd.DataFrame:
    """Pivot Sina wide-form into ``{period, item, value}`` rows."""
    if df is None or df.empty or df.shape[0] == 0 or df.shape[1] == 0:
        return pd.DataFrame(columns=["period", "item", "value"])
    period_col = df.columns[0]
    rows = []
    for _, row in df.iterrows():
        period_raw = row[period_col]
        if pd.isna(period_raw):
            continue
        period_date = _parse_date(str(period_raw))
        if period_date is None:
            continue
        for col in df.columns[1:]:
            v = _to_float(row[col])
            rows.append({
                "period": period_date,
                "item": str(col).replace(" ", ""),
                "value": v,
            })
    return pd.DataFrame(rows)


# convenience alias
_long = normalise_sina


_LINE_ITEMS = {
    "net_profit_attrib": [
        "归属于母公司股东的净利润",
        "归属于母公司所有者的净利润",
        "归母净利润",
        "归属于母公司净利润",
    ],
    "net_profit": ["净利润"],
    "deducted_net_profit": ["扣除非经常性损益后的净利润"],
    "revenue": ["营业总收入", "营业收入"],
    "asset_total": ["资产总计"],
    "liab_total": ["负债合计"],
    "equity_attrib": [
        "归属于母公司股东权益合计",
        "归属于母公司所有者权益合计",
    ],
    "shares_outstanding": ["实收资本(或股本)", "实收资本", "股本"],
    "ocf": ["经营活动产生的现金流量净额"],
}


def _values_by_item(long: pd.DataFrame, key: str) -> pd.Series:
    if long is None or long.empty:
        return pd.Series(dtype=float)
    aliases = _LINE_ITEMS.get(key, [key])
    for alias in aliases:
        sub = long[long["item"].str.contains(alias, na=False, regex=False)]
        if not sub.empty:
            series = (
                sub.dropna(subset=["value"])
                .set_index("period")["value"]
                .sort_index()
            )
            if not series.empty:
                return series
    return pd.Series(dtype=float)


# ---------------------------------------------------------------------------
# Public dataclass
# ---------------------------------------------------------------------------
@dataclass
class StockMetrics:
    symbol: str = ""
    name: str = ""
    listing_date: Optional[str] = None
    industry_shenwan: Optional[str] = None
    industry_name: Optional[str] = None
    close_price: Optional[float] = None
    close_price_as_of: Optional[str] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    pc_ratio: Optional[float] = None
    roe_ttm_pct: Optional[float] = None
    deducted_non_net_profit_positive_3y: Optional[bool] = None
    main_revenue_yoy_pct_list: list[float] = field(default_factory=list)
    cash_dividend_per_share_history: list[dict] = field(default_factory=list)
    payout_ratio_pct: Optional[float] = None
    is_one_time_dividend: Optional[bool] = None
    dividend_yield_pct_history: list[dict] = field(default_factory=list)
    debt_ratio_pct: Optional[float] = None
    operating_cash_flow_per_share: Optional[float] = None
    operating_cash_flow_total: Optional[float] = None
    cash_dividend_total: Optional[float] = None
    audit_opinion: Optional[str] = None
    is_st: Optional[bool] = None
    main_business_summary: Optional[str] = None
    has_non_standard_audit: bool = False
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# variant that builds metrics from a fetch_many payload (used in parallel runs)
# ---------------------------------------------------------------------------
def metrics_from_payload(
    symbol: str,
    name: str,
    payload: dict,
    *,
    listing_date: Optional[str] = None,
    industry_shenwan: Optional[str] = None,
    industry_name: Optional[str] = None,
) -> StockMetrics:
    out = StockMetrics(
        symbol=symbol,
        name=name,
        listing_date=listing_date,
        industry_shenwan=industry_shenwan,
        industry_name=industry_name,
        close_price=payload.get("price"),
        close_price_as_of=payload.get("as_of"),
    )
    if out.close_price is not None and pd.isna(out.close_price):
        out.close_price = None

    is_long = normalise_sina(payload.get("income"))
    bs_long = normalise_sina(payload.get("balance"))
    cf_long = normalise_sina(payload.get("cashflow"))

    np_attrib = _values_by_item(is_long, "net_profit_attrib")
    if np_attrib.empty:
        np_attrib = _values_by_item(is_long, "net_profit")
    np_ttm = _ttm(np_attrib)
    out.audit_opinion = _audit_opinion(payload.get("income"))

    rev_series = _values_by_item(is_long, "revenue")
    if len(rev_series) >= 4:
        out.main_revenue_yoy_pct_list = _annual_yoy_pct(rev_series, window=3)

    equity_eop = _values_by_item(bs_long, "equity_attrib")
    equity_avg = _avg_equity_pair(equity_eop)
    if np_ttm is not None and equity_avg:
        out.roe_ttm_pct = (np_ttm / equity_avg) * 100.0

    shares = _latest(_values_by_item(bs_long, "shares_outstanding"))
    bvps: Optional[float] = None
    if shares and not equity_eop.empty:
        latest_equity = equity_eop.iloc[-1]
        if shares > 0 and latest_equity is not None:
            bvps = latest_equity / shares
    if out.close_price and bvps:
        out.pb = out.close_price / bvps

    if shares and np_ttm:
        eps_ttm = np_ttm / shares
        if eps_ttm > 0 and out.close_price:
            out.pe_ttm = out.close_price / eps_ttm
            if out.roe_ttm_pct and out.pe_ttm:
                out.pc_ratio = out.pe_ttm / out.roe_ttm_pct

    np_series = _values_by_item(is_long, "deducted_net_profit")
    if not np_series.empty:
        out.deducted_non_net_profit_positive_3y = _last_n_positive(np_series, n=3)

    liab = _values_by_item(bs_long, "liab_total")
    asset = _values_by_item(bs_long, "asset_total")
    if not liab.empty and not asset.empty:
        last_liab = liab.iloc[-1]
        last_asset = asset.iloc[-1]
        if last_liab is not None and last_asset and last_asset > 0:
            out.debt_ratio_pct = (last_liab / last_asset) * 100.0

    ocf = _values_by_item(cf_long, "ocf")
    if not ocf.empty:
        last_annual_ocf = _last_annual(ocf)
        out.operating_cash_flow_total = last_annual_ocf
        if shares and last_annual_ocf is not None:
            out.operating_cash_flow_per_share = last_annual_ocf / shares

    div_df = payload.get("dividend")
    if div_df is not None and not div_df.empty:
        div_df = div_df.copy()
        div_df.columns = [str(c) for c in div_df.columns]
        candidates = [c for c in div_df.columns if "派息" in c or "派现" in c or "现金" in c]
        if candidates:
            dcol = candidates[0]
            div_df[dcol] = pd.to_numeric(div_df[dcol], errors="coerce")
            div_df = div_df.dropna(subset=[dcol])
            for _, row in div_df.iterrows():
                year = None
                for col in ("报告时间", "年度"):
                    if col in row.index:
                        m = re.search(r"(20\d{2})", str(row.get(col, "")))
                        if m:
                            year = int(m.group(1))
                            break
                cash_per_share = float(row[dcol]) / 10.0
                dy = (cash_per_share / out.close_price * 100.0) if out.close_price else None
                out.cash_dividend_per_share_history.append({
                    "year": year,
                    "cash_per_share": cash_per_share,
                    "dividend_yield_pct_at_close": dy,
                    "scheme": str(row.get("实施方案分红说明", ""))[:80],
                })
        by_year: dict[int, dict] = {}
        for d in out.cash_dividend_per_share_history:
            if d.get("year") is not None:
                by_year[d["year"]] = d
        cash_hist_sorted = sorted(by_year.values(), key=lambda x: x["year"])
        out.cash_dividend_per_share_history = cash_hist_sorted

        cps = [d["cash_per_share"] for d in cash_hist_sorted]
        if cps:
            sorted_cps = sorted(cps)
            median = sorted_cps[len(sorted_cps) // 2]
            latest = cps[-1]
            out.is_one_time_dividend = latest > 2 * median if median > 0 else None

        last_two = cash_hist_sorted[-2:]
        out.dividend_yield_pct_history = [
            {
                "year": d["year"],
                "cash_per_share": d["cash_per_share"],
                "dividend_yield_pct_at_close": d["dividend_yield_pct_at_close"],
            }
            for d in last_two
        ]

        if cash_hist_sorted and not np_series.empty and shares:
            for d in reversed(cash_hist_sorted):
                yr = d.get("year")
                if yr is None:
                    continue
                try:
                    ann_nps = np_series[np_series.index.year == yr]
                    if ann_nps.empty:
                        continue
                    annual_ni = ann_nps.iloc[-1]
                    if annual_ni is None or annual_ni <= 0:
                        continue
                    cash_total = d["cash_per_share"] * shares
                    out.cash_dividend_total = cash_total
                    out.payout_ratio_pct = (cash_total / annual_ni) * 100.0
                    break
                except Exception:
                    continue

    try:
        mb = payload.get("main_biz")
        if mb is not None and not mb.empty:
            text = " ".join(str(c) for c in mb.columns)
            out.main_business_summary = text[:120]
    except Exception:
        pass

    if out.main_revenue_yoy_pct_list:
        declines = [d for d in out.main_revenue_yoy_pct_list if d is not None and d < -20]
        if declines:
            out.warnings.append("主业收入连续下降超20%")
    if out.payout_ratio_pct is not None and out.payout_ratio_pct > 100:
        out.warnings.append("分红支付率 > 100%，透支式分红，可持续性差")
    if (
        out.operating_cash_flow_total is not None
        and out.cash_dividend_total is not None
        and out.operating_cash_flow_total < out.cash_dividend_total
    ):
        out.warnings.append("经营性现金流 < 当年分红总额，现金流支撑不足")
    if out.debt_ratio_pct is not None and out.debt_ratio_pct > 60:
        out.warnings.append(f"负债率 {out.debt_ratio_pct:.1f}% > 60%")

    return out


# ---------------------------------------------------------------------------
# helper functions (the long form)
# ---------------------------------------------------------------------------
def _ttm(series: pd.Series) -> Optional[float]:
    if series is None or series.empty:
        return None
    if len(series) >= 4:
        recent = series.sort_index().tail(4)
        return float(recent.sum())
    return float(series.iloc[-1])


def _avg_equity_pair(equity_eop: pd.Series) -> Optional[float]:
    if equity_eop is None or equity_eop.empty or len(equity_eop) < 2:
        return equity_eop.iloc[-1] if not equity_eop.empty else None
    return float((equity_eop.iloc[-1] + equity_eop.iloc[-2]) / 2)


def _annual_yoy_pct(series: pd.Series, window: int = 3) -> list[float]:
    """Year-over-year growth, keeping only the most recent ``window`` years.

    Returns the last N annual YoY values (default 3 years). Pass
    ``window=0`` for the full historical list.
    """
    if series is None or series.empty:
        return []
    df = pd.DataFrame({"v": series.values, "p": pd.to_datetime(series.index)})
    df["year"] = df["p"].dt.year
    annual = df.groupby("year")["v"].last().sort_index()
    yoy_list: list[float] = []
    for i in range(1, len(annual)):
        prev = annual.iloc[i - 1]
        cur = annual.iloc[i]
        if prev and prev > 0:
            yoy_list.append((cur - prev) / prev * 100.0)
    if window and len(yoy_list) > window:
        yoy_list = yoy_list[-window:]
    return yoy_list


def _last_n_positive(series: pd.Series, n: int = 3) -> bool:
    if series.empty:
        return False
    recent = series.sort_index().tail(n)
    return bool((recent > 0).all())


def _last_annual(series: pd.Series) -> Optional[float]:
    if series.empty:
        return None
    dec = series[pd.to_datetime(series.index).month == 12]
    if not dec.empty:
        return float(dec.iloc[-1])
    return float(series.iloc[-1])


def _latest(series: pd.Series) -> Optional[float]:
    if series.empty:
        return None
    try:
        return float(series.iloc[-1])
    except Exception:
        return None


def _audit_opinion(wide_income) -> Optional[str]:
    if wide_income is None or wide_income.empty:
        return None
    audit_text_col: Optional[str] = None
    for c in wide_income.columns:
        if "审计意见" in str(c):
            audit_text_col = c
            break
    if audit_text_col is not None and audit_text_col in wide_income.columns:
        vals = wide_income[audit_text_col].dropna().astype(str)
        for v in vals[::-1]:
            if v.strip():
                return v[:80]
    return None


# Backwards-compat alias used by tests
def build_metrics(*args, **kwargs) -> StockMetrics:  # pragma: no cover
    from .data_fetcher import DataFetcher
    fetcher = kwargs.get("fetcher") or args[0]
    if not isinstance(fetcher, DataFetcher):
        raise TypeError("build_metrics requires DataFetcher")
    symbol = kwargs.get("symbol") or args[1]
    name = kwargs.get("name") or args[2]
    close_price = kwargs.get("close_price")
    income_df = fetcher.income_statement(symbol)
    balance_df = fetcher.balance_sheet(symbol)
    cashflow_df = fetcher.cashflow_statement(symbol)
    dividend_df = fetcher.dividend_history(symbol)
    main_biz_df = fetcher.main_business(symbol)
    payload = {
        "income": income_df, "balance": balance_df, "cashflow": cashflow_df,
        "dividend": dividend_df, "main_biz": main_biz_df,
        "price": close_price, "as_of": kwargs.get("close_price_as_of"),
    }
    return metrics_from_payload(
        symbol, name, payload,
        listing_date=kwargs.get("listing_date"),
        industry_shenwan=kwargs.get("industry_shenwan"),
        industry_name=kwargs.get("industry_name"),
    )