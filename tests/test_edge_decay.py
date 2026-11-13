"""The recorder keeps a scan once; the report reads what it kept."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import edge_decay  # noqa: E402
import record_candidate_metrics  # noqa: E402


def candidate(symbol, strategy, passes, win_rate, stat=3.4, gate="z", occurrences=40):
    return {
        "symbol": symbol, "strategy": strategy,
        "history": {"win_rate": win_rate, "occurrences": occurrences},
        "significance": {"gate": gate, "z": stat if gate == "z" else None,
                         "t_stat_R": stat if gate == "t" else None,
                         "passes_corrected": passes, "passes_naive": True},
        "permutation": {"passes": passes, "p_value": 0.02},
    }


def payload(scan_id, candidates, generated_utc="2026-11-13 16:58:37"):
    return {
        "scan_id": scan_id, "generated_utc": generated_utc,
        "results": candidates,
        "passed_both_gates": [c["symbol"] for c in candidates
                              if c["significance"]["passes_corrected"]],
        "stood_down": not any(c["significance"]["passes_corrected"] for c in candidates),
        "trials_cumulative": 51684, "z_crit_corrected": 4.898,
    }


def flat(*args, **kwargs):
    """The candidate as the recorder writes it, which is what the report reads."""
    return record_candidate_metrics.snapshot(payload("x", [candidate(*args, **kwargs)]))["candidates"][0]


class TheRecorder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.out = self.root / "candidate_metrics.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def write_scan(self, doc):
        path = self.root / "tier3_results.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path

    def test_same_scan_id_twice_writes_one_line(self):
        path = self.write_scan(payload("abc123", [candidate("EURUSD", "donchian", False, 0.42)]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(record_candidate_metrics.record(path, self.out), 0)
            self.assertEqual(record_candidate_metrics.record(path, self.out), 0)
        self.assertEqual(len(self.out.read_text(encoding="utf-8").splitlines()), 1)

    def test_a_second_scan_appends(self):
        with contextlib.redirect_stdout(io.StringIO()):
            first = self.write_scan(payload("abc123", [candidate("EURUSD", "donchian", False, 0.42)]))
            record_candidate_metrics.record(first, self.out)
            second = self.write_scan(payload("def456", [candidate("EURUSD", "donchian", True, 0.50)]))
            record_candidate_metrics.record(second, self.out)
        rows = [json.loads(line) for line in self.out.read_text(encoding="utf-8").splitlines()]
        self.assertEqual([r["scan_id"] for r in rows], ["abc123", "def456"])

    def test_it_captures_per_candidate_fields(self):
        path = self.write_scan(payload("abc123", [
            candidate("EURUSD", "donchian", False, 0.42),
            candidate("XAUUSD", "bollinger", True, 0.55, stat=5.1, gate="t"),
        ]))
        with contextlib.redirect_stdout(io.StringIO()):
            record_candidate_metrics.record(path, self.out)
        row = json.loads(self.out.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(row["shortlist"], 2)
        self.assertEqual(row["trials_cumulative"], 51684)
        self.assertEqual(row["passed_both"], ["XAUUSD"])
        self.assertFalse(row["stood_down"])
        first, second = row["candidates"]
        self.assertEqual(first, {"symbol": "EURUSD", "strategy": "donchian", "gate": "z",
                                 "stat": 3.4, "win_rate": 0.42, "occurrences": 40,
                                 "passes_corrected": False, "permutation_passes": False})
        # A t gate records the t statistic, never the z beside it.
        self.assertEqual((second["gate"], second["stat"]), ("t", 5.1))


class TheReport(unittest.TestCase):
    def test_empty_history_says_so_and_exits_zero(self):
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "candidate_metrics.jsonl"
            with contextlib.redirect_stdout(out):
                code = edge_decay.main(["--history", str(missing)])
        self.assertEqual(code, 0)
        self.assertIn("no history yet", out.getvalue())

    def test_a_pass_that_later_fails_is_decay(self):
        rows = [{"scan_id": "a", "generated_utc": "2026-11-12 10:00:00",
                 "candidates": [flat("EURUSD", "donchian", True, 0.60)]},
                {"scan_id": "b", "generated_utc": "2026-11-13 10:00:00",
                 "candidates": [flat("EURUSD", "donchian", False, 0.45)]}]
        (trail,) = edge_decay.trails(rows).values()
        reasons = edge_decay.decay_reasons(trail)
        self.assertTrue(any("gate: passed" in r for r in reasons), reasons)
        self.assertTrue(any("win-rate falling" in r for r in reasons), reasons)

    def test_a_rising_win_rate_is_not_decay(self):
        rows = [{"scan_id": s, "generated_utc": f"2026-11-1{i} 10:00:00",
                 "candidates": [flat("EURUSD", "donchian", False, rate)]}
                for i, (s, rate) in enumerate(zip("abc", (0.40, 0.45, 0.50)))]
        (trail,) = edge_decay.trails(rows).values()
        self.assertEqual(edge_decay.decay_reasons(trail), [])

    def test_the_report_prints_the_trajectory(self):
        rows = [{"scan_id": "a", "generated_utc": "2026-11-12 10:00:00", "shortlist": 1,
                 "passed_both": [], "candidates": [flat("EURUSD", "donchian", False, 0.42)]}]
        text = "\n".join(edge_decay.report(rows))
        self.assertIn("EURUSD/donchian", text)
        self.assertIn("seen  1", text)


if __name__ == "__main__":
    unittest.main()
