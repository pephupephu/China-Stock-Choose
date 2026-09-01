"""Shenwan industry mapping and basic industry-level metric helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class ShenwanIndex:
    """Top-level 申万 industry index.

    ``name_cn``  - Chinese name, e.g. 农林牧渔
    ``pe_avg``   - weighted-average PE TTM (latest disclosure)
    ``pb_avg``   - weighted-average PB (latest disclosure)
    """

    code: str
    name_cn: str
    pe_avg: Optional[float] = None
    pb_avg: Optional[float] = None


SHENWAN_LV1 = [
    ("801010", "农林牧渔"),
    ("801020", "基础化工"),
    ("801030", "有色金属"),
    ("801040", "电子"),
    ("801050", "汽车"),
    ("801080", "机械设备"),
    ("801090", "国防军工"),
    ("801100", "家用电器"),
    ("801110", "纺织服饰"),
    ("801120", "食品饮料"),
    ("801130", "轻工制造"),
    ("801140", "医药生物"),
    ("801150", "公用事业"),
    ("801160", "交通运输"),
    ("801170", "房地产"),
    ("801180", "金融服务"),
    ("801200", "商贸零售"),
    ("801210", "社会服务"),
    ("801230", "综合"),
    ("801710", "建材"),
    ("801720", "建筑装饰"),
    ("801730", "电力设备"),
    ("801740", "国防军工"),
    ("801750", "计算机"),
    ("801760", "传媒"),
    ("801770", "通信"),
    ("801780", "银行"),
    ("801790", "非银金融"),
    ("801880", "汽车"),
    ("801890", "机械设备"),
    ("801950", "煤炭"),
    ("801960", "石油石化"),
    ("801970", "环保"),
    ("801980", "美容护理"),
]


class ShenwanResolver:
    """Lazy, cached shenwan industry -> symbols mapping.

    Construction is cheap (just an in-memory table). The first call to
    ``members(code)`` or ``for_symbol(symbol)`` triggers the API fetch
    behind a per-process LRU cache.
    """

    def __init__(self, fetcher) -> None:
        self.fetcher = fetcher
        self._member_cache: dict[str, list[str]] = {}
        self._symbol_to_code: dict[str, str] = {}

    def members(self, code: str) -> list[str]:
        if code in self._member_cache:
            return self._member_cache[code]
        try:
            symbols = self.fetcher.shenwan_index_components(code)
        except Exception:
            symbols = []
        # Cache even on failure so a flaky endpoint can't trigger a per-symbol
        # refetch storm (would otherwise be O(stocks x industries) network calls).
        self._member_cache[code] = symbols
        for s in symbols:
            self._symbol_to_code.setdefault(s, code)
        return symbols

    def for_symbol(self, symbol: str) -> Optional[str]:
        if symbol in self._symbol_to_code:
            return self._symbol_to_code[symbol]
        for code, name in SHENWAN_LV1:
            if code in self._member_cache and symbol in self._member_cache[code]:
                self._symbol_to_code[symbol] = code
                return code
            try:
                members = self.members(code)
                if symbol in members:
                    return code
            except Exception:
                continue
            time.sleep(0.1)
        return None

    def all_codes(self) -> list[tuple[str, str]]:
        return list(SHENWAN_LV1)


def compute_industry_medians(
    resolver: ShenwanResolver,
    symbol_to_metrics: dict[str, dict],
) -> dict[str, dict[str, float]]:
    """For each industry compute median ROE / PB / PE TTM of its members.

    ``symbol_to_metrics`` MUST already include ``roe_ttm_pct``, ``pb``,
    ``pe_ttm`` keys for every member in the universe you wish to rank.
    """
    out: dict[str, dict[str, float]] = {}
    for code, name in resolver.all_codes():
        try:
            members = resolver.members(code)
        except Exception:
            continue
        if not members:
            continue
        series = {k: [] for k in ("roe_ttm_pct", "pb", "pe_ttm")}
        for sym in members:
            m = symbol_to_metrics.get(sym)
            if not m:
                continue
            for k in series:
                v = m.get(k)
                if v is not None and v == v:  # not NaN
                    series[k].append(float(v))
        agg = {}
        for k, vals in series.items():
            if not vals:
                continue
            s = pd.Series(vals)
            agg[f"median_{k}"] = float(s.median())
            agg[f"mean_{k}"] = float(s.mean())
        if agg:
            agg["n"] = len(series["roe_ttm_pct"])
            out[code] = agg
    return out