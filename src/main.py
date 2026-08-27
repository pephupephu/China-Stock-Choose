"""Orchestrator for the daily A-share screener.

Run modes::

    python -m src.main run       # full pipeline + email + artefacts
    python -m src.main screen    # screen only (no email)
    python -m src.main test      # smoke test on a tiny sample
    python -m src.main --help

Note: data acquisition is parallelised with a thread pool; cache TTL 6 h
means subsequent runs are essentially free.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import logging
import re
import sys
import time
from pathlib import Path

import pandas as pd

from .config import AppConfig, load_config
from .data_fetcher import (
    DataFetcher,
    is_st_or_star,
    fetch_many,
)
from .industry import ShenwanResolver, compute_industry_medians
from .metrics import metrics_from_payload
from .report import render_html, render_markdown, write_outputs
from .screener import ScreeningResult, screen, sort_picks
from .notifier import render_plain_text, send_email


logger = logging.getLogger("china-stock-choose")


def _setup_logging(level: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _listing_date_heuristic(income: pd.DataFrame) -> str | None:
    if income is None or income.empty:
        return None
    if "报告日" not in income.columns:
        return None
    try:
        earliest = pd.to_datetime(income["报告日"], errors="coerce").min()
        return earliest.strftime("%Y-%m-%d") if pd.notna(earliest) else None
    except Exception:
        return None


def _run_full_screen(cfg: AppConfig, limit: int = 0) -> list[ScreeningResult]:
    fetcher = DataFetcher(
        proxy=cfg.data.akshare_proxy,
        cache_dir=cfg.data.cache_dir,
        cache_ttl_seconds=cfg.data.cache_ttl_seconds,
        max_workers=int(__import__("os").getenv("SCREENER_MAX_WORKERS", "16")),
    )
    resolver = ShenwanResolver(fetcher)

    universe = fetcher.all_a_share_codes()
    universe = universe[universe["symbol"].str.match(r"^\d{6}$", na=False)]
    # Filter to pure A-shares only: drop Shenzhen B (200xxx) and Shanghai B (9xxxxx),
    # plus ST / *ST names so they never reach the rule pipeline or the report.
    universe = universe[~universe["symbol"].str.startswith(("200", "9"))]
    universe = universe[~universe["name"].astype(str).str.contains("ST", na=False, regex=False)]
    if limit and limit > 0:
        universe = universe.head(limit)
    logger.info("Universe size: %d A-shares%s (excluded ST/B-share)", len(universe), " (limited)" if limit else "")

    today = _dt.date.today()
    symbol_to_meta: dict[str, dict[str, str]] = {}
    for _, row in universe.iterrows():
        symbol = str(row["symbol"])
        name = str(row["name"])
        try:
            shenwan_code = resolver.for_symbol(symbol)
            shenwan_name = next(
                (n for c, n in resolver.all_codes() if c == shenwan_code),
                "未分类",
            )
        except Exception:
            shenwan_code = None
            shenwan_name = "未分类"
        symbol_to_meta[symbol] = {
            "name": name,
            "shenwan_code": shenwan_code or "",
            "shenwan_name": shenwan_name,
        }

    payloads = fetch_many(fetcher, symbol_to_meta, today)

    results: list[ScreeningResult] = []
    symbol_to_metrics: dict[str, dict] = {}
    for symbol, payload in payloads.items():
        try:
            is_st = is_st_or_star(symbol_to_meta[symbol]["name"])
            income = payload.get("income")
            metrics = metrics_from_payload(
                symbol,
                symbol_to_meta[symbol]["name"],
                payload,
                listing_date=_listing_date_heuristic(income),
                industry_shenwan=symbol_to_meta[symbol]["shenwan_code"] or None,
                industry_name=symbol_to_meta[symbol]["shenwan_name"],
            )
            metrics.is_st = is_st
            res = screen(metrics, cfg.rules)
            results.append(res)
            symbol_to_metrics[symbol] = {
                k: v for k, v in metrics.__dict__.items()
            }
        except Exception as exc:
            logger.warning("skipping %s: %s", symbol, exc)
        time.sleep(cfg.data.fetcher_sleep_seconds)

    medians = compute_industry_medians(resolver, symbol_to_metrics)
    for r in results:
        if r.metrics.industry_shenwan and r.metrics.industry_shenwan in medians:
            for key, val in medians[r.metrics.industry_shenwan].items():
                if not hasattr(r.metrics, key) or getattr(r.metrics, key) is None:
                    setattr(r.metrics, key, val)

    return sort_picks(results)


def _send(cfg: AppConfig, results: list[ScreeningResult], today: _dt.date) -> None:
    if not cfg.email.recipients:
        logger.warning("EMAIL_RECIPIENTS not set; skipping email")
        return
    html_body = render_html(results, today)
    plain_body = render_plain_text(results, today)
    subject = (
        f"{cfg.email.subject_prefix} {today.isoformat()} "
        f"命中 {sum(1 for r in results if r.passes)} 只"
    )
    send_email(cfg.email, subject=subject, html_body=html_body, plain_body=plain_body)
    logger.info("Email sent to %s", ", ".join(cfg.email.recipients))


def cmd_run(cfg: AppConfig, limit: int = 0) -> int:
    results = _run_full_screen(cfg, limit=limit)
    today = _dt.date.today()
    files = write_outputs(cfg.output_dir, results, today)
    logger.info("Outputs: %s", ", ".join(str(p) for p in files.values()))
    try:
        _send(cfg, results, today)
    except Exception as exc:
        logger.error("Email failed: %s", exc)
        return 1
    return 0


def cmd_screen(cfg: AppConfig, limit: int = 0) -> int:
    results = _run_full_screen(cfg, limit=limit)
    today = _dt.date.today()
    files = write_outputs(cfg.output_dir, results, today)
    picks = sum(1 for r in results if r.passes)
    print(f"\n===== 选股结果 ({today.isoformat()}) =====")
    print(f"满足全部规则: {picks} 只 / 扫描 {len(results)} 只\n")
    top = [r for r in results if r.passes][:50]
    if top:
        print("命中清单 (按 score 排序):")
        for r in top:
            m = r.metrics
            yr = (m.dividend_yield_pct_history or [{}])[-1] if m.dividend_yield_pct_history else {}
            dy_str = ", ".join(
                f"{d['year']}:{d['dividend_yield_pct_at_close']:.2f}%"
                for d in (m.dividend_yield_pct_history or [])
                if d.get("year")
            )
            cps_str = ", ".join(
                f"{d['year']}:{d['cash_per_share']:.2f}"
                for d in (m.cash_dividend_per_share_history or [])
                if d.get("year")
            )
            yoy_str = ", ".join(
                f"{v:+.1f}%" for v in m.main_revenue_yoy_pct_list
            )
            print(
                f"\n  {m.symbol} {m.name} ({m.industry_name or '未分类'})"
                f"\n  上市日: {m.listing_date or '-'}"
                f"    现价: {m.close_price} ({m.close_price_as_of})"
                f"\n  PE: {m.pe_ttm}  PB: {m.pb}  市赚率: {m.pc_ratio}"
                f"  ROE TTM: {m.roe_ttm_pct}%"
                f"\n  每股OCF: {m.operating_cash_flow_per_share}  负债率: {m.debt_ratio_pct}%"
                f"  分红率: {m.payout_ratio_pct}%"
                f"\n  近2年每股分红: {cps_str}"
                f"\n  近2年股息率: {dy_str}"
                f"\n  主营YoY: {yoy_str}"
                f"\n  在建工程/重大事项(同花顺主营): {m.main_business_summary or '-'}"
                f"\n  评分: {r.score:.1f}"
            )
            if r.warnings:
                print(f"  ⚠ {', '.join(r.warnings)}")
    print("\n落盘文件:")
    for path in files.values():
        print(f"  {path}")
    return 0


def cmd_test(cfg: AppConfig) -> int:
    sample_symbols = ["600519", "000858", "601318", "000651"]
    fetcher = DataFetcher(
        proxy=cfg.data.akshare_proxy,
        cache_dir=cfg.data.cache_dir,
        cache_ttl_seconds=cfg.data.cache_ttl_seconds,
        max_workers=2,
    )
    today = _dt.date.today()
    meta = {s: {"name": fetcher.all_a_share_codes().set_index("symbol").loc[s, "name"]} for s in sample_symbols}
    payloads = fetch_many(fetcher, meta, today)
    buf = io.StringIO()
    buf.write("\n===== Smoke test =====\n")
    for symbol, payload in payloads.items():
        try:
            metrics = metrics_from_payload(
                symbol, meta[symbol]["name"], payload
            )
            res = screen(metrics, cfg.rules)
            buf.write(
                f"\n=== {metrics.symbol} {metrics.name} "
                f"close={metrics.close_price} "
                f"PE={metrics.pe_ttm} PB={metrics.pb} 市赚率={metrics.pc_ratio} "
                f"ROE={metrics.roe_ttm_pct}% 负债率={metrics.debt_ratio_pct}% "
                f"每股OCF={metrics.operating_cash_flow_per_share} "
                f"分红率={metrics.payout_ratio_pct}% "
                f"passes={res.passes} score={res.score:.1f}\n"
            )
            if res.hard_fail_reasons:
                buf.write(f"  失败原因: {res.hard_fail_reasons}\n")
            if res.warnings:
                buf.write(f"  警示: {res.warnings}\n")
            dy = metrics.dividend_yield_pct_history or []
            buf.write(f"  近2年股息率: {[(d['year'], round(d['dividend_yield_pct_at_close'], 2)) for d in dy if d.get('year')]}\n")
        except Exception as exc:
            buf.write(f"!! {symbol} failed: {exc}\n")
    sys.stdout.write(buf.getvalue())
    sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="china-stock-choose")
    parser.add_argument("--limit", type=int, default=0, help="limit to first N symbols (debug/test)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="full pipeline + email")
    sub.add_parser("screen", help="run screener only")
    sub.add_parser("test", help="smoke test on a handful of tickers")
    args = parser.parse_args(argv)
    cfg = load_config()
    _setup_logging(cfg.log_level)

    if args.cmd == "run":
        return cmd_run(cfg, limit=getattr(args, "limit", 0))
    if args.cmd == "screen":
        return cmd_screen(cfg, limit=getattr(args, "limit", 0))
    if args.cmd == "test":
        return cmd_test(cfg)
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())