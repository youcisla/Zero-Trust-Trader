"""The reference distribution behind the gate.

The t-gate compares a Student-t statistic against a normal Bonferroni quantile
until this was fixed; at 25 trades the stated family-wise error was 26 times the
real one. These pin the quantiles against published tables, so a future change to
the incomplete beta cannot quietly move the bar.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import _mt5stub  # noqa: E402

_mt5stub.install()

import timemachine_mt5 as tm  # noqa: E402


class StudentT(unittest.TestCase):
    # Two-sided 5% critical values, standard tables.
    REFERENCE = {1: 12.706, 2: 4.303, 5: 2.571, 10: 2.228, 25: 2.060,
                 50: 2.009, 100: 1.984, 200: 1.972}

    def test_critical_values_match_the_tables(self):
        for df, want in self.REFERENCE.items():
            self.assertAlmostEqual(tm.student_t_crit(0.05, df), want, places=2,
                                   msg=f"df={df}")

    def test_the_tail_is_the_one_the_audit_measured(self):
        # z = 4.612 is the bar at 12,516 trials. Nominally p = 4.0e-06; at 25
        # trades the real tail is 26x that, which is why the bar was wrong.
        p = tm.student_t_p(4.612, 25)
        self.assertAlmostEqual(p / 4.0e-06, 26.0, delta=2.0)

    def test_a_normal_quantile_is_not_a_t_quantile_at_small_n(self):
        self.assertGreater(tm.student_t_crit(0.05, 5), 2.5)
        self.assertLess(tm.student_t_crit(0.05, 10_000), 1.97)

    def test_a_flat_series_is_not_significant(self):
        s = tm.significance(0.5, 40, 1.5, [0.0] * 40, 100, use_t=True)
        self.assertFalse(s["passes_corrected"])
        self.assertEqual(s["t_crit"], tm.student_t_crit(0.05 / 100, 39))

    def test_the_gate_uses_the_student_bar_when_it_should(self):
        # a tight, positive series: t far above any bar
        strong = [0.4 + 0.05 * (-1) ** i for i in range(20)]
        s = tm.significance(0.9, 20, 1.5, strong, 100, use_t=True)
        self.assertEqual(s["gate"], "t")
        self.assertGreater(s["t_crit"], 1.96)
        self.assertTrue(s["passes_corrected"])
        # t ~ 2.5: clears the naive 1.96, fails its own bar at 100 trials
        weak = [0.05 + 0.09 * (-1) ** i for i in range(20)]
        w = tm.significance(0.5, 20, 1.5, weak, 100, use_t=True)
        self.assertTrue(w["passes_naive"])
        self.assertFalse(w["passes_corrected"])


if __name__ == "__main__":
    unittest.main()
