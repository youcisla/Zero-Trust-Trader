"""Reports must not upgrade missing, stale or contradictory evidence."""
import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import report
import report_funnel


def proposal():
    return {"scan_id": "fixture", "generated_utc": datetime.now(timezone.utc).isoformat(),
        "stood_down": False, "survivors": ["FIXTURE"], "trials_declared": 10000,
        "z_crit_corrected": 4.5, "results": [{"symbol": "FIXTURE", "strategy": "fixture",
          "significance": {"gate": "t", "z": 8, "t_stat_R": 5, "passes_corrected": True,
                           "passes_naive": True, "z_crit_bonferroni": 4.5},
          "permutation": {"passes": True, "p_value": .01}, "history": {"avg_R": .1},
          "current": {}, "tier2": {}}]}


class TestReportEvidence(unittest.TestCase):
    def test_failed_ledger_run_has_a_failure_exit(self):
        with mock.patch.object(report_funnel, "last_run", return_value="fixture | FAILED fixture"), \
             mock.patch("sys.argv", ["report_funnel.py"]), contextlib.redirect_stdout(io.StringIO()):
            self.assertNotEqual(report_funnel.main(), 0)

    def test_missing_gate_or_nonpositive_mean_is_not_a_proposal(self):
        for mean in (0, -.1, float("nan"), None):
            data = proposal()
            data["results"][0]["history"]["avg_R"] = mean
            self.assertEqual(report.verdict_block(data)[0], "UNKNOWN")
        data = proposal()
        data["results"][0]["permutation"] = {}
        self.assertEqual(report.verdict_block(data)[0], "UNKNOWN")

    def test_report_does_not_attribute_old_evidence_to_current_revision(self):
        html = report.render(proposal(), Path("fixture.json"))
        self.assertIn("not recorded", html)
        self.assertIn("independent single-use bridge consent", html)
        self.assertNotIn("ZTT_ALLOW_APPLY", html)

    def test_explanation_uses_the_actual_gate(self):
        data = proposal()["results"][0]
        data["significance"].update(t_stat_R=-2, passes_corrected=False, passes_naive=False)
        self.assertIn("t=-2.00", report_funnel.why_failed(data))

    def test_mismatched_or_invalid_timestamp_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = root / "tier3_results.json"
            ledger = root / "ledger.log"
            for scan_id, stamp in (("different", datetime.now(timezone.utc).isoformat()),
                                   ("fixture", "invalid"), ("fixture", "2000-01-01 00:00:00")):
                data = proposal()
                data["scan_id"], data["generated_utc"] = scan_id, stamp
                stage.write_text(json.dumps(data), encoding="utf-8")
                ledger.write_text("fixture | scan_id=fixture | PROPOSAL\n", encoding="utf-8")
                with mock.patch.object(report_funnel, "LOG", ledger), \
                     mock.patch.object(report_funnel, "T3", stage), \
                     mock.patch("sys.argv", ["report_funnel.py"]), \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertNotEqual(report_funnel.main(), 0)


if __name__ == "__main__":
    unittest.main()
