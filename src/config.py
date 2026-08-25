"""Centralised configuration for the China-Stock-Choose screener.

All runtime knobs live here. Values come from environment variables first
(those are what GitHub Actions / scheduled tasks feed in), then fall back
to a local .env file if present, then to safe defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


@dataclass
class ScreenerRules:
    """Hard filter thresholds. Mirrors the merged rule-set in README."""

    # Dividend yield window (>= 4% in last 2 distinct annual cash distributions)
    min_dividend_yield_pct: float = 4.0
    dividend_lookback_years: int = 2

    # Valuation
    min_pe_ttm: float = 0.0
    max_pe_ttm: float = 30.0

    # Profitability & stability
    require_deducted_non_net_profit_positive_years: int = 3
    min_roe_pct: float = 10.0          # 3y avg OR each of last 3 years
    roe_strict_all_years: bool = True
    max_revenue_decline_pct: float = 20.0   # YoY single-year max drop in main revenue
    revenue_decline_eval_years: int = 3

    # Capital structure
    max_debt_ratio_pct: float = 70.0
    require_positive_cf_per_share: bool = True

    # Distribution
    min_payout_ratio_pct: float = 40.0
    require_ocf_covers_dividend: bool = True

    # Universe exclusions
    exclude_st: bool = True
    exclude_qualified: bool = False        # exclude non-standard audit opinions
    require_cash_dividend: bool = True    # exclude pure 送股 / 转增 distributions

    # Industry-relative (informational only)
    industry_compare: bool = True


@dataclass
class EmailConfig:
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    smtp_use_ssl: bool = True
    username: str = ""
    password: str = ""            # app-specific password for QQ / 163 etc.
    sender: str = ""             # usually same as username
    recipients: list[str] = field(default_factory=list)
    subject_prefix: str = "[China-Stock-Choose]"


@dataclass
class DataSources:
    akshare_proxy: Optional[str] = None
    fetcher_timeout_seconds: int = 30
    fetcher_max_retries: int = 3
    fetcher_sleep_seconds: float = 0.25
    cache_dir: Path = Path("output/.cache")
    cache_ttl_seconds: int = 6 * 3600


@dataclass
class AppConfig:
    rules: ScreenerRules = field(default_factory=ScreenerRules)
    email: EmailConfig = field(default_factory=EmailConfig)
    data: DataSources = field(default_factory=DataSources)
    output_dir: Path = Path("output")
    top_n: int = 50
    log_level: str = "INFO"


def load_config(env_path: Optional[Path] = None) -> AppConfig:
    env: dict[str, str] = {}
    if env_path is None:
        env_path = Path(__file__).resolve().parent.parent / ".env"
    env.update(_read_env_file(env_path))
    env.update({k: v for k, v in os.environ.items()})  # real env wins

    rules = ScreenerRules(
        min_dividend_yield_pct=float(env.get("RULE_MIN_DIVIDEND_YIELD_PCT", 4.0)),
        max_pe_ttm=float(env.get("RULE_MAX_PE_TTM", 30.0)),
        max_debt_ratio_pct=float(env.get("RULE_MAX_DEBT_RATIO_PCT", 70.0)),
        max_revenue_decline_pct=float(env.get("RULE_MAX_REVENUE_DECLINE_PCT", 20.0)),
        min_payout_ratio_pct=float(env.get("RULE_MIN_PAYOUT_RATIO_PCT", 40.0)),
        min_roe_pct=float(env.get("RULE_MIN_ROE_PCT", 10.0)),
    )

    rcpts = [r.strip() for r in env.get("EMAIL_RECIPIENTS", "").split(",") if r.strip()]
    email = EmailConfig(
        smtp_host=env.get("SMTP_HOST", "smtp.qq.com"),
        smtp_port=int(env.get("SMTP_PORT", "465")),
        smtp_use_ssl=env.get("SMTP_USE_SSL", "true").lower() != "false",
        username=env.get("SMTP_USERNAME", ""),
        password=env.get("SMTP_PASSWORD", ""),
        sender=env.get("EMAIL_SENDER") or env.get("SMTP_USERNAME", ""),
        recipients=rcpts,
        subject_prefix=env.get("EMAIL_SUBJECT_PREFIX", "[China-Stock-Choose]"),
    )

    data = DataSources(
        akshare_proxy=env.get("AKSHARE_PROXY") or env.get("HTTPS_PROXY") or None,
        cache_dir=Path(env.get("CACHE_DIR", "output/.cache")),
        cache_ttl_seconds=int(env.get("CACHE_TTL_SECONDS", str(6 * 3600))),
    )

    return AppConfig(
        rules=rules,
        email=email,
        data=data,
        output_dir=Path(env.get("OUTPUT_DIR", "output")),
        top_n=int(env.get("TOP_N", "50")),
        log_level=env.get("LOG_LEVEL", "INFO"),
    )