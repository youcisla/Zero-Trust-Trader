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


class TestLedgerChain(unittest.TestCase):
    """Tamper-evidence: an edited or deleted row must not verify."""

    KEY = b"k" * 32

    def _chain(self, d, bodies):
        log = pathlib.Path(d) / "ledger.log"
        for body in bodies:
            prev = log.read_text(encoding="utf-8") if log.exists() else ""
            log.write_text(prev + tt.sign_line(body, log, self.KEY) + "\n", encoding="utf-8")
        return log

    def test_intact_chain_verifies(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._chain(d, ["a total_tests=10", "b total_tests=20"])
            self.assertEqual(tt.verify(log, self.KEY)[0], "ok")

    def test_edited_row_breaks_the_chain(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._chain(d, ["a total_tests=10", "b total_tests=20"])
            log.write_text(log.read_text(encoding="utf-8").replace("total_tests=20", "total_tests=2"),
                           encoding="utf-8")
            state, detail = tt.verify(log, self.KEY)
            self.assertEqual(state, "broken")
            self.assertIn("line", detail)

    def test_deleted_row_breaks_the_chain(self):
        with tempfile.TemporaryDirectory() as d:
            log = self._chain(d, ["a total_tests=10", "b total_tests=20", "c total_tests=30"])
            rows = log.read_text(encoding="utf-8").splitlines()
            log.write_text("\n".join([rows[0], rows[2]]) + "\n", encoding="utf-8")
            self.assertEqual(tt.verify(log, self.KEY)[0], "broken")

    def test_unsigned_append_warns_once_on_stderr(self):
        """Silent degradation is the failure mode this project keeps hitting."""
        import contextlib
        import io
        import tempfile
        from unittest import mock

        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "ledger.log"
            tt._WARNED_UNSIGNED = False
            err = io.StringIO()
            with mock.patch.object(tt, "ledger_key", lambda *a, **k: None), \
                    contextlib.redirect_stderr(err):
                first = tt.sign_line("body", log)
                second = tt.sign_line("body2", log)
            self.assertEqual(first, "body")
            self.assertEqual(second, "body2")
            self.assertEqual(err.getvalue().count("WARNING"), 1, "warn once, not per row")
            tt._WARNED_UNSIGNED = False

    def test_unsigned_rows_are_reported_not_trusted(self):
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "ledger.log"
            log.write_text("a total_tests=10\n", encoding="utf-8")
            self.assertEqual(tt.verify(log, self.KEY)[0], "unsigned")


class TestAudit(unittest.TestCase):
    """audit.jsonl is one JSON object per line, append-only."""

    def test_write_appends_one_parseable_object_per_line(self):
        import audit
        with tempfile.TemporaryDirectory() as d:
            original, audit.AUDIT = audit.AUDIT, pathlib.Path(d) / "audit.jsonl"
            try:
                audit.write("verdict", verdict="STAND_DOWN")
                audit.write("refused", problems=["pinned account missing"])
                records = audit.tail(10)
                raw = audit.AUDIT.read_text(encoding="utf-8")
            finally:
                audit.AUDIT = original
        self.assertEqual(raw.count("\n"), 2)
        self.assertEqual([r["event"] for r in records], ["verdict", "refused"])
        self.assertEqual(records[0]["verdict"], "STAND_DOWN")


if __name__ == "__main__":
    unittest.main()
