"""The desk's record: one row per run, one per answer, and no secrets in it.

The database is committed, so these tests are also the check that it cannot
carry a machine path or the broker login.
"""

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import desk_store


def notes(when="2026-11-10T09:00:00Z", **over):
    base = {"generated_utc": when, "model": "deepseek-flash", "facts_sha": "abc",
            "status": "ok", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "roles": {
                "scout": {"agent": "scout", "items": [{"symbols": ["EURUSD"]}, {"symbols": []}]},
                "skeptic": {"agent": "skeptic", "symbol": "EURUSD", "verdict": "noise"},
                "chief": {"agent": "chief", "summary": "stand down"},
                "jury": {"agent": "jury", "verdict": "stand_down", "size_lots": 0.01},
            }}
    base.update(over)
    return base


class RoundTripTests(unittest.TestCase):
    def test_a_run_is_one_row_and_each_role_one_answer(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            desk_store.record(root, notes())
            rows = desk_store.history(root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(sorted(rows[0]["roles"]), ["chief", "jury", "scout", "skeptic"])
        self.assertEqual(rows[0]["roles"]["jury"]["headline"], "stand_down")
        self.assertEqual(rows[0]["roles"]["scout"]["headline"], "2 items, 1 mapped")

    def test_the_same_run_is_never_stored_twice(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            desk_store.record(root, notes())
            desk_store.record(root, notes())
            rows = desk_store.history(root)   # inside the block: the temp dir owns the file
        self.assertEqual(len(rows), 1)

    def test_a_later_run_is_kept_beside_the_earlier_one(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            desk_store.record(root, notes("2026-11-10T09:00:00Z"))
            desk_store.record(root, notes("2026-11-10T09:15:00Z"))
            rows = desk_store.history(root)
        self.assertEqual([r["generated_utc"] for r in rows],
                         ["2026-11-10T09:15:00Z", "2026-11-10T09:00:00Z"])

    def test_every_answer_of_one_role_can_be_read_back(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            desk_store.record(root, notes("2026-11-10T09:00:00Z"))
            desk_store.record(root, notes("2026-11-10T09:15:00Z"))
            got = desk_store.answers(root, "jury")
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["payload"]["verdict"], "stand_down")

    def test_export_mirrors_the_newest_runs(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            desk_store.record(root, notes("2026-11-10T09:00:00Z"))
            path = desk_store.export(root)
            view = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(view["count"], 1)
        self.assertIn("jury", view["runs"][0]["roles"])

    def test_an_empty_tree_says_nothing_rather_than_zero(self):
        with TemporaryDirectory() as d:
            self.assertEqual(desk_store.history(Path(d)), [])


class NoSecretsTests(unittest.TestCase):
    def test_the_broker_login_is_redacted_before_it_is_stored(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / ".env").write_text("MT5_EXPECTED_LOGIN=62155913\n", encoding="utf-8")
            dirty = notes()
            dirty["roles"]["chief"]["summary"] = "account 62155913 on PepperstoneUK-Demo"
            desk_store.record(root, dirty)
            text = (root / desk_store.DB).read_bytes().decode("utf-8", "replace")
        self.assertNotIn("62155913", text)
        self.assertIn("PepperstoneUK-Demo", text)

    def test_a_machine_path_is_replaced(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            dirty = notes()
            dirty["roles"]["chief"]["summary"] = r"read C:\\Users\\someone\\tree\\x.log"
            desk_store.record(root, dirty)
            got = desk_store.answers(root, "chief")
        self.assertEqual(got[0]["payload"]["summary"], "read [path]")

    def test_a_clean_run_is_stored_unaltered(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            with mock.patch.dict("os.environ", {"MT5_EXPECTED_LOGIN": "62155913"}):
                dirty = notes()
                dirty["roles"]["chief"]["summary"] = "nothing to redact here"
                desk_store.record(root, dirty)
            got = desk_store.answers(root, "chief")
        self.assertEqual(got[0]["payload"]["summary"], "nothing to redact here")


if __name__ == "__main__":
    unittest.main()
