"""The anchor must not change what a clean ledger counts.

count must be byte-for-byte the number the parent produced: total_tests is
preferred over trials, FAILED rows still count, and one scan_id counts once. If
this drifts, the bar the whole honesty argument rests on drifts with it.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import trials_total as tt  # noqa: E402

KEY = b"k" * 32

# 150 + 132 + 1791 are ordinary rows; 100 is one scan counted once despite two
# FAILED rows; total_tests wins over trials on the last row (1791, not 3582).
CLEAN = [
    "2026-11-05T13:00:00Z | universe=50 tested=50 total_tests=150 | STAND_DOWN",
    "2026-11-05T13:15:00Z | universe=50 tested=44 total_tests=132 | STAND_DOWN",
    "FAILED tier2 rc=1 total_tests=100 scan_id=abc",
    "FAILED tier2 rc=1 total_tests=100 scan_id=abc",
    "2026-11-05T14:59:00Z | total_tests=1791 trials=1791 | STAND_DOWN",
]
EXPECTED_LOGGED = 150 + 132 + 100 + 1791


class CleanLedgerCount(unittest.TestCase):
    def test_plain_ledger_counts_as_the_parent_did(self):
        self.assertEqual(tt.total(lines=list(CLEAN)), (tt.LEGACY_TRIALS, EXPECTED_LOGGED))

    def test_signing_and_anchoring_leave_the_count_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            log = pathlib.Path(d) / "funnel_history.log"
            original, tt.LOG = tt.LOG, log
            try:
                for body in CLEAN:
                    prev = log.read_text(encoding="utf-8") if log.exists() else ""
                    log.write_text(prev + tt.sign_line(body, log, KEY) + "\n",
                                   encoding="utf-8")
                before = tt.total()
                state, _ = tt.verify(log, KEY)  # bootstraps the anchor
                self.assertEqual(state, "ok")
                self.assertEqual(tt.total(), before)
                self.assertEqual(before, (tt.LEGACY_TRIALS, EXPECTED_LOGGED))
            finally:
                tt.LOG = original


if __name__ == "__main__":
    unittest.main()
