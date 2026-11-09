"""Tier 3 must treat "nothing triggered" as a result, not a crash.

Observed in production 2026-11-06T11:43Z: tier 2 found zero candidates, tier 3
exited rc=1 with nothing on stderr, the orchestrator logged FAILED, and the 100
hypotheses that scan had tested never reached the ledger. Under-counting trials
weakens the Bonferroni gate in the permissive direction, so this is a
statistical bug before it is a cosmetic one.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock


class TestEmptyShortlist(unittest.TestCase):
    def _run(self, payload: dict, extra_argv: list[str]):
        import tier3_deep as t3
        import trials_total

        with tempfile.TemporaryDirectory() as d:
            src = pathlib.Path(d) / "candidates.json"
            src.write_text(json.dumps(payload), encoding="utf-8")
            captured: dict = {}
            argv = ["tier3_deep.py", "--tier2", str(src)] + extra_argv
            # Nothing before the empty-shortlist branch touches MT5, so no
            # terminal is needed and no ledger row is written.
            with mock.patch.object(t3, "log_run",
                                   lambda s, p: captured.update(p)), \
                 mock.patch.object(t3, "OUT", pathlib.Path(d) / "tier3.json"), \
                 mock.patch.object(t3, "HERE", pathlib.Path(d)), \
                 mock.patch.object(trials_total, "required_trials", lambda count: 6500 + count), \
                 mock.patch.object(sys, "argv", argv):
                rc = t3.main()
        return rc, captured

    def test_no_candidates_logs_the_scan_and_exits_zero(self):
        rc, payload = self._run(
            {"top": [], "total_tests": 100, "symbols_tested": 50,
             "strategies_tested": 2, "triggering": 0},
            ["--trials", "6600"],
        )
        self.assertEqual(rc, 0, "an empty shortlist is not a failure")
        self.assertTrue(payload["stood_down"])
        self.assertEqual(payload["results"], [])
        self.assertEqual(payload["trials_declared"], 100,
                         "the 100 hypotheses this scan tested must still count")
        self.assertEqual(payload["trials_cumulative"], 6600,
                         "the cumulative bar must advance by what was tested")

    def test_cumulative_falls_back_to_the_ledger_total(self):
        # A hand-run must never gate on this scan alone, so with no --trials the
        # bar comes from the ledger (>= what this scan tested).
        rc, payload = self._run(
            {"top": [], "total_tests": 90, "symbols_tested": 45,
             "strategies_tested": 2, "triggering": 0},
            [],
        )
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(payload["trials_cumulative"], 90)


if __name__ == "__main__":
    unittest.main()
