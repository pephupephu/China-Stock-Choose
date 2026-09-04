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
import json
import logging
import re
import sys
import time
from dataclasses import fields as _dc_fields
from pathlib import Path

import pandas as pd

from .config import AppConfig, load_config
from .data_fetcher import (
    DataFetcher,
    is_st_or_star,
    fetch_many,
)
from .industry import ShenwanResolver, compute_industry_medians
from .metrics import StockMetrics, metrics_from_payload
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


def _build_universe(fetcher: DataFetcher) -> pd.DataFrame:
    """Filtered A-share universe (no ST / B-share / non-6-digit codes)."""
    universe = fetcher.all_a_share_codes()
    universe = universe[universe["symbol"].str.match(r"^\d{6}$", na=False)]
    universe = universe[~universe["symbol"].str.startswith(("200", "9"))]
    universe = universe[~universe["name"].astype(str).str.contains("ST", na=False, regex=False)]
    return universe


def _run_full_screen(
    cfg: AppConfig,
    limit: int = 0,
    only_symbols: Optional[list[str]] = None,
) -> list[ScreeningResult]:
    fetcher = DataFetcher(
        proxy=cfg.data.akshare_proxy,
        cache_dir=cfg.data.cache_dir,
        cache_ttl_seconds=cfg.data.cache_ttl_seconds,
        max_workers=int(__import__("os").getenv("SCREENER_MAX_WORKERS", "16")),
    )
    resolver = ShenwanResolver(fetcher)
    resolver.warm()  # ponytail: pre-fill industry->members cache so the per-stock loop below hits cache

    universe = _build_universe(fetcher)
    if only_symbols is not None:
        wanted = set(only_symbols)
        universe = universe[universe["symbol"].isin(wanted)]
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

    medians = compute_industry_medians(resolver, symbol_to_metrics)
    for r in results:
        if r.metrics.industry_shenwan and r.metrics.industry_shenwan in medians:
            for key, val in medians[r.metrics.industry_shenwan].items():
                if not hasattr(r.metrics, key) or getattr(r.metrics, key) is None:
                    setattr(r.metrics, key, val)

    return sort_picks(results)


def _send(cfg: AppConfig, results: list[ScreeningResult], today: _dt.date,
         soft_picks: Optional[list[ScreeningResult]] = None,
         near_misses: Optional[list[ScreeningResult]] = None) -> None:
    if not cfg.email.recipients:
        logger.warning("EMAIL_RECIPIENTS not set; skipping email")
        return
    html_body = render_html(results, today, soft_picks=soft_picks, near_misses=near_misses)
    plain_body = render_plain_text(results, today, soft_picks=soft_picks, near_misses=near_misses)
    subject = (
        f"{cfg.email.subject_prefix} {today.isoformat()} "
        f"命中 {sum(1 for r in results if r.passes)} 只"
    )
    send_email(cfg.email, subject=subject, html_body=html_body, plain_body=plain_body)
    logger.info("Email sent to %s", ", ".join(cfg.email.recipients))


_CONTINUITY_LABELS = {"近3年股息率≥4%", "近3年扣非净利润 > 0"}

def _fallback_buckets(results, rules) -> tuple:
    """When 0 hard hits: split rejects into (软命中, 近1差1).

    * 软命中: the only failures are 3-year-continuity rules AND the most
      recent reported year still meets the dividend yield threshold. 持续至今满足.
    * 近1差1: exactly one hard rule failed.
    """
    soft = []
    near_miss = []
    for r in results:
        if r.passes:
            continue
        failed = set(r.failed_labels)
        if failed.issubset(_CONTINUITY_LABELS):
            history = r.metrics.dividend_yield_pct_history or []
            current = history[0].get("dividend_yield_pct_at_close") if history else None
            if current is not None and current >= rules.min_dividend_yield_pct:
                soft.append(r)
        if len(r.hard_fail_reasons) == 1:
            near_miss.append(r)
    soft.sort(key=lambda x: x.score, reverse=True)
    near_miss.sort(key=lambda x: x.score, reverse=True)
    return soft, near_miss

def cmd_run(cfg: AppConfig, limit: int = 0) -> int:
    results = _run_full_screen(cfg, limit=limit)
    today = _dt.date.today()
    has_hard = any(r.passes for r in results)
    soft, near_miss = _fallback_buckets(results, cfg.rules) if not has_hard else ([], [])
    files = write_outputs(cfg.output_dir, results, today, soft_picks=soft, near_misses=near_miss)
    logger.info("Outputs: %s", ", ".join(str(p) for p in files.values()))
    if soft:
        logger.info("Soft hits (持续至今满足): %d", len(soft))
    if near_miss:
        logger.info("Near-miss (1 fail): %d", len(near_miss))
    try:
        _send(cfg, results, today, soft, near_miss)
    except Exception as exc:
        logger.error("Email failed: %s", exc)
        return 1
    return 0


def _weekly_path(cfg: AppConfig, week: str) -> Path:
    return cfg.output_dir / f".weekly_{week}.json"


def _load_weekly(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_weekly(path: Path, store: dict) -> None:
    path.write_text(
        json.dumps(store, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _rebuild_result(rd: dict) -> ScreeningResult:
    # ponytail: compute_industry_medians setattrs median_*/mean_*/n onto r.metrics,
    # then cmd_weekly saves r.metrics.__dict__ -- those extras get stored.
    # StockMetrics is a @dataclass and rejects unknown kwargs. Filter to known fields.
    valid_keys = {f.name for f in _dc_fields(StockMetrics)}
    metrics = StockMetrics(**{k: v for k, v in rd.get("metrics", {}).items() if k in valid_keys})
    return ScreeningResult(
        metrics=metrics,
        passes=rd.get("passes", False),
        score=rd.get("score", 0.0),
        hard_fail_reasons=rd.get("hard_fail_reasons", []),
        soft_fail_reasons=rd.get("soft_fail_reasons", []),
        warnings=rd.get("warnings", []),
        failed_labels=rd.get("failed_labels", []),
    )


def cmd_weekly(cfg: AppConfig, chunk: int | None = None, push_weekday: int | None = None) -> int:
    """Incremental mode: screen the next ``chunk`` uncovered symbols, accumulate
    results across the ISO week, and push the email once coverage is complete
    or on ``push_weekday`` (default Friday). Already-screened symbols are read
    from the weekly store, so they are never re-fetched.
    """
    chunk = chunk or cfg.incremental_chunk
    push_weekday = push_weekday if push_weekday is not None else cfg.weekly_push_weekday
    today = _dt.date.today()
    iso = today.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"
    path = _weekly_path(cfg, week)
    store = _load_weekly(path)

    fetcher = DataFetcher(
        proxy=cfg.data.akshare_proxy,
        cache_dir=cfg.data.cache_dir,
        cache_ttl_seconds=cfg.data.cache_ttl_seconds,
        max_workers=int(__import__("os").getenv("SCREENER_MAX_WORKERS", "16")),
    )
    universe = _build_universe(fetcher)
    all_symbols = [str(s) for s in universe["symbol"]]
    pending = [s for s in all_symbols if s not in store]
    logger.info(
        "Weekly %s: store=%d pending=%d universe=%d",
        week, len(store), len(pending), len(all_symbols),
    )

    if pending:
        batch = pending[:chunk]
        results = _run_full_screen(cfg, only_symbols=batch)
        for r in results:
            store[r.metrics.symbol] = {
                "metrics": r.metrics.__dict__,
                "score": r.score,
                "passes": r.passes,
                "hard_fail_reasons": r.hard_fail_reasons,
                "soft_fail_reasons": r.soft_fail_reasons,
                "warnings": r.warnings,
                "failed_labels": r.failed_labels,
            }
        _save_weekly(path, store)
        logger.info("Added %d; coverage %d/%d", len(batch), len(store), len(all_symbols))

    coverage = len(store)
    complete = coverage >= len(all_symbols)
    push = bool(store) and (complete or today.weekday() == push_weekday)
    if not push:
        logger.info(
            "Not pushing yet (coverage %d/%d, weekday %d != push %d). Re-run daily to accumulate.",
            coverage, len(all_symbols), today.weekday(), push_weekday,
        )
        return 0

    results = sort_picks([_rebuild_result(rd) for rd in store.values()])
    has_hard = any(r.passes for r in results)
    soft, near_miss = _fallback_buckets(results, cfg.rules) if not has_hard else ([], [])
    files = write_outputs(cfg.output_dir, results, today, soft_picks=soft, near_misses=near_miss)
    logger.info("Weekly push: coverage %d/%d, outputs %s", coverage, len(all_symbols), list(files.values()))
    try:
        _send(cfg, results, today, soft, near_miss)
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
    sub.add_parser("weekly", help="incremental daily chunk; push when week complete / on push weekday")
    sub.add_parser("test", help="smoke test on a handful of tickers")
    args = parser.parse_args(argv)
    cfg = load_config()
    _setup_logging(cfg.log_level)

    if args.cmd == "run":
        return cmd_run(cfg, limit=getattr(args, "limit", 0))
    if args.cmd == "screen":
        return cmd_screen(cfg, limit=getattr(args, "limit", 0))
    if args.cmd == "weekly":
        return cmd_weekly(cfg)
    if args.cmd == "test":
        return cmd_test(cfg)
    parser.print_help()
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
