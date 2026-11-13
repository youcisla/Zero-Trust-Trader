"""The non-live A/B leg must charge the same spread as the live policy (#7).

`tools/exit_policy_ab.py` compares "trail only" (`live_exits=True`) with
"trail + rr TP" (`live_exits=False`). The fixed-target leg priced a long's entry
at the ask but priced short entries and every short exit at the bid, so a short
position paid no spread at all: the A/B credited the take-profit policy with a
cost-free market on one side.
"""
from __future__ import annotations

import pathlib
import sys
import unittest

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from _mt5stub import install  # noqa: E402

install()  # must precede marketdata / timemachine_mt5

import timemachine_mt5 as tm  # noqa: E402

SPREAD = 0.0002
RISK = 0.01
RR = 1.5


def bars(n: int = 10, high: float = 1.10005, low: float = 1.09995) -> dict:
    c = np.full(n, 1.10)
    return {"o": c.copy(), "h": np.full(n, high), "l": np.full(n, low),
            "c": c, "t": np.arange(n) * 900 + 1_700_000_000}


def trade_row(side, live, barset=None, horizon: int = 2):
    b = barset or bars()
    atr = np.full(len(b["c"]), 0.01)
    row, _exit_i = tm._trade_row(b, 0, side, 1.10, RISK, RR, horizon, atr,
                                 SPREAD, live, None)
    return row


class EqualCostsGiveEqualR(unittest.TestCase):
    def test_a_horizon_exit_costs_one_spread_each_side(self):
        long_r = trade_row("long", live=False)["R"]
        short_r = trade_row("short", live=False)["R"]
        self.assertAlmostEqual(long_r, -SPREAD / RISK, places=6)
        self.assertAlmostEqual(short_r, long_r, places=6,
                               msg="a short must pay the spread its long mirror pays")

    def test_the_fixed_target_leg_matches_the_live_leg(self):
        # Outcome labels differ by design (the fixed leg calls an untaken
        # barrier "neither", the live leg calls it "horizon"); the money must not.
        for side in ("long", "short"):
            with self.subTest(side=side):
                fixed = trade_row(side, live=False)
                live = trade_row(side, live=True)
                self.assertAlmostEqual(fixed["R"], live["R"], places=6)

    def test_a_short_stop_is_checked_at_the_ask(self):
        # High touches the stop only after the spread: 1.1099 + 0.0002 >= 1.1100
        out = trade_row("short", live=False, barset=bars(high=1.1099, low=1.0999))
        self.assertEqual(out["outcome"], "stop")
        self.assertAlmostEqual(out["R"], -1.0)


class TheToolSourcesTheValidatedSpread(unittest.TestCase):
    def test_the_ab_reads_the_spread_from_marketdata(self):
        src = (ROOT / "tools" / "exit_policy_ab.py").read_text(encoding="utf-8")
        self.assertIn("md.tick_spread(", src)
        self.assertNotIn("info.spread", src,
                         "a raw spread field bypasses tick_spread's validation")


if __name__ == "__main__":
    unittest.main()
