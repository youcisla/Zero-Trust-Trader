"""The five-second check, the flat exit, and the break-even cushion."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import kill_switch  # noqa: E402
import position_manager as pm  # noqa: E402
import run_funnel as rf  # noqa: E402


class ThePreflight(unittest.TestCase):
    def test_a_kill_switch_stops_it_before_the_terminal_is_asked(self):
        saved = kill_switch.MARKER
        kill_switch.MARKER = ROOT / "KILL_SWITCH"
        try:
            kill_switch.MARKER.write_text("", encoding="utf-8")
            rc = rf.preflight_report()
        finally:
            kill_switch.MARKER.unlink(missing_ok=True)
            kill_switch.MARKER = saved
        self.assertEqual(rc, kill_switch.EXIT_KILLED)


class TheFlatExit(unittest.TestCase):
    def test_empty_is_flat_and_unknown_is_not(self):
        self.assertTrue(pm.flat_confirmed({"positions": []}))
        self.assertFalse(pm.flat_confirmed({"positions": None}))
        self.assertFalse(pm.flat_confirmed({}))
        self.assertFalse(pm.flat_confirmed({"positions": [{"ticket": 1}]}))


class TheBreakEvenCushion(unittest.TestCase):
    """`be_lock_r` is a lock past flat, on top of the spread the stop already pays."""

    def setUp(self):
        self.cfg = pm.Cfg(be_r=1.0, trail_k=2.0)

    def _be(self, cfg):
        # +1.2R of move, 2ATR trail below that would normally win the max/min.
        return pm.next_stop(1, 100.0, 90.0, 112.0, 100.0, 0.30, cfg)

    def test_default_locks_the_spread_and_nothing_more(self):
        stop, why = self._be(self.cfg)
        self.assertEqual(why, "break-even")
        self.assertAlmostEqual(stop, 100.30)  # entry + spread

    def test_a_cushion_locks_past_flat(self):
        stop, why = self._be(pm.Cfg(be_r=1.0, trail_k=2.0, be_lock_r=0.20))
        self.assertEqual(why, "break-even")
        self.assertAlmostEqual(stop, 102.30)  # entry + spread + 0.20 * 10 risk

    def test_a_negative_cushion_is_refused_and_the_stop_is_unchanged(self):
        stop, why = self._be(pm.Cfg(be_r=1.0, trail_k=2.0, be_lock_r=-1.0))
        self.assertIsNone(stop)
        self.assertEqual(why, "invalid risk inputs")


if __name__ == "__main__":
    unittest.main()
