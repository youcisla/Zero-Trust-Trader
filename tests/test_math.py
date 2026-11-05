"""Indicator and significance maths, including the two bugs found in production.

Every case here is either a regression test for a real defect or a property that
must hold for the funnel's numbers to mean anything.
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

import marketdata as md          # noqa: E402
import timemachine_mt5 as tm     # noqa: E402


class TestRSI(unittest.TestCase):
    def test_flat_series_is_neutral(self):
        """REGRESSION: au/ad with ad==0 gave inf -> RSI 100 on a dead instrument,
        which reads as wildly overbought and fires a false short."""
        self.assertAlmostEqual(md.rsi(np.full(100, 1.1), 14)[-1], 50.0)

    def test_monotonic_rise_is_maximal(self):
        self.assertAlmostEqual(md.rsi(np.arange(100) * 0.001 + 1.1, 14)[-1], 100.0)

    def test_monotonic_fall_is_zero(self):
        self.assertAlmostEqual(md.rsi(np.arange(100, 0, -1) * 0.001 + 1.1, 14)[-1], 0.0)

    def test_bounds_and_warmup(self):
        c = np.cumsum(np.random.RandomState(0).randn(300)) + 100
        r = md.rsi(c, 14)
        self.assertTrue(np.all(np.isnan(r[:14])))
        self.assertTrue(np.nanmin(r) >= 0.0 and np.nanmax(r) <= 100.0)

    def test_too_short_returns_all_nan(self):
        self.assertTrue(np.all(np.isnan(md.rsi(np.arange(10.0), 14))))


class TestMACD(unittest.TestCase):
    def test_signal_line_exists(self):
        """REGRESSION: ema() seeds from x[:n].mean(); the MACD line starts with
        NaNs (EMA26 warms up after EMA12), poisoning the signal EMA so it stayed
        NaN forever and no cross ever fired — a dead strategy family."""
        c = np.cumsum(np.random.RandomState(1).randn(400)) + 100
        line, sig = md.macd(c)
        self.assertFalse(np.isnan(line[-1]), "MACD line should be live")
        self.assertFalse(np.isnan(sig[-1]), "MACD signal must not be all NaN")
        self.assertLess(int(np.isnan(line).sum()), len(c))

    def test_signal_is_smoother_than_line(self):
        c = np.cumsum(np.random.RandomState(2).randn(400)) + 100
        line, sig = md.macd(c)
        tail = ~np.isnan(sig)
        self.assertLess(np.std(np.diff(sig[tail])), np.std(np.diff(line[tail])))


class TestATR(unittest.TestCase):
    def test_warmup_then_positive(self):
        rs = np.random.RandomState(3)
        c = np.cumsum(rs.randn(200)) + 100
        h, l = c + 0.5, c - 0.5
        a = md.atr(h, l, c, 14)
        self.assertTrue(np.all(np.isnan(a[:14])))
        self.assertTrue(np.all(a[14:] > 0))

    def test_short_series_all_nan(self):
        self.assertTrue(np.all(np.isnan(md.atr([1.0] * 5, [1.0] * 5, [1.0] * 5, 14))))


class TestDonchian(unittest.TestCase):
    def test_channel_excludes_the_current_bar(self):
        """No look-ahead: bar i's channel must be built from bars i-n..i-1."""
        h = np.array([1.0, 2.0, 3.0, 100.0])
        l = np.array([2.0, 1.0, 0.5, 50.0])
        upper, lower = md.donchian(h, l, 2)
        self.assertTrue(np.isnan(upper[1]))
        self.assertEqual(upper[2], 2.0)   # max of bars 0..1, not bar 2
        self.assertEqual(lower[2], 1.0)   # min of bars 0..1
        self.assertEqual(upper[3], 3.0)   # 100.0 must NOT leak in


class TestBollinger(unittest.TestCase):
    def test_uses_population_sd(self):
        """ddof=0 deliberately: pandas rolling().std() defaults to ddof=1, which
        would make Tier 2 and Tier 3 disagree about whether price closed outside
        the band."""
        c = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        _m, up, lo = md.bollinger(c, 5, 2.0)
        mid, sd = c.mean(), c.std()          # ddof=0
        self.assertAlmostEqual(up[-1], mid + 2 * sd)
        self.assertAlmostEqual(lo[-1], mid - 2 * sd)

    def test_band_contains_mean(self):
        c = np.random.RandomState(4).rand(100) + 10
        _m, up, lo = md.bollinger(c, 20, 2.0)
        ok = ~np.isnan(up)
        self.assertTrue(np.all(lo[ok] <= up[ok]))


class TestBonferroni(unittest.TestCase):
    def test_known_values(self):
        """The bars quoted in README/TRIALS must be reproducible."""
        self.assertAlmostEqual(tm.bonferroni_z(150), 3.588, places=2)
        self.assertAlmostEqual(tm.bonferroni_z(525), 3.902, places=2)
        self.assertAlmostEqual(tm.bonferroni_z(1791), 4.190, places=2)

    def test_monotonic_in_tests(self):
        zs = [tm.bonferroni_z(n) for n in (1, 10, 100, 1_000, 100_000)]
        self.assertEqual(zs, sorted(zs), "the bar must rise with the search space")

    def test_single_hypothesis_equals_the_naive_threshold(self):
        """With one hypothesis the correction is a no-op: exactly 1.96."""
        self.assertAlmostEqual(tm.bonferroni_z(1), 1.96, places=2)


class TestSignificance(unittest.TestCase):
    def test_naive_vs_corrected(self):
        """A z of 2.40 is a finding at 1 test and noise at 568."""
        s = tm.significance(0.469, 304, 1.5, [0.19] * 304, n_tests=568)
        self.assertTrue(s["passes_naive"])
        self.assertFalse(s["passes_corrected"])

    def test_break_even_matches_rr(self):
        s = tm.significance(0.40, 100, 1.5, [0.0] * 100, n_tests=1)
        self.assertAlmostEqual(s["break_even"], 0.4)


class TestPermutationLock(unittest.TestCase):
    """The permutation gate must have identical resolution on every run.

    At 300 permutations the smallest attainable p-value is 1/301, so the same
    candidate can clear the bar on one run and miss it on the next. A gate that
    wobbles is not a gate, so the count is pinned and the CLI refuses anything
    else rather than silently clamping.
    """

    def test_both_modules_pin_the_same_value(self):
        import run_funnel
        import tier3_deep
        self.assertEqual(tier3_deep.PERMUTATIONS, 1000)
        self.assertEqual(run_funnel.PERMUTATIONS, 1000)
        self.assertEqual(tier3_deep.PERMUTATIONS, run_funnel.PERMUTATIONS,
                         "the two locked constants have drifted apart")

    def test_library_default_is_1000(self):
        import inspect
        param = inspect.signature(tm.permutation_test).parameters["permutations"]
        self.assertEqual(param.default, 1000)


if __name__ == "__main__":
    unittest.main()
