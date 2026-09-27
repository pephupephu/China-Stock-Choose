"""Rotation bookkeeping test: a scan round must keep rolling across ISO weeks.

The old store was keyed by ISO week, so every Monday the rotation restarted
from the top of the universe and the tail was never scanned. This guards that
regression. Runs standalone (``python tests/test_rotation.py``) or under pytest.
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src import main
from src.config import AppConfig
from src.metrics import StockMetrics
from src.screener import ScreeningResult

UNIVERSE = ["000001", "000002", "000003", "000004", "000005"]


class _FakeDate:
    """Date shim so the test can jump across an ISO-week boundary."""

    value = dt.date(2026, 9, 21)  # Monday, ISO 2026-W39

    @classmethod
    def today(cls) -> dt.date:
        return cls.value


class _FakeFetcher:
    def __init__(self, **_kwargs):
        pass


def _make_result(symbol: str) -> ScreeningResult:
    return ScreeningResult(
        metrics=StockMetrics(symbol=symbol, name=symbol, close_price=10.0,
                             roe_ttm_pct=8.0, debt_ratio_pct=50.0),
        passes=symbol == "000003",
        score=1.0,
        hard_fail_reasons=["近3年股息率≥4%"],
    )


def test_rotation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        cfg = AppConfig(output_dir=Path(tmp))
        sent: list = []
        scanned: list = []

        def fake_screen(_cfg, limit=0, only_symbols=None):
            scanned.append(list(only_symbols))
            return [_make_result(s) for s in only_symbols]

        def fake_send(_cfg, results, today, soft=None, near=None, progress=None):
            sent.append((today.isoformat(), sorted(r.metrics.symbol for r in results), progress))

        def run(chunk=2) -> dict:
            main.cmd_daily(cfg, chunk=chunk)
            return json.loads(
                (cfg.output_dir / ".scan_store.json").read_text(encoding="utf-8")
            )

        def covered(store: dict) -> list[str]:
            return sorted(k for k in store if k != "__meta__")

        main.DataFetcher = _FakeFetcher  # type: ignore[assignment]
        main._build_universe = lambda _f: pd.DataFrame({"symbol": UNIVERSE, "name": UNIVERSE})
        main._prefilter_by_pe = lambda _f, u, _max: set(u["symbol"])
        main._run_full_screen = fake_screen
        main.write_outputs = lambda *_a, **_k: {}
        main._send = fake_send
        real_dt = main._dt
        main._dt = types.SimpleNamespace(date=_FakeDate)
        try:
            # Mon W39: first chunk.
            store = run()
            assert covered(store) == ["000001", "000002"], covered(store)
            assert sent[-1][2] == (2, 5, 2, 1), sent[-1]

            # Tue W39: next chunk, same round.
            _FakeDate.value = dt.date(2026, 9, 22)
            store = run()
            assert covered(store) == ["000001", "000002", "000003", "000004"], covered(store)

            # Next Monday (a NEW ISO week): keep rolling, finish the round.
            _FakeDate.value = dt.date(2026, 9, 28)
            store = run()
            assert scanned[-1] == ["000005"], scanned[-1]
            assert sent[-1][1] == UNIVERSE, sent[-1]  # round summary = whole round
            assert sent[-1][2] == (5, 5, 1, 1), sent[-1]
            assert store["__meta__"]["round"] == 2, store["__meta__"]
            assert covered(store) == [], covered(store)

            # Round 2 starts from the top again.
            _FakeDate.value = dt.date(2026, 9, 29)
            store = run()
            assert scanned[-1] == ["000001", "000002"], scanned[-1]
            assert sent[-1][2] == (2, 5, 2, 2), sent[-1]
        finally:
            main._dt = real_dt
    print("rotation test ok")


if __name__ == "__main__":
    test_rotation()
