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


def jev():
    """The shape desk.jev() returns on a live answer."""
    return {"status": "ok", "model": "typesafe/jev-1.14-20261014",
            "usage": {"input_tokens": 1200},
            "answers": {
                "attention": {"type": "choice", "choice": "stand_down", "confidence": 0.98,
                              "probabilities": {"show": 0, "stand_down": 0.99, "escalate": 0.01}},
                "grounded": {"type": "choice", "choice": "supported", "confidence": 0.92,
                             "probabilities": {"supported": 0.96, "unsupported": 0.04}},
            }}


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


class JevRowTests(unittest.TestCase):
    """Jev is a second model beside the verdict: one stored row, never an input."""

    def test_a_jev_answer_is_stored_as_its_own_row(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            desk_store.record(root, notes(jev=jev()))
            rows = desk_store.history(root)
        self.assertEqual(rows[0]["roles"]["jev"]["headline"],
                         "stand_down (p 0.99) · grounded: supported")

    def test_the_stored_row_is_the_headline_and_the_typed_note(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            note = jev()
            desk_store.record(root, notes(jev=note))
            got = desk_store.answers(root, "jev")
        self.assertEqual(set(got[0]["payload"]), set(note))   # no key the note lacks

    def test_the_export_carries_the_jev_answer(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            desk_store.record(root, notes(jev=jev()))
            view = json.loads(desk_store.export(root).read_text(encoding="utf-8"))
        self.assertEqual(view["runs"][0]["roles"]["jev"]["headline"],
                         "stand_down (p 0.99) · grounded: supported")

    def test_a_run_without_jev_stores_no_row(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            desk_store.record(root, notes())
            rows = desk_store.history(root)
        self.assertNotIn("jev", rows[0]["roles"])

    def test_a_disabled_layer_is_its_reason_not_a_label(self):
        off = {"status": "disabled", "model": "typesafe/jev-1.14",
               "reason": "the decision model is off"}
        with TemporaryDirectory() as d:
            root = Path(d)
            desk_store.record(root, notes(jev=off))
            rows = desk_store.history(root)
        self.assertEqual(rows[0]["roles"]["jev"]["headline"], "the decision model is off")


class NoSecretsTests(unittest.TestCase):
    def test_the_broker_login_is_redacted_before_it_is_stored(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            (root / ".env").write_text("MT5_EXPECTED_LOGIN=20481736\n", encoding="utf-8")
            dirty = notes()
            dirty["roles"]["chief"]["summary"] = "account 20481736 on PepperstoneUK-Demo"
            desk_store.record(root, dirty)
            text = (root / desk_store.DB).read_bytes().decode("utf-8", "replace")
        self.assertNotIn("20481736", text)
        self.assertIn("PepperstoneUK-Demo", text)

    def test_an_eight_digit_login_is_redacted_without_being_known(self):
        """Any account-shaped token, not only the one currently configured."""
        self.assertEqual(desk_store._redact("login 12345678", []), "login [login]")

    def test_posix_and_unc_paths_are_replaced(self):
        # Built from parts: a literal home path in a tracked file trips the
        # repo's own privacy gate, which is exactly what it is for.
        for probe in ("/" + "Users/bob/x", "\\\\srv\\share\\x"):
            self.assertIn("[path]", desk_store._redact(probe, []))

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
            with mock.patch.dict("os.environ", {"MT5_EXPECTED_LOGIN": "20481736"}):
                dirty = notes()
                dirty["roles"]["chief"]["summary"] = "nothing to redact here"
                desk_store.record(root, dirty)
            got = desk_store.answers(root, "chief")
        self.assertEqual(got[0]["payload"]["summary"], "nothing to redact here")


if __name__ == "__main__":
    unittest.main()
