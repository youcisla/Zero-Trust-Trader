"""The Monte Carlo engine and the pure helpers, tested on synthetic data.

`simulate()` publishes `p_target` in every tier3 result document and had no test.
It is pure stdlib and seeded, so a property test is cheap: a pool whose every
forward bar returns +1% cannot miss a target that is inside that move.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import timemachine_mt5 as tm  # noqa: E402


def slot_pool(cyc: int = 96, up: float = 0.0, rows: int = 10) -> dict:
    """The shape `simulate()` consumes: slot -> [(open, high, low, close), ...]."""
    return {slot: [(1.0, 1.0 + up + 0.001, 1.0 - 0.001, 1.0 + up)] * rows
            for slot in range(cyc)}


class TheSlotIsTheBarWithinItsCycle(unittest.TestCase):
    def test_m15_boundaries(self):
        # 2026-11-10 14:59 UTC is slot 59 of an hour; 15:00 is 60.
        import datetime as dt
        t1459 = dt.datetime(2026, 11, 10, 14, 59, tzinfo=dt.timezone.utc).timestamp()
        t1500 = dt.datetime(2026, 11, 10, 15, 0, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(tm.slot_of(t1459, "M15"), 59)
        self.assertEqual(tm.slot_of(t1500, "M15"), 60)

    def test_d1_is_the_weekday(self):
        import datetime as dt
        monday = dt.datetime(2026, 11, 9, 12, tzinfo=dt.timezone.utc).timestamp()
        self.assertEqual(tm.slot_of(monday, "D1"), 0)


class Drawdown(unittest.TestCase):
    def test_monotonic_rise_has_no_drawdown(self):
        self.assertEqual(tm.max_drawdown([0.1, 0.2, 0.3, 0.4]), 0.0)

    def test_a_dip_is_measured_from_the_peak(self):
        # The contract is a NEGATIVE number: the worst peak-to-trough excursion.
        self.assertAlmostEqual(tm.max_drawdown([1.0, -0.5, 0.2]), -0.5)

    def test_empty_is_zero_not_an_error(self):
        self.assertEqual(tm.max_drawdown([]), 0.0)


class TheVerdictLine(unittest.TestCase):
    def test_three_branches_are_distinct(self):
        self.assertTrue(tm.verdict_line(5.0, 4.0).startswith("PASS"))
        self.assertTrue(tm.verdict_line(3.0, 4.0).startswith("FAIL"))
        self.assertTrue(tm.verdict_line(0.5, 4.0).startswith("FAIL"))
        # the naive/ corrected distinction is the one that matters
        self.assertIn("naive 1.96", tm.verdict_line(3.0, 4.0))

    def test_a_naive_pass_is_still_a_fail_against_the_corrected_bar(self):
        line = tm.verdict_line(3.0, 4.5)
        self.assertTrue(line.startswith("FAIL"))
        self.assertIn("luck", line)


class Simulate(unittest.TestCase):
    def test_a_target_inside_the_move_is_reached(self):
        # Every bar's high is above entry, so a long target just above entry hits.
        pool = slot_pool(up=0.01)
        hit = tm.simulate(pool, entry=1.0, stop=0.5, target=1.005, start_slot=0,
                          horizon=4, rr=1.5, replays=100, seed=7, window=0, side="long",
                          tf="M15")
        self.assertGreater(hit["p_target"], 0.99)

    def test_a_target_above_every_bar_is_never_reached(self):
        pool = slot_pool()
        hit = tm.simulate(pool, entry=1.0, stop=0.5, target=100.0, start_slot=0,
                          horizon=4, rr=1.5, replays=100, seed=7, window=0, side="long",
                          tf="M15")
        self.assertEqual(hit["p_target"], 0.0)

    def test_the_same_seed_gives_the_same_answer(self):
        pool = slot_pool(up=0.001)
        kw = dict(entry=1.0, stop=0.5, target=1.05, start_slot=0, horizon=4, rr=1.5,
                  replays=100, window=2, side="long", tf="M15")
        self.assertEqual(tm.simulate(pool, seed=11, **kw)["p_target"],
                         tm.simulate(pool, seed=11, **kw)["p_target"])

    def test_it_says_it_is_not_the_live_policy(self):
        pool = slot_pool()
        out = tm.simulate(pool, entry=1.0, stop=0.5, target=1.05, start_slot=0, horizon=4,
                          rr=1.5, replays=10, seed=1, window=0, side="long", tf="M15")
        self.assertFalse(out["policy_matches_live"])
        self.assertIn("Not a live-policy", out["limitations"])


if __name__ == "__main__":
    unittest.main()
