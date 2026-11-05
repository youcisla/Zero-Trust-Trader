"""The trials ledger — the number the whole honesty argument rests on."""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import trials_total as tt  # noqa: E402


class TestTrialsTotal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = tt.LOG
        tt.LOG = pathlib.Path(self.tmp.name) / "funnel_history.log"

    def tearDown(self):
        tt.LOG = self._orig
        self.tmp.cleanup()

    def _write(self, *lines):
        tt.LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_missing_log_is_just_the_legacy_count(self):
        self.assertEqual(tt.total(), (tt.LEGACY_TRIALS, 0))

    def test_sums_scan_totals(self):
        self._write(
            "2026-11-05T13:00:00Z | universe=50 tested=50 total_tests=150 | STAND_DOWN",
            "2026-11-05T13:15:00Z | universe=50 tested=44 total_tests=132 | STAND_DOWN",
        )
        self.assertEqual(tt.total(), (tt.LEGACY_TRIALS, 282))

    def test_prefers_total_tests_over_trials(self):
        """A run line carries both fields with the same value; counting both
        would double the ledger."""
        self._write("2026-11-05T14:59:00Z | total_tests=1791 ... trials=1791 | STAND_DOWN")
        self.assertEqual(tt.total()[1], 1791)

    def test_failed_lines_are_ignored(self):
        self._write(
            "2026-11-05T14:33:00Z | FAILED tier1 rc=1 | NameError: name 'OUT'",
            "2026-11-05T13:00:00Z | total_tests=150 | STAND_DOWN",
        )
        self.assertEqual(tt.total()[1], 150)

    def test_empty_log(self):
        self._write()
        self.assertEqual(tt.total(), (tt.LEGACY_TRIALS, 0))

    def test_legacy_is_frozen(self):
        """LEGACY_TRIALS is history, not a knob. If this fails, someone has
        reset the ledger and every significance bar downstream is wrong."""
        self.assertEqual(tt.LEGACY_TRIALS, 2205)


class TestFailureRow(unittest.TestCase):
    def test_tail_never_splits_a_ledger_row(self):
        """A traceback joined with newlines used to turn one failed run into
        several ledger rows, which then read like a crash dump in status."""
        from run_funnel import tail

        traceback_text = (
            "Traceback (most recent call last):\n"
            '  File "tier1_universe.py", line 178, in main\n'
            '    OUT.write_text(payload)\n'
            "NameError: name 'OUT' is not defined\n"
        )
        out = tail(traceback_text, 3)
        self.assertNotIn("\n", out)
        self.assertIn("NameError", out)
        self.assertEqual(tail("", 3), "")


class TestNoCompounding(unittest.TestCase):
    def test_two_scans_add_their_own_tests_only(self):
        """Logging the running total made trials_total sum it again, so the
        significance bar doubled every scan until nothing could clear it."""
        import tempfile

        import tier3_deep as t3
        import trials_total as tt

        with tempfile.TemporaryDirectory() as d:
            tmp = pathlib.Path(d)
            old_here, old_log = t3.HERE, tt.LOG
            t3.HERE, tt.LOG = tmp, tmp / "funnel_history.log"
            try:
                src = {"symbols_in": 50, "symbols_tested": 44, "strategies_tested": 3,
                       "total_tests": 132, "triggering": 5, "deduped": []}
                payload = {"stood_down": True, "survivors": [], "results": [1, 2, 3],
                           "passed_both_gates": [], "permutations": 1000,
                           "trials_declared": 132, "trials_cumulative": 5388}
                t3.log_run(src, payload)
                t3.log_run(src, payload)
                _legacy, logged = tt.total()
            finally:
                t3.HERE, tt.LOG = old_here, old_log

            self.assertEqual(logged, 264, "two 132-test scans must add 264, not compound")


if __name__ == "__main__":
    unittest.main()
