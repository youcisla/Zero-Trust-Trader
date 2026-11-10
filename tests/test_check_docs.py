"""The document check must actually fail on a tree that disagrees.

It is the only guard on the architecture map's hand-typed numbers, and for a day
it was a check that could not run in CI (the numbers come from gitignored
artifacts). A check whose failure path is untested is a check that prints
"passed" - which is exactly the bug SEC-01 found in check_public.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import check_docs  # noqa: E402


def fixture(root: Path, *, universe: int = 248, shares: int = 210, tag: str | None = None,
            tag_shares: int = 210) -> None:
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "tools").mkdir(exist_ok=True)
    (root / "tools" / "known_tool.py").write_text("# documented below\n", encoding="utf-8")
    (root / "README.md").write_text("known_tool.py\n", encoding="utf-8")
    diagram = {
        "components": [
            {"id": "tier1", "tag": tag or f"1,712 → {universe}", "sources": []},
            {"id": "liquid", "tag": f"{tag_shares} shares", "sources": []},
            {"id": "tier2", "tag": f"{universe}–{universe * 3} tests/scan", "sources": []},
        ]
    }
    (root / "docs" / "architecture.archify.json").write_text(json.dumps(diagram), encoding="utf-8")
    (root / "liquid_universe.json").write_text(json.dumps(
        {"count": universe, "by_class": {"Shares": shares}}), encoding="utf-8")
    (root / "tier1_universe_20260101T000000Z.json").write_text(json.dumps(
        {"universe_total": 1712, "symbols": [{"symbol": "EURUSD"}]}), encoding="utf-8")


class TheCheckBites(unittest.TestCase):
    def test_a_matching_tree_passes(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(Path(d))
            failures, _ = check_docs.check(Path(d))
        self.assertEqual(failures, [])

    def test_a_stale_tag_is_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(Path(d), tag="1,723 → 120")
            failures, _ = check_docs.check(Path(d))
        self.assertTrue(any("tier1 tag" in f for f in failures), failures)

    def test_the_wrong_share_count_is_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(Path(d), shares=83, tag_shares=210)
            failures, _ = check_docs.check(Path(d))
        self.assertTrue(any("shares" in f for f in failures), failures)

    def test_an_undocumented_tool_is_a_failure(self):
        with tempfile.TemporaryDirectory() as d:
            fixture(Path(d))
            (Path(d) / "tools" / "secret_new_tool.py").write_text("", encoding="utf-8")
            failures, _ = check_docs.check(Path(d))
        self.assertTrue(any("secret_new_tool.py" in f for f in failures), failures)

    def test_an_empty_tier1_artifact_does_not_shadow_a_full_one(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            fixture(root)
            # A newer, stamped, EMPTY artifact must be skipped, as make_liquid does.
            (root / "tier1_universe_20991231T000000Z.json").write_text(
                json.dumps({"universe_total": 1712, "symbols": []}), encoding="utf-8")
            failures, _ = check_docs.check(root)
        self.assertEqual(failures, [], "the empty artifact shadowed the full one")

    def test_the_family_count_comes_from_the_matrix_source(self):
        self.assertEqual(check_docs.family_count(ROOT), 3)


if __name__ == "__main__":
    unittest.main()
