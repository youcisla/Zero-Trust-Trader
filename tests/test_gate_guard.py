"""The PreToolUse guard must refuse the AGENTS.md §4 edits and nothing else.

A guard that blocks too much is as broken as one that blocks too little: the
first teaches an operator to delete the hook. So every deny case here is paired
with a near-miss that must pass.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

_spec = importlib.util.spec_from_file_location(
    "gate_guard", ROOT / ".claude" / "hooks" / "gate_guard.py")
gg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gg)


class TheFrozenTrialCount(unittest.TestCase):
    def test_lowering_it_is_refused(self):
        why = gg.check_edit("trials_total.py", "LEGACY_TRIALS = 1000")
        self.assertIn("LEGACY_TRIALS", why)

    def test_keeping_it_is_allowed(self):
        self.assertIsNone(gg.check_edit("trials_total.py", "LEGACY_TRIALS = 2205"))
        self.assertIsNone(gg.check_edit("trials_total.py", "# just a comment\n"))


class TheLockedPermutationCount(unittest.TestCase):
    def test_another_value_is_refused(self):
        self.assertIsNotNone(gg.check_edit("timemachine_mt5.py", "PERMUTATIONS = 300"))

    def test_the_locked_value_passes(self):
        self.assertIsNone(gg.check_edit("timemachine_mt5.py", "PERMUTATIONS = 1000"))

    def test_both_alpha_sites_are_guarded(self):
        self.assertIsNotNone(gg.check_edit("timemachine_mt5.py", "x(alpha: float = 0.10)"))
        self.assertIsNotNone(gg.check_edit(
            "timemachine_mt5.py", "(ge + 1) / (permutations + 1) < 0.10"))
        self.assertIsNone(gg.check_edit("timemachine_mt5.py", "x(alpha: float = 0.05)"))
        self.assertIsNone(gg.check_edit(
            "timemachine_mt5.py", "(ge + 1) / (permutations + 1) < 0.05"))


class TheSupervisorStaysDry(unittest.TestCase):
    def test_apply_on_a_launcher_is_refused(self):
        self.assertIsNotNone(gg.check_edit("start_supervisor.cmd",
                                           "position_manager.py --apply"))
        self.assertIsNotNone(gg.check_edit("run_desk.bat", "x --apply"))

    def test_apply_outside_a_launcher_is_not_this_guard_s_business(self):
        self.assertIsNone(gg.check_edit("position_manager.py", '"--apply"'))


class TheProtectedRuntimeFiles(unittest.TestCase):
    def test_deleting_the_ledger_is_refused_in_both_shells(self):
        for command in ("rm -f funnel_history.log",
                        "cd . && rm funnel_history.log",
                        "Remove-Item funnel_history.log",
                        "mv trades.csv /tmp/"):
            with self.subTest(command=command):
                self.assertIsNotNone(gg.check_command(command))

    def test_wiping_ignored_state_is_refused(self):
        self.assertIsNotNone(gg.check_command("git clean -fdx"))

    def test_committing_runtime_state_is_refused(self):
        self.assertIsNotNone(gg.check_command("git add funnel_history.log"))
        self.assertIsNotNone(gg.check_command("git add -A liquid_universe.json"))

    def test_rewriting_the_ledger_through_git_is_refused(self):
        self.assertIsNotNone(gg.check_command("git checkout -- funnel_history.log"))
        self.assertIsNotNone(gg.check_command("git restore stops.json"))

    def test_reading_and_searching_are_untouched(self):
        for command in ("grep -n rm funnel_history.log",
                        "cat funnel_history.log",
                        "python trials_total.py",
                        "python -m unittest discover -s tests",
                        "cd dashboard && cargo test --release",
                        "git status --porcelain"):
            with self.subTest(command=command):
                self.assertIsNone(gg.check_command(command))


class ThePayloadPath(unittest.TestCase):
    def test_a_write_carries_its_content(self):
        self.assertEqual(gg.new_text("Write", {"content": "abc"}), "abc")

    def test_an_edit_carries_its_replacement(self):
        self.assertEqual(gg.new_text("Edit", {"old_string": "a", "new_string": "b"}), "b")

    def test_a_multiedit_joins_its_parts(self):
        self.assertEqual(gg.new_text("MultiEdit", {"edits": [
            {"new_string": "a"}, {"new_string": "b"}]}), "a\nb")

    def test_a_malformed_payload_allows_the_call(self):
        self.assertIsNone(gg.new_text("Edit", {}) or None)


if __name__ == "__main__":
    unittest.main()
