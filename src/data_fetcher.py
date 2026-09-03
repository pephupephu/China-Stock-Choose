"""Real-data fetchers for Chinese A-share screening.

All endpoints in this module come from publicly disclosed sources:

- 巨潮资讯网 (cninfo.com.cn)         - 历史分红 / 公告披露
- 新浪财经 (finance.sina.com.cn)       - 资产负债表 / 利润表 / 现金流量表
- 腾讯财经 (web.ifzq.gtimg.cn)         - 日 K 线（Tencent endpoint 替代 Eastmoney
                                          push2his；后者在国内代理下经常被墙）
- 同花顺 (10jqka.com.cn)               - 主营业务构成
- 申万指数 (Sina mirror)               - 行业分类 / 行业平均 PE

Third-party analyst ratings, opinion pieces, and social commentary are
explicitly avoided.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

import pandas as pd


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _symbol_to_tx(symbol: str) -> str:
    if len(symbol) != 6 or not symbol.isdigit():
        raise ValueError(f"unexpected symbol: {symbol!r}")
    if symbol.startswith(("60", "68", "11", "13")):
        return "sh" + symbol
    return "sz" + symbol


class DataFetcher:
    """Thin wrapper around akshare with on-disk caching, retries and proxy."""

    def __init__(
        self,
        proxy: Optional[str] = None,
        timeout: int = 60,
        max_retries: int = 3,
        sleep_seconds: float = 0.25,
        cache_dir: Path = Path("output/.cache"),
        cache_ttl_seconds: int = 6 * 3600,
        max_workers: int = 6,
    ) -> None:
        self.proxy = proxy
        self.timeout = timeout
        self.max_retries = max_retries
        self.sleep_seconds = sleep_seconds
        self.cache_dir = Path(cache_dir)
        self.cache_ttl_seconds = cache_ttl_seconds
        self.max_workers = max_workers
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if self.proxy:
            os.environ.setdefault("HTTP_PROXY", self.proxy)
            os.environ.setdefault("HTTPS_PROXY", self.proxy)
            os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost")
        import akshare as ak  # noqa: WPS433
        self._ak = ak
        self._hits = 0
        self._misses = 0

    # ---------------------------------------------------------------- cache
    def _cache_path(self, fn_name: str, args: tuple, kwargs: dict) -> Path:
        return self.cache_dir / f"{fn_name}__{_stable_hash({'a': args, 'k': kwargs})}.parquet"

    def _read_cache(self, path: Path) -> Optional[pd.DataFrame]:
        if not path.exists():
            return None
        age = time.time() - path.stat().st_mtime
        if age > self.cache_ttl_seconds:
            return None
        try:
            df = pd.read_parquet(path)
        except Exception:
            return None
        self._hits += 1
        return df

    def _write_cache(self, path: Path, df: pd.DataFrame) -> None:
        try:
            df.to_parquet(path, index=False)
        except Exception:
            json_path = path.with_suffix(".json")
            df.to_json(json_path, orient="records", force_ascii=False)

    def call(self, fn_name: str, *args: Any, force: bool = False, **kwargs: Any) -> pd.DataFrame:
        """Invoke an AKShare function with caching, retries and proxy handling."""
        path = self._cache_path(fn_name, args, kwargs)
        if not force:
            cached = self._read_cache(path)
            if cached is not None:
                return cached

        fn = getattr(self._ak, fn_name)
        last_err: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            pool = ThreadPoolExecutor(max_workers=1)
            future = pool.submit(fn, *args, **kwargs)
            try:
                df = future.result(timeout=self.timeout)
                if not isinstance(df, pd.DataFrame):
                    df = pd.DataFrame(df)
                self._misses += 1
                self._write_cache(path, df)
                return df
            except FuturesTimeoutError as exc:
                last_err = TimeoutError(f"akshare {fn_name} timed out after {self.timeout}s")
                break
            except Exception as exc:
                last_err = exc
                time.sleep(self.sleep_seconds * attempt)
            finally:
                pool.shutdown(wait=False)
        if last_err:
            raise last_err
        raise RuntimeError("akshare call failed without exception")

    # ---------------------------------------------------------------- domain

    def all_a_share_codes(self) -> pd.DataFrame:
        df = self.call("stock_info_a_code_name")
        df = df.rename(columns={"code": "symbol", "name": "name"})
        df["symbol"] = df["symbol"].astype(str)
        return df

    def dividend_history(self, symbol: str) -> pd.DataFrame:
        return self.call("stock_dividend_cninfo", symbol=symbol)

    def balance_sheet(self, symbol: str) -> pd.DataFrame:
        return self.call("stock_financial_report_sina", stock=symbol, symbol="资产负债表")

    def income_statement(self, symbol: str) -> pd.DataFrame:
        return self.call("stock_financial_report_sina", stock=symbol, symbol="利润表")

    def cashflow_statement(self, symbol: str) -> pd.DataFrame:
        return self.call("stock_financial_report_sina", stock=symbol, symbol="现金流量表")

    def daily_history(self, symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
        df = self.call(
            "stock_zh_a_hist_tx",
            symbol=_symbol_to_tx(symbol),
            start_date=start_date,
            end_date=end_date,
        )
        rename = {"date": "日期", "open": "开盘", "close": "收盘", "high": "最高", "low": "最低"}
        return df.rename(columns=rename)

    def main_business(self, symbol: str) -> pd.DataFrame:
        return self.call("stock_zyjs_ths", symbol=symbol)

    def shenwan_index_components(self, shenwan_code: str) -> list[str]:
        df = self.call("index_component_sw", symbol=shenwan_code)
        if isinstance(df, list):
            return [str(x).zfill(6) for x in df if x]
        if "证券代码" in df.columns:
            return df["证券代码"].astype(str).str.zfill(6).tolist()
        if "代码" in df.columns:
            return df["代码"].astype(str).str.zfill(6).tolist()
        return []

    def shenwan_index_history(self, shenwan_code: str) -> pd.DataFrame:
        return self.call("stock_industry_clf_hist_sw", symbol=shenwan_code)

    def industry_pe_ratio_cninfo(self, date: Optional[str] = None) -> pd.DataFrame:
        if date is None:
            return self.call("stock_industry_pe_ratio_cninfo")
        return self.call("stock_industry_pe_ratio_cninfo", date=date)


# Parallel fetch helpers ---------------------------------------------------
def fetch_one(
    fetcher: DataFetcher,
    symbol: str,
    name: str,
    today,
    shenwan_code: Optional[str] = None,
    shenwan_name: Optional[str] = None,
) -> dict[str, Any]:
    """Fetch everything for ONE stock into a flat dict, with safe exceptions."""
    payload: dict[str, Any] = {
        "symbol": symbol, "name": name,
        "shenwan_code": shenwan_code, "shenwan_name": shenwan_name,
        "income": None, "balance": None, "cashflow": None,
        "dividend": None, "main_biz": None, "price": None, "as_of": None,
    }
    try:
        end = today.strftime("%Y%m%d")
        start = today.replace(year=today.year - 1).strftime("%Y%m%d")
        price = fetcher.daily_history(symbol, start_date=start, end_date=end)
        if price is not None and not price.empty:
            price = price.dropna(subset=["收盘"])
            if not price.empty:
                payload["price"] = float(price.iloc[-1]["收盘"])
                payload["as_of"] = str(price.iloc[-1]["日期"])
    except Exception:
        pass
    try:
        payload["income"] = fetcher.income_statement(symbol)
    except Exception:
        pass
    try:
        payload["balance"] = fetcher.balance_sheet(symbol)
    except Exception:
        pass
    try:
        payload["cashflow"] = fetcher.cashflow_statement(symbol)
    except Exception:
        pass
    try:
        payload["dividend"] = fetcher.dividend_history(symbol)
    except Exception:
        pass
    try:
        payload["main_biz"] = fetcher.main_business(symbol)
    except Exception:
        pass
    return payload


def fetch_many(
    fetcher: DataFetcher,
    symbol_to_meta: dict[str, dict[str, str]],
    today,
    sleep_seconds: float = 0.05,
) -> dict[str, dict[str, Any]]:
    """Fetch many stocks in parallel -- returns {symbol: payload_dict}.

    ``symbol_to_meta`` maps symbol -> {name, shenwan_code, shenwan_name}.
    """
    out: dict[str, dict[str, Any]] = {}
    total = len(symbol_to_meta)
    if total == 0:
        return out
    syms = list(symbol_to_meta.keys())
    with ThreadPoolExecutor(max_workers=fetcher.max_workers) as pool:
        futures = {
            pool.submit(
                fetch_one,
                fetcher,
                sym,
                symbol_to_meta[sym]["name"],
                today,
                symbol_to_meta[sym].get("shenwan_code"),
                symbol_to_meta[sym].get("shenwan_name"),
            ): sym
            for sym in syms
        }
        done = 0
        for fut in as_completed(futures):
            sym = futures[fut]
            done += 1
            try:
                payload = fut.result()
            except Exception as exc:  # pragma: no cover
                logger.warning("fetch error for %s: %s", sym, exc)
                continue
            out[sym] = payload
            if done % 50 == 0:
                logger.info("已抓取 %d/%d 只", done, total)
    return out


# Wire up a module-level logger without importing logging above
import logging  # noqa: E402
logger = logging.getLogger("china-stock-choose.data")


def is_st_or_star(name: str) -> bool:
    upper = name.upper().replace(" ", "")
    return any(token in upper for token in ("ST", "*ST", "SST", "S*ST"))


def listing_date_from_first_filing(income: pd.DataFrame) -> Optional[str]:
    if income is None or income.empty:
        return None
    if "报告日" not in income.columns:
        return None
    try:
        earliest = pd.to_datetime(income["报告日"], errors="coerce").min()
        return earliest.strftime("%Y-%m-%d") if pd.notna(earliest) else None
    except Exception:
        return None
