"""The scorecard reads the desk's own record; a fixture proves the arithmetic."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import desk_scorecard  # noqa: E402
import desk_store  # noqa: E402


def fixture(root: Path, runs: int = 3, verdicts=("escalate", "stand_down", "stand_down")):
    db = root / "desk_history.sqlite"
    con = sqlite3.connect(db)
    con.executescript(desk_store.SCHEMA)
    for i in range(runs):
        con.execute("insert into runs (id, generated_utc, model, facts_sha, status,"
                    " prompt_tokens, completion_tokens, recorded_utc)"
                    " values (?,?,?,?,?,?,?,?)",
                    (i + 1, f"2026-11-1{i}T10:00:00Z", "deepseek-flash", "sha", "ok",
                     1000, 100, "2026-11-11T10:00:05Z"))
        con.execute("insert into answers (run_id, role, status, headline, payload) values (?,?,?,?,?)",
                    (i + 1, "chief", "ok", "ok", json.dumps({"agent": "chief", "summary": "s",
                                                             "contradictions": [], "watch": "w"})))
        con.execute("insert into answers (run_id, role, status, headline, payload) values (?,?,?,?,?)",
                    (i + 1, "jury", "ok" if i < len(verdicts) else "error", "ok",
                     json.dumps({"agent": "jury", "verdict": verdicts[i] if i < len(verdicts) else None,
                                 "why": "because" if i < len(verdicts) else None,
                                 "size_lots": 0.01})))
    con.commit()
    con.close()
    return db


class TheScorecard(unittest.TestCase):
    def test_it_counts_runs_roles_and_verdicts(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(Path(d))
            doc = desk_scorecard.report(Path(d))
        self.assertEqual(doc["runs"], 3)
        self.assertEqual(doc["verdicts"], {"escalate": 1, "stand_down": 2})
        self.assertEqual(doc["roles"]["chief"], {"ok": 3})
        self.assertEqual(doc["escalations_total"], 1)

    def test_a_missing_database_says_so_instead_of_printing_zero(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit) as ctx:
            desk_scorecard.report(Path(d))
        self.assertIn("no desk history", str(ctx.exception))

    def test_the_rendered_table_names_every_role(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(Path(d))
            text = desk_scorecard.render(desk_scorecard.report(Path(d)))
        for role in ("scout", "skeptic", "chief", "jury"):
            self.assertIn(role, text)
        self.assertIn("escalations    1 of 3 judged runs", text)
        # a number that means "one call" must not be printed as the run's cost
        self.assertNotIn("tokens/day", text)


if __name__ == "__main__":
    unittest.main()
