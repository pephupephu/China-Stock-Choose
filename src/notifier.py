"""Email notifier with multipart HTML + text alternatives.

Designed for QQ Mail / 163 Mail / Gmail. SSL on 465 by default; STARTTLS
on 587 if you set SMTP_USE_SSL=false. Uses an app-specific password.
"""

from __future__ import annotations

import datetime as _dt
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Iterable, Optional

from .config import EmailConfig
from .screener import ScreeningResult


def render_plain_text(
    results: list[ScreeningResult],
    run_date: _dt.date,
    soft_picks: Optional[list[ScreeningResult]] = None,
    near_misses: Optional[list[ScreeningResult]] = None,
) -> str:
    lines = [f"China-Stock-Choose · {run_date.isoformat()}", ""]
    picks = [r for r in results if r.passes]
    soft_picks = soft_picks or []
    near_misses = near_misses or []
    lines.append(f"满足全部规则: {len(picks)} 只 / 扫描 {len(results)}")
    lines.append("说明: 数据来自巨潮资讯网、新浪财经、同花顺、申万指数。仅供研究自用，不构成投资建议。")
    lines.append("")
    for r in picks:
        m = r.metrics
        lines.append(f"== {m.symbol} {m.name} ==")
        lines.append(f"  现价   : {m.close_price}")
        lines.append(f"  板块   : {m.industry_name}")
        lines.append(f"  上市日 : {m.listing_date}")
        lines.append(f"  TTM PE : {m.pe_ttm}    PB: {m.pb}    市赚率: {m.pc_ratio}")
        lines.append(f"  ROE   : {m.roe_ttm_pct}%")
        lines.append(f"  分红率 : {m.payout_ratio_pct}%    负债率: {m.debt_ratio_pct}%")
        lines.append(f"  每股OCF: {m.operating_cash_flow_per_share}")
        if m.warnings:
            lines.append(f"  ⚠ {', '.join(m.warnings)}")
        lines.append("")
    if soft_picks:
        lines.append("【软命中：仅连续性规则未满足，但最近一年仍达股息率门槛】")
        for r in soft_picks:
            m = r.metrics
            lines.append(f"  ~ {m.symbol} {m.name}  ROE {m.roe_ttm_pct}%  负债率 {m.debt_ratio_pct}%")
        lines.append("")
    if near_misses:
        lines.append("【近失：仅 1 条硬规则未满足】")
        for r in near_misses:
            m = r.metrics
            lines.append(f"  ~ {m.symbol} {m.name}  " + "; ".join(r.hard_fail_reasons))
        lines.append("")
    return "\n".join(lines)


def send_email(
    cfg: EmailConfig,
    subject: str,
    html_body: str,
    plain_body: str,
    attachments: Iterable[Path] = (),
) -> None:
    if not cfg.username or not cfg.password:
        raise RuntimeError("SMTP_USERNAME / SMTP_PASSWORD must be set to send email")
    if not cfg.recipients:
        raise RuntimeError("EMAIL_RECIPIENTS must be set (comma-separated list)")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.sender or cfg.username
    msg["To"] = ", ".join(cfg.recipients)
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    context = ssl.create_default_context()
    if cfg.smtp_use_ssl:
        with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=context, timeout=30) as s:
            s.login(cfg.username, cfg.password)
            s.sendmail(msg["From"], cfg.recipients, msg.as_string())
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=30) as s:
            s.starttls(context=context)
            s.login(cfg.username, cfg.password)
            s.sendmail(msg["From"], cfg.recipients, msg.as_string())