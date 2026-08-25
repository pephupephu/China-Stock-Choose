"""Render screening results to HTML (for email) + Markdown (for git)."""

from __future__ import annotations

import datetime as _dt
import html
import io
import json
from pathlib import Path
from typing import Iterable, Optional

from .metrics import StockMetrics
from .screener import ScreeningResult


def _fmt_pct(value: Optional[float], sign: bool = False) -> str:
    if value is None:
        return "-"
    s = f"{value:+.1f}%" if sign else f"{value:.1f}%"
    return s


def _fmt_float(value: Optional[float], digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def _fmt_str(value: Optional[str], fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    return html.escape(str(value))


def _fmt_bool(value: Optional[bool], true_label: str, false_label: str) -> str:
    if value is None:
        return "-"
    return true_label if value else false_label


# ---------------------------------------------------------------------------
# markdown
# ---------------------------------------------------------------------------
def render_markdown_table(results: list[ScreeningResult]) -> str:
    rows: list[str] = []
    rows.append(
        "| 代码 | 简称 | 板块 | 上市日期 | 现价 | "
        "近2年股息率 | ROE TTM | TTM PE | PB | 市赚率 | "
        "主营YoY | 负债率 | 分红率 | 每股OCF | 近2年每股分红 | 审计意见 | 备注 |"
    )
    rows.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        m = r.metrics
        dy = (
            ", ".join(
                f"{d['year']}:{_fmt_pct(d['dividend_yield_pct_at_close'])}"
                for d in (m.dividend_yield_pct_history or [])
                if d.get("year")
            )
            or "-"
        )
        cps_history = ", ".join(
            f"{d['year']}:{d['cash_per_share']:.2f}"
            for d in (m.cash_dividend_per_share_history or [])
            if d.get("year")
        )
        yoy = (
            ", ".join(_fmt_pct(v, sign=True) for v in m.main_revenue_yoy_pct_list)
            or "-"
        )
        warnings = " / ".join(m.warnings or []) or "-"
        rows.append(
            "| "
            + " | ".join(
                [
                    m.symbol,
                    m.name,
                    _fmt_str(m.industry_name, "未分类"),
                    _fmt_str(m.listing_date, "-"),
                    _fmt_float(m.close_price),
                    dy,
                    _fmt_pct(m.roe_ttm_pct),
                    _fmt_float(m.pe_ttm),
                    _fmt_float(m.pb),
                    _fmt_float(m.pc_ratio, 1),
                    yoy,
                    _fmt_pct(m.debt_ratio_pct),
                    _fmt_pct(m.payout_ratio_pct),
                    _fmt_float(m.operating_cash_flow_per_share),
                    cps_history or "-",
                    _fmt_str(m.audit_opinion, "-"),
                    warnings,
                ]
            )
            + " |"
        )
    return "\n".join(rows)


def render_markdown(results: list[ScreeningResult], run_date: _dt.date) -> str:
    picks = [r for r in results if r.passes]
    rejected = [r for r in results if not r.passes]
    buf = io.StringIO()
    buf.write(f"# 选股结果 · {run_date.isoformat()}\n\n")
    buf.write(
        f"**满足全部硬过滤条件:** {len(picks)} 只 (扫描 {len(results)} 只)\n\n"
    )
    if picks:
        buf.write("## 入选名单\n\n")
        buf.write(render_markdown_table(picks))
        buf.write("\n\n## 数据来源\n\n")
        buf.write(
            "- 巨潮资讯网 (cninfo.com.cn) - 现金分红 / 公司公告\n"
            "- 新浪财经 (finance.sina.com.cn) - 资产负债表 / 利润表 / 现金流量表 / 日K\n"
            "- 同花顺 - 主营业务构成\n"
            "- 申万指数 - 行业成分 / 行业市盈率\n\n"
        )
    buf.write("---\n\n")
    buf.write(
        f"**被规则剔除:** {len(rejected)} 只（前 50 条节选）\n\n"
    )
    if rejected:
        buf.write(render_markdown_table(rejected[:50]))
        buf.write("\n")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# html
# ---------------------------------------------------------------------------
def _table_html(rows: list[dict[str, str]], columns: list[tuple[str, str]]) -> str:
    head = "".join(f"<th>{label}</th>" for _, label in columns)
    body = []
    for row in rows:
        cells = "".join(
            f"<td>{row.get(key, '-')}</td>" for key, _ in columns
        )
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _pick_row_html(r: ScreeningResult) -> dict[str, str]:
    m = r.metrics
    dy_text = ""
    for d in (m.dividend_yield_pct_history or []):
        dy_text += f"{d.get('year', '')} {_fmt_pct(d.get('dividend_yield_pct_at_close'))}; "
    return {
        "code": m.symbol,
        "name": m.name,
        "industry": _fmt_str(m.industry_name, "未分类"),
        "listing": _fmt_str(m.listing_date, "-"),
        "close": _fmt_float(m.close_price),
        "dy": dy_text or "-",
        "roe": _fmt_pct(m.roe_ttm_pct),
        "pe": _fmt_float(m.pe_ttm),
        "pb": _fmt_float(m.pb),
        "pc": _fmt_float(m.pc_ratio, 1),
        "debt": _fmt_pct(m.debt_ratio_pct),
        "payout": _fmt_pct(m.payout_ratio_pct),
        "ocfps": _fmt_float(m.operating_cash_flow_per_share),
        "audit": _fmt_str(m.audit_opinion, "-"),
        "yoy": ", ".join(_fmt_pct(v, sign=True) for v in m.main_revenue_yoy_pct_list) or "-",
        "score": f"{r.score:.1f}",
        "warnings": " / ".join(m.warnings or []) or "-",
    }


def render_html(results: list[ScreeningResult], run_date: _dt.date) -> str:
    picks = [r for r in results if r.passes]
    rejected = [r for r in results if not r.passes][:50]
    css = (
        "body{font-family:'PingFang SC',Helvetica,Arial,sans-serif;"
        "font-size:14px;color:#222;max-width:1280px;margin:auto;padding:24px}"
        "h1{margin-bottom:4px}h2{margin-top:24px}"
        "table{border-collapse:collapse;width:100%;margin-top:12px;font-size:13px}"
        "th,td{border:1px solid #dde;padding:6px 8px;text-align:left;vertical-align:top}"
        "th{background:#f4f6fa}"
        "tr.pick td{background:#f9fff3}"
        "tr.soft-warn td.warning{color:#b14f00}"
        ".pill{display:inline-block;border:1px solid #cde;background:#eef;padding:2px 6px;border-radius:4px;margin-right:4px;font-size:12px}"
        "footer{color:#888;font-size:12px;margin-top:24px}"
    )
    columns_pick = [
        ("code", "代码"),
        ("name", "简称"),
        ("industry", "板块"),
        ("listing", "上市日期"),
        ("close", "现价"),
        ("dy", "近2年股息率"),
        ("roe", "ROE TTM"),
        ("pe", "TTM PE"),
        ("pb", "PB"),
        ("pc", "市赚率"),
        ("debt", "负债率"),
        ("payout", "分红率"),
        ("ocfps", "每股OCF"),
        ("yoy", "主营YoY"),
        ("audit", "审计意见"),
        ("warnings", "风险提示"),
    ]
    pick_rows = [_pick_row_html(r) for r in picks]
    rejected_rows = [_pick_row_html(r) for r in rejected]

    body = []
    body.append(f"<h1>📈 每日选股 · {run_date.isoformat()}</h1>")
    body.append(
        f"<p><span class='pill'>规则命中 {len(picks)} 只</span>"
        f"<span class='pill'>扫描 {len(results)} 只</span>"
        f"<span class='pill'>运行 {run_date.isoformat()}</span></p>"
    )
    body.append(
        "<p style='color:#555'>仅依据公开披露的上市公司财务数据，未引入第三方研报/分析观点。"
        "数据来源：巨潮资讯网、新浪财经、同花顺、申万指数。</p>"
    )
    if pick_rows:
        body.append("<h2>✔ 满足全部硬过滤条件</h2>")
        body.append(_table_html(pick_rows, columns_pick))
    body.append("<h2>✘ 被规则剔除（节选前 50）</h2>")
    body.append(_table_html(rejected_rows, columns_pick))
    body.append(
        "<footer>China-Stock-Choose · 仅供研究自用，不构成投资建议。请独立判断并自负盈亏。</footer>"
    )
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        f"<style>{css}</style></head><body>{''.join(body)}</body></html>"
    )


# ---------------------------------------------------------------------------
# file helpers
# ---------------------------------------------------------------------------
def write_outputs(
    output_dir: Path,
    results: list[ScreeningResult],
    run_date: _dt.date,
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"pick_{run_date.isoformat()}.md"
    html_path = output_dir / f"pick_{run_date.isoformat()}.html"
    json_path = output_dir / f"pick_{run_date.isoformat()}.json"

    md_path.write_text(render_markdown(results, run_date), encoding="utf-8")
    html_path.write_text(render_html(results, run_date), encoding="utf-8")

    payload = {
        "run_date": run_date.isoformat(),
        "picks": [
            {
                **{k: v for k, v in r.metrics.__dict__.items()},
                "score": r.score,
                "warnings": r.warnings,
                "passes": r.passes,
                "hard_fail_reasons": r.hard_fail_reasons,
                "soft_fail_reasons": r.soft_fail_reasons,
            }
            for r in results
        ],
    }
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return {"md": md_path, "html": html_path, "json": json_path}