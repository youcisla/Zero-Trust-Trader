"""The report must compare a candidate's statistic to the bar it was gated on (#4).

Under `--t-gate` the statistic is a Student-t and its bar comes from the t
distribution at that candidate's df. The HTML card and the funnel ticket printed
the t statistic beside the normal Bonferroni z bar, which reads as a miss when
the candidate actually passed (or a pass when it did not).
"""
from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from _mt5stub import install  # noqa: E402

install()  # must precede marketdata / report_funnel

import report          # noqa: E402
import report_funnel   # noqa: E402


def doc(gate: str = "t") -> dict:
    return {"scan_id": "fixture", "generated_utc": datetime.now(timezone.utc).isoformat(),
        "stood_down": False, "survivors": ["FIXTURE"], "trials_declared": 10000,
        "trials_cumulative": 2305, "z_crit_corrected": 4.5,
        "results": [{"symbol": "FIXTURE", "strategy": "fixture", "side": "long",
          "significance": {"gate": gate, "z": 9.0, "t_stat_R": 3.2, "t_crit": 2.9,
                           "passes_corrected": True, "passes_naive": True,
                           "z_crit_bonferroni": 4.5, "n_tests": 2305,
                           "mean_net_R": 0.12},
          "permutation": {"passes": True, "p_value": 0.01, "permutations": 1000,
                          "null_z_mean": 0.0},
          "history": {"avg_R": 0.12, "n_decided": 40, "win_rate": 0.25,
                      "max_drawdown_R": -3.1},
          "current": {"side": "long", "proposed_entry": 1.1, "proposed_stop": 1.09,
                      "risk": 0.01},
          "tier2": {"class": "FX", "spread_pct_atr": 1.5}}]}


class TheHtmlCardUsesTheTBar(unittest.TestCase):
    def test_the_t_statistic_is_shown_against_its_own_bar(self):
        html = report.candidate_card(doc("t")["results"][0])
        self.assertIn("2.9", html, "the t bar must appear beside the t statistic")
        self.assertNotIn("4.5", html, "the normal bar must not gate a t statistic")

    def test_the_z_gate_still_uses_the_bonferroni_z_bar(self):
        html = report.candidate_card(doc("z")["results"][0])
        self.assertIn("4.5", html)
        self.assertNotIn("2.9", html)


class TheTicketUsesTheTBar(unittest.TestCase):
    def _run(self, data):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            stage = root / "tier3_results.json"
            stage.write_text(json.dumps(data), encoding="utf-8")
            ledger = root / "funnel_history.log"
            ledger.write_text(f"2026-11-10T14:00:00Z | universe=1 tested=1 total_tests=100 "
                              f"trials=100 cumulative=2305 scan_id={data['scan_id']}\n",
                              encoding="utf-8")
            out = io.StringIO()
            with mock.patch.object(report_funnel, "T3", stage), \
                 mock.patch.object(report_funnel, "LOG", ledger), \
                 mock.patch("sys.argv", ["report_funnel.py"]), \
                 contextlib.redirect_stdout(out):
                rc = report_funnel.main()
        return rc, out.getvalue()

    def test_the_evidence_line_uses_the_t_bar(self):
        rc, text = self._run(doc("t"))
        self.assertEqual(rc, 0, text)
        self.assertIn("vs corrected 2.90", text)
        self.assertNotIn("vs corrected 4.50", text)

    def test_the_evidence_line_uses_the_z_bar_for_the_z_gate(self):
        rc, text = self._run(doc("z"))
        self.assertEqual(rc, 0, text)
        self.assertIn("vs corrected 4.50", text)


if __name__ == "__main__":
    unittest.main()
