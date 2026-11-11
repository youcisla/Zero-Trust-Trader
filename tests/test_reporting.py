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
        "z_crit_corrected": 4.5, "results": [{"symbol": "FIXTURE", "strategy": "fixture", "side": "long",
          "significance": {"gate": "t", "z": 8, "t_stat_R": 5, "passes_corrected": True,
                           "passes_naive": True, "z_crit_bonferroni": 4.5},
          "permutation": {"passes": True, "p_value": .01},
          "history": {"avg_R": .1, "n_decided": 40, "win_rate": .25, "max_drawdown_R": -3.1},
          "current": {"side": "long", "proposed_entry": 1.1, "proposed_stop": 1.09,
                      "risk": 0.01},
          "tier2": {"class": "FX", "spread_pct_atr": 1.5}}]}


class TestTheWinnerPath(unittest.TestCase):
    """The one branch that prints a ticket had no test at all.

    Every other test in this file proves the report refuses something. This one
    proves it can say yes — and that it says no when the ledger's scan_id and the
    artifact's disagree.
    """

    def _run(self, doc, ledger_line):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "tier3_results.json").write_text(json.dumps(doc), encoding="utf-8")
            ledger = root / "funnel_history.log"
            ledger.write_text(ledger_line + "\n", encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(report_funnel, "T3", root / "tier3_results.json"), \
                 mock.patch.object(report_funnel, "LOG", ledger), \
                 mock.patch("sys.argv", ["report_funnel.py"]), contextlib.redirect_stdout(out):
                return report_funnel.main(), out.getvalue()

    def test_a_verified_survivor_prints_a_ticket(self):
        doc = proposal()
        rc, text = self._run(doc, f"2026-11-10T14:00:00Z | universe=1 tested=1 total_tests=100 "
                                  f"trials=100 cumulative=2305 scan_id={doc['scan_id']}")
        self.assertEqual(rc, 0, text)
        self.assertIn("ACTION", text.upper())

    def test_evidence_from_another_scan_is_refused(self):
        doc = proposal()
        rc, text = self._run(doc, "2026-11-10T14:00:00Z | universe=1 tested=1 total_tests=100 "
                                  "trials=100 cumulative=2305 scan_id=someone-else")
        self.assertEqual(rc, 2, text)
        self.assertIn("historical or mismatched", text)


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
