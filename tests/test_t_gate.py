"""The gate must measure money, not a proportion.

Under the live exit policy there is no +rr target, so p0 = 1/(1+rr) is a
break-even that no trade can reach. Measured on EURNZD M15: 13.6% win rate,
-0.670R per trade, and the win-rate z still cleared nothing while the
permutation null (scored the same way) reported p = 0.0233 -> passes = True.
These tests pin both failure modes: the z gate admitting a negative-expectancy
series, and the null passing a strategy that only lost more slowly than random.
"""

from __future__ import annotations

import pathlib
import statistics
import sys
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import numpy as np  # noqa: E402

import timemachine_mt5 as tm  # noqa: E402

ZC_50 = tm.bonferroni_z(50)  # ~3.29, a bar the fixtures below can clear


def mixture(win: float, win_R: float, loss_R: float, n: int = 250):
    """n trades with exactly the requested win rate and payoff shape."""
    k = int(round(win * n))
    return [win_R] * k + [loss_R] * (n - k)


def flat_bars(n: int = 400, seed: int = 3) -> dict:
    rng = np.random.default_rng(seed)
    c = 1.10 * np.exp(np.cumsum(rng.normal(0, 0.0004, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    pad = np.abs(c) * 0.0003
    return {"o": o, "h": np.maximum(o, c) + pad, "l": np.minimum(o, c) - pad,
            "c": c, "t": np.arange(n) * 900 + 1_700_000_000}


class TestTGateReplacesThePhantomBenchmark(unittest.TestCase):
    def test_t_from_matches_statistics(self):
        rs = [1.0, -1.0, 2.0, 0.5, -0.5]
        want = statistics.mean(rs) / (statistics.stdev(rs) / len(rs) ** 0.5)
        self.assertAlmostEqual(tm._t_from(rs), want, places=9)

    def test_t_from_is_zero_when_undecidable(self):
        self.assertEqual(tm._t_from([]), 0.0)
        self.assertEqual(tm._t_from([1.0, 2.0]), 0.0)      # fewer than 3
        self.assertEqual(tm._t_from([1.0, 1.0, 1.0]), 0.0)  # zero variance

    def test_low_win_high_R_passes_only_under_t(self):
        """35% at +2.5R / 65% at -0.8R: avg +0.355R, t ~ +3.6, z ~ -1.6."""
        rs = mixture(0.35, 2.5, -0.8)
        t_gate = tm.significance(0.35, len(rs), 1.5, rs, 50, use_t=True)
        z_gate = tm.significance(0.35, len(rs), 1.5, rs, 50, use_t=False)
        # 0.35 x 250 rounds to 88 winners, so the realised mean is +0.362R.
        self.assertGreater(statistics.mean(rs), 0.3)
        self.assertLess(z_gate["z"], 0.0)
        self.assertFalse(z_gate["passes_corrected"],
                         "the z gate cannot see a low-win/high-R edge")
        self.assertGreater(t_gate["t_stat_R"], ZC_50)
        self.assertTrue(t_gate["passes_corrected"],
                        "the t gate must admit a genuinely profitable asymmetrical edge")
        self.assertEqual(t_gate["gate"], "t")
        self.assertEqual(z_gate["gate"], "z")


    def test_significance_document_is_json_serialisable(self):
        """R values come from numpy scalars; leaking np.float64/np.bool_ into
        the document breaks `--json` on the one path that uses the t gate."""
        import json
        rs = [np.float64(v) for v in mixture(0.35, 2.5, -0.8)]
        for use_t in (True, False):
            doc = tm.significance(0.35, len(rs), 1.5, rs, 50, use_t=use_t)
            json.dumps(doc)  # raises if a numpy scalar escaped

    def test_scratch_heavy_negative_expectancy_is_refused_by_both_gates(self):
        """55% at +0.01R / 45% at -1.0R: win rate 55% (z ~ +4.8) but avg -0.44R."""
        rs = mixture(0.55, 0.01, -1.0)
        self.assertLess(statistics.mean(rs), -0.4)
        z_gate = tm.significance(0.55, len(rs), 1.5, rs, 50, use_t=False)
        t_gate = tm.significance(0.55, len(rs), 1.5, rs, 50, use_t=True)
        self.assertGreater(z_gate["z"], ZC_50)
        self.assertFalse(z_gate["passes_corrected"],
                         "a positive win-rate z must never override negative mean R")
        self.assertLess(t_gate["t_stat_R"], 0.0)
        self.assertFalse(t_gate["passes_corrected"],
                         "the t gate must reject negative expectancy regardless of win rate")


class TestPermutationNeverPassesANonPositiveExpectancy(unittest.TestCase):
    def setUp(self):
        self.bars = flat_bars()
        self.trades = [{"bar": 100 + i, "time": int(self.bars["t"][100 + i]),
                        "side": "long", "R": r, "outcome": "stop", "mae_R": 0.0,
                        "bars_held": 1}
                       for i, r in enumerate([-0.9, -1.0, -0.8, -1.1, -0.95] * 6)]

    def test_losing_strategy_cannot_pass_even_if_it_beats_the_null(self):
        p = tm.permutation_test(self.bars, self.trades, 1.5, 1.5, 32,
                                permutations=120, seed=19, live_exits=False,
                                use_t=True)
        self.assertIsNotNone(p)
        self.assertLess(p["actual_z"], 0.0)
        self.assertFalse(p["passes"],
                         "a negative-expectancy series must never pass the null")

    def test_statistic_key_names_what_was_tested(self):
        p_z = tm.permutation_test(self.bars, self.trades, 1.5, 1.5, 32,
                                  permutations=60, seed=19, live_exits=False)
        p_t = tm.permutation_test(self.bars, self.trades, 1.5, 1.5, 32,
                                  permutations=60, seed=19, live_exits=False,
                                  use_t=True)
        self.assertEqual(p_z["statistic"], "z")
        self.assertEqual(p_t["statistic"], "t")


class TestDedupeKeepsTheHigherEdgeUnderTheActiveGate(unittest.TestCase):
    def test_sort_and_dedupe_follow_the_gated_statistic(self):
        import tier3_deep as t3

        a = {"symbol": "AAA", "significance": {"gate": "t", "z": 9.0, "t_stat_R": 3.1}}
        b = {"symbol": "BBB", "significance": {"gate": "t", "z": 4.0, "t_stat_R": 6.2}}
        stat_key = "t_stat_R"
        survivors = sorted([a, b], key=lambda d: d["significance"][stat_key], reverse=True)
        with unittest.mock.patch.object(t3, "correlation", lambda *_, **__: 0.95):
            kept, dropped = t3.dedupe(survivors, 0.7, stat_key)
        self.assertEqual([d["symbol"] for d in kept], ["BBB"])
        self.assertEqual(dropped[0]["symbol"], "AAA")
        self.assertEqual(dropped[0]["t_stat_R"], 3.1)
        self.assertEqual(dropped[0]["gate"], "t")

    def test_default_key_stays_z_for_backward_compatibility(self):
        import tier3_deep as t3

        a = {"symbol": "AAA", "significance": {"z": 9.0}}
        b = {"symbol": "BBB", "significance": {"z": 4.0}}
        with unittest.mock.patch.object(t3, "correlation", lambda *_, **__: 0.95):
            kept, dropped = t3.dedupe([a, b], 0.7)
        self.assertEqual([d["symbol"] for d in kept], ["AAA"])
        self.assertEqual(dropped[0]["z"], 4.0)


if __name__ == "__main__":
    unittest.main()
