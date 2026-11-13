"""A number belongs to the candidate the note names.

The skeptic is shown several candidates' own test output at once, and the guard
used to accept any number found anywhere in that concatenation - so a note about
AAAUSD could cite BBBUSD's t-statistic and pass. Attribution is the point: a
number the named candidate did not report is a fabrication for that note.
"""

import json
import unittest

import desk

CANDIDATES = [
    {"symbol": "AAAUSD", "strategy": "donchian",
     "significance": {"t_stat_R": -19.78, "z_crit_bonferroni": 4.531}},
    {"symbol": "BBBUSD", "strategy": "bollinger",
     "significance": {"t_stat_R": -2.11, "z_crit_bonferroni": 4.531}},
]
SOURCE = "tier3: cumulative=8766\n" + json.dumps(CANDIDATES)


def _skeptic(symbol, reason):
    return {"agent": "skeptic", "symbol": symbol, "verdict": "noise",
            "reasons": [reason]}


class CitationAttribution(unittest.TestCase):
    def test_a_figure_from_another_candidate_is_rejected(self):
        out = desk._guard(_skeptic("AAAUSD", "its t of -2.11 is inside noise"),
                          SOURCE, "skeptic", json.dumps(CANDIDATES[0]))
        self.assertEqual(out["status"], "rejected")
        self.assertIn("2.11", out["reason"])

    def test_a_candidate_quoting_its_own_figure_is_accepted(self):
        out = desk._guard(_skeptic("AAAUSD", "its t of -19.78 is noise"),
                          SOURCE, "skeptic", json.dumps(CANDIDATES[0]))
        self.assertEqual(out["status"], "ok")

    def test_a_context_figure_outside_the_named_candidate_is_rejected(self):
        out = desk._guard(_skeptic("AAAUSD", "against cumulative 8766"),
                          SOURCE, "skeptic", json.dumps(CANDIDATES[0]))
        self.assertEqual(out["status"], "rejected")
        self.assertIn("8766", out["reason"])

    def test_no_known_candidate_falls_back_to_the_whole_source_with_a_warning(self):
        out = desk._guard(_skeptic("ZZZUSD", "its t of -19.78 is noise"),
                          SOURCE, "skeptic", None,
                          "note names 'ZZZUSD', not a candidate; figures matched "
                          "against the whole source")
        self.assertEqual(out["status"], "ok")
        self.assertIn("ZZZUSD", out["citation_warning"])

    def test_without_a_scope_the_whole_source_still_gates(self):
        out = desk._guard(_skeptic("AAAUSD", "its t of -44.4 is noise"),
                          SOURCE, "skeptic")
        self.assertEqual(out["status"], "rejected")
        self.assertIn("44.4", out["reason"])


if __name__ == "__main__":
    unittest.main()
