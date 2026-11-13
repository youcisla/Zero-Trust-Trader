"""A switch nobody can test is a switch nobody can trust."""

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import kill_switch  # noqa: E402
import position_manager  # noqa: E402


class TheKillSwitch(unittest.TestCase):
    def test_absence_is_not_consent_and_presence_says_where(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertIsNone(kill_switch.active(root, environ={}))
            (root / "KILL_SWITCH").write_text("", encoding="utf-8")
            why = kill_switch.active(root, environ={})
            self.assertIn("KILL_SWITCH", why)
            self.assertIn(str(root), why)

    def test_the_environment_is_a_switch_too(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertIsNone(kill_switch.active(root, environ={"ZTT_KILL_SWITCH": "0"}))
            self.assertIn("ZTT_KILL_SWITCH=1",
                          kill_switch.active(root, environ={"ZTT_KILL_SWITCH": "1"}))
            # Whitespace is not a value: " 1 " must still stop, "yes" must not.
            self.assertIn("ZTT_KILL_SWITCH=1",
                          kill_switch.active(root, environ={"ZTT_KILL_SWITCH": " 1 "}))
            self.assertIsNone(kill_switch.active(root, environ={"ZTT_KILL_SWITCH": "yes"}))

    def test_one_killed_pass_publishes_why_and_never_reads_the_broker(self):
        # The guard is ahead of the maintenance marker and the terminal import,
        # so this passes with no MetaTrader5 present at all.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "KILL_SWITCH").write_text("", encoding="utf-8")
            saved = kill_switch.MARKER
            kill_switch.MARKER = root / "KILL_SWITCH"
            try:
                doc = position_manager.run_once(
                    position_manager.Cfg(), apply=False, close_at=None)
            finally:
                kill_switch.MARKER = saved
        self.assertFalse(doc["ok"])
        self.assertIn("kill switch", doc["error"])
        self.assertIsNone(doc["positions"], "unknown, never an empty list")
        self.assertEqual(doc["actions"], [])
        self.assertFalse(doc["applied"])


if __name__ == "__main__":
    unittest.main()
