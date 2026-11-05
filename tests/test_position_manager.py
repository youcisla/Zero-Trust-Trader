"""The money path. If these fail, the supervisor can enlarge a loss.

`next_stop()` is the only function that decides whether a stop moves, so the
protective-direction rule is tested here once rather than trusted everywhere.
"""

from __future__ import annotations

import pathlib
import random
import sys
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

from position_manager import Cfg, close_due, next_stop  # noqa: E402

CFG = Cfg()
LONG, SHORT = 1, -1


class TestProtectiveDirection(unittest.TestCase):
    def test_long_stop_never_moves_down(self):
        new, _ = next_stop(LONG, 1.1000, 1.0950, 1.1030, 0.0020, 0.0002, CFG)
        self.assertIsNotNone(new)
        self.assertGreater(new, 1.0950)

    def test_short_stop_never_moves_up(self):
        new, _ = next_stop(SHORT, 1.1000, 1.1050, 1.0970, 0.0020, 0.0002, CFG)
        self.assertIsNotNone(new)
        self.assertLess(new, 1.1050)

    def test_rejects_a_worse_existing_stop(self):
        """Stop already better than anything we would compute: leave it alone."""
        new, why = next_stop(LONG, 1.1000, 1.0995, 1.0990, 0.0020, 0.0002, CFG)
        self.assertIsNone(new)
        self.assertEqual(why, "no improvement")

    def test_stop_stays_the_safe_side_of_price(self):
        """A long stop at or above the live price would close the trade instantly
        and, worse, could invert into a guaranteed loss."""
        for price in (1.1010, 1.1050, 1.2000):
            new, _ = next_stop(LONG, 1.1000, 1.0950, price, 0.0020, 0.0002, CFG)
            if new is not None:
                self.assertLess(new, price, f"long stop {new} >= price {price}")

    def test_property_never_widens_risk(self):
        """Random sweep: for either side, a returned stop must strictly reduce
        distance to price, and never cross it."""
        rng = random.Random(7)
        for _ in range(3000):
            side = rng.choice((LONG, SHORT))
            entry = rng.uniform(1.0, 2.0)
            atr = rng.uniform(0.0005, 0.02)
            spread = atr * 0.1
            sl = entry - side * rng.uniform(0.5, 3.0) * atr
            price = entry + side * rng.uniform(-1.0, 4.0) * atr
            new, _why = next_stop(side, entry, sl, price, atr, spread, CFG)
            if new is None:
                continue
            if side == LONG:
                self.assertLess(new, price, "long stop crossed price")
                self.assertGreater(new, sl, "long stop moved away from price")
            else:
                self.assertGreater(new, price, "short stop crossed price")
                self.assertLess(new, sl, "short stop moved away from price")


class TestBreakEven(unittest.TestCase):
    # Fixtures sit clear of the exact 1R boundary: at exactly 1R the trigger is
    # decided by floating-point equality, which is not a property worth pinning.
    def test_moves_to_entry_plus_spread_at_one_r(self):
        """risk 0.005, ATR large enough that the trail is not the better candidate."""
        new, why = next_stop(LONG, 1.1000, 1.0950, 1.1055, 0.0100, 0.0002, CFG)
        self.assertEqual(why, "break-even")
        self.assertAlmostEqual(new, 1.1002, places=6)

    def test_not_before_one_r(self):
        new, why = next_stop(LONG, 1.1000, 1.0950, 1.1030, 0.0020, 0.0002, CFG)
        self.assertNotEqual(why, "break-even")
        self.assertLess(new, 1.1002)

    def test_break_even_covers_costs_not_just_entry(self):
        """Moving to exactly entry leaves the trade negative after commission."""
        new, _ = next_stop(LONG, 1.1000, 1.0950, 1.1055, 0.0100, 0.0005, CFG)
        self.assertGreater(new, 1.1000)

    def test_short_mirror(self):
        new, why = next_stop(SHORT, 1.1000, 1.1050, 1.0945, 0.0100, 0.0002, CFG)
        self.assertEqual(why, "break-even")
        self.assertAlmostEqual(new, 1.0998, places=6)


class TestTrailingAndChurn(unittest.TestCase):
    def test_trail_follows_price(self):
        a, _ = next_stop(LONG, 1.1000, 1.0950, 1.1030, 0.0020, 0.0002, CFG)
        b, _ = next_stop(LONG, 1.1000, 1.0950, 1.1060, 0.0020, 0.0002, CFG)
        self.assertGreater(b, a)

    def test_ignores_micro_adjustments(self):
        """0.1 ATR floor: without it, every tick sends a modify request."""
        new, why = next_stop(LONG, 1.1000, 1.0974, 1.1015, 0.0020, 0.0002, CFG)
        self.assertIsNone(new)
        self.assertEqual(why, "below min step")

    def test_zero_atr_is_refused(self):
        self.assertEqual(next_stop(LONG, 1.1, 1.09, 1.12, 0.0, 0.0, CFG), (None, "no atr"))


class TestSeed(unittest.TestCase):
    def test_position_without_a_stop_gets_one(self):
        new, why = next_stop(LONG, 1.1000, 0.0, 1.1000, 0.0020, 0.0002, CFG)
        self.assertEqual(why, "seed stop")
        self.assertAlmostEqual(new, 1.0960)

    def test_seed_respects_side(self):
        new, _ = next_stop(SHORT, 1.1000, 0.0, 1.1000, 0.0020, 0.0002, CFG)
        self.assertGreater(new, 1.1000)


class TestSessionClose(unittest.TestCase):
    def _at(self, h, m, day=5):
        """2026-11-05 is a Thursday; day=7 is Saturday."""
        return datetime(2026, 11, day, h, m, tzinfo=timezone.utc)

    def test_before_and_after(self):
        self.assertFalse(close_due(self._at(20, 44), "20:45", 4))
        self.assertTrue(close_due(self._at(20, 45), "20:45", 4))
        self.assertTrue(close_due(self._at(23, 0), "20:45", 4))

    def test_no_close_on_weekend(self):
        """Saturday: the broker settled already; a close attempt would error."""
        self.assertFalse(close_due(self._at(21, 0, day=7), "20:45", 6))

    def test_disabled_when_no_time(self):
        self.assertFalse(bool(None) and close_due(self._at(23, 0), "20:45", 4))


class TestStateWrite(unittest.TestCase):
    """Windows raises WinError 5 when another process holds the destination
    open. The dashboard polls it once a second, so losing that race must not
    kill supervision."""

    def test_retries_while_the_reader_holds_the_file(self):
        import pathlib
        import tempfile

        import position_manager as pm

        with tempfile.TemporaryDirectory() as d:
            orig, pm.STATE = pm.STATE, pathlib.Path(d) / "engine_state.json"
            real, seen = pathlib.Path.replace, {"n": 0}

            def flaky(self, target):
                seen["n"] += 1
                if seen["n"] < 3:
                    raise PermissionError(13, "Access is denied")
                return real(self, target)

            pathlib.Path.replace = flaky
            try:
                pm.write_state({"ok": True})
            finally:
                pathlib.Path.replace = real
                pm.STATE = orig

            self.assertEqual(seen["n"], 3, "should give up after retrying, not on the first collision")
            self.assertIn('"ok": true', (pathlib.Path(d) / "engine_state.json").read_text())


if __name__ == "__main__":
    unittest.main()
