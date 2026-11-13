"""SECURITY-01 — the signed anchor detects a lowered trial count.

The chain alone catches an edit or a deletion in the middle, but a deletion of
the newest signed row leaves the chain `ok` and silently weakens the
Bonferroni bar. `ledger_head.json` binds the count and the chain tail with the
same key, so truncation, rollback and an emptied ledger all refuse.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import trials_total as tt  # noqa: E402

KEY = b"k" * 32


class AnchorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_log = tt.LOG
        tt.LOG = pathlib.Path(self.tmp.name) / "funnel_history.log"

    def tearDown(self):
        tt.LOG = self._orig_log
        self.tmp.cleanup()

    def _chain(self, *bodies):
        log = tt.LOG
        for body in bodies:
            prev = log.read_text(encoding="utf-8") if log.exists() else ""
            log.write_text(prev + tt.sign_line(body, log, KEY) + "\n", encoding="utf-8")
        return log

    def _anchor(self) -> pathlib.Path:
        return tt._anchor_path(tt.LOG)

    def test_bootstrap_creates_the_anchor_once_from_a_verified_chain(self):
        log = self._chain("a total_tests=10", "b total_tests=20")
        self.assertFalse(self._anchor().exists())
        state, detail = tt.verify(log, KEY)
        self.assertEqual(state, "ok")
        self.assertIn("anchor created", detail)
        rec = json.loads(self._anchor().read_text(encoding="utf-8"))
        self.assertEqual(rec["count"], tt.LEGACY_TRIALS + 30)
        # The second look finds the anchor and does not bootstrap again.
        state2, detail2 = tt.verify(log, KEY)
        self.assertEqual(state2, "ok")
        self.assertNotIn("anchor created", detail2)

    def test_a_read_only_check_does_not_seed_the_anchor(self):
        log = self._chain("a total_tests=10")
        state, detail = tt.verify(log, KEY, bootstrap=False)
        self.assertEqual(state, "ok")
        self.assertIn("no anchor yet", detail)
        self.assertFalse(self._anchor().exists(), "read-only checks must never write")
        # The next scan seeds it.
        self.assertEqual(tt.verify(log, KEY)[0], "ok")
        self.assertTrue(self._anchor().exists())

    def test_a_broken_chain_never_bootstraps_an_anchor(self):
        log = self._chain("a total_tests=10", "b total_tests=20")
        log.write_text(log.read_text(encoding="utf-8").replace(
            "total_tests=20", "total_tests=2"), encoding="utf-8")
        state, _ = tt.verify(log, KEY)
        self.assertEqual(state, "broken")
        self.assertFalse(self._anchor().exists(), "only an ok chain may seed an anchor")

    def test_deleting_the_newest_signed_row_is_truncated_and_refused(self):
        log = self._chain("a total_tests=10", "b total_tests=0")
        self.assertEqual(tt.verify(log, KEY)[0], "ok")  # anchors at 2215, tail=b
        rows = log.read_text(encoding="utf-8").splitlines()
        log.write_text("\n".join(rows[:-1]) + "\n", encoding="utf-8")
        state, _ = tt.verify(log, KEY)
        self.assertEqual(state, "truncated")
        self.assertTrue(tt.refuses(state, KEY))

    def test_deleting_a_counted_newest_row_lowers_the_count_and_is_refused(self):
        log = self._chain("a total_tests=10", "b total_tests=20")
        tt.verify(log, KEY)  # anchor count 2235, tail=b
        rows = log.read_text(encoding="utf-8").splitlines()
        log.write_text(rows[0] + "\n", encoding="utf-8")
        state, _ = tt.verify(log, KEY)
        self.assertNotEqual(state, "ok")
        self.assertTrue(tt.refuses(state, KEY))

    def test_a_count_below_the_anchor_is_rollback(self):
        log = self._chain("a total_tests=10")
        tt.verify(log, KEY)  # bootstrap: count 2215
        # A correctly signed anchor that claims more trials than the ledger holds.
        tt.write_anchor(999_999, tt.last_sig(log), key=KEY, path=log)
        state, detail = tt.verify(log, KEY)
        self.assertEqual(state, "rollback")
        self.assertIn("below the anchored", detail)
        self.assertTrue(tt.refuses(state, KEY))

    def test_an_edited_anchor_is_broken(self):
        log = self._chain("a total_tests=10")
        tt.verify(log, KEY)
        rec = json.loads(self._anchor().read_text(encoding="utf-8"))
        rec["count"] = 0  # an attacker who lowers the anchor without the key
        self._anchor().write_text(json.dumps(rec), encoding="utf-8")
        self.assertEqual(tt.verify(log, KEY)[0], "broken")

    def test_emptying_the_ledger_is_truncated_when_keyed(self):
        log = self._chain("a total_tests=10", "b total_tests=20")
        tt.verify(log, KEY)
        log.write_text("", encoding="utf-8")
        self.assertEqual(tt.verify(log, KEY)[0], "truncated")

    def test_empty_or_unsigned_is_refused_only_when_a_key_is_configured(self):
        self._chain()  # create the temp directory
        tt.LOG.write_text("a total_tests=10\n", encoding="utf-8")  # unsigned row
        state, _ = tt.verify(tt.LOG, KEY)
        self.assertEqual(state, "unsigned")
        self.assertTrue(tt.refuses(state, KEY), "a keyed tree must refuse unsigned")
        # No key anywhere: keyless dev runs proceed on unsigned and empty.
        self.assertFalse(tt.refuses("unsigned", None))
        self.assertFalse(tt.refuses("empty", None))
        self.assertTrue(tt.refuses("empty", KEY), "a keyed tree must refuse empty")


if __name__ == "__main__":
    unittest.main()
