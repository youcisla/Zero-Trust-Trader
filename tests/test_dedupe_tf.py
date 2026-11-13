"""Tier-3 dedupe must measure correlation on the timeframe the scan used (#5).

Tier 2 dedupes on `--tf`; Tier 3's `dedupe()` called `correlation()` without a
timeframe, so it silently used the H1 default. Two symbols that are clones at
M15 could both reach the deep pass, and two that are independent at M15 could
lose one to an H1 correlation nobody was trading.
"""
from __future__ import annotations

import pathlib
import sys
import unittest
from unittest import mock

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from _mt5stub import install  # noqa: E402

install()  # must precede marketdata / tier3_deep

import marketdata as md          # noqa: E402
import tier3_deep as t3          # noqa: E402


def series(seed: int, n: int = 400) -> dict:
    rng = np.random.default_rng(seed)
    c = 1.10 * np.exp(np.cumsum(rng.normal(0, 0.0005, n)))
    return {"o": c.copy(), "h": c * 1.0002, "l": c * 0.9998, "c": c,
            "t": np.arange(n) * 900 + 1_700_000_000}


def fake_fetch(symbol: str, tf: str = "H1", bars: int = 3000, offset=None):
    """AAA and BBB are the same series at M15 and independent at H1."""
    if tf == "M15":
        return series(1)
    return series(1 if symbol == "AAA" else 2)


SURVIVORS = [{"symbol": "AAA", "significance": {"z": 6.0}},
             {"symbol": "BBB", "significance": {"z": 4.0}}]


class DedupeUsesTheScanTimeframe(unittest.TestCase):
    def test_a_pair_correlated_only_on_the_scan_timeframe_is_dropped(self):
        with mock.patch.object(md, "fetch", side_effect=fake_fetch):
            kept, dropped = t3.dedupe(SURVIVORS, 0.7, tf="M15")
        self.assertEqual([d["symbol"] for d in kept], ["AAA"])
        self.assertEqual([d["symbol"] for d in dropped], ["BBB"])
        self.assertAlmostEqual(dropped[0]["correlation"], 1.0, places=3)

    def test_the_same_pair_is_independent_on_another_timeframe(self):
        with mock.patch.object(md, "fetch", side_effect=fake_fetch):
            kept, _dropped = t3.dedupe(SURVIVORS, 0.7, tf="H1")
        self.assertEqual([d["symbol"] for d in kept], ["AAA", "BBB"])


if __name__ == "__main__":
    unittest.main()
