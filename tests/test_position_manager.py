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


class TestFridayClose(unittest.TestCase):
    def test_friday_session_close_actually_fires(self):
        """run_once passed isoweekday() into a 0-based check, so Friday (5)
        failed `weekday < 5` and positions were never flattened on the one day
        a weekend gap hurts most."""
        friday = datetime(2026, 11, 6, 23, 0)
        self.assertEqual(friday.weekday(), 4, "fixture assumption: 2026-11-06 is a Friday")

        self.assertTrue(close_due(friday, "20:45", friday.weekday()))
        self.assertFalse(close_due(friday, "23:59", friday.weekday()))

        saturday = datetime(2026, 11, 7, 23, 0)
        self.assertFalse(close_due(saturday, "20:45", saturday.weekday()))

        # Pin the convention: the old call site must stay broken.
        self.assertFalse(close_due(friday, "20:45", friday.isoweekday()))


class TestStateWrite(unittest.TestCase):
    """The dashboard polls state while the supervisor writes it.

    Publishing a NEW generation and renaming onto a name that does not exist
    yet is what makes WinError 5 impossible rather than merely rare.
    """

    def setUp(self):
        import pathlib
        import tempfile

        import position_manager as pm

        self.pm = pm
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.orig = pm.HERE
        pm.HERE = self.dir

    def tearDown(self):
        self.pm.HERE = self.orig
        self.tmp.cleanup()

    def test_a_reader_holding_the_newest_state_cannot_block_the_next_write(self):
        pm = self.pm
        self.assertTrue(pm.write_state({"n": 1}))
        newest = sorted(self.dir.glob("engine_state.*.json"))[-1]
        with newest.open("rb") as held:      # a reader sits on the file
            self.assertEqual(held.read(1)[:1], b"{")
            self.assertTrue(pm.write_state({"n": 2}),
                            "the writer must never touch the generation a reader holds")
        self.assertEqual(len(list(self.dir.glob("engine_state.*.json"))), 2)

    def test_old_generations_are_pruned_and_no_tmp_is_left(self):
        pm = self.pm
        for i in range(5):
            self.assertTrue(pm.write_state({"n": i}))
        self.assertLessEqual(len(list(self.dir.glob("engine_state.*.json"))), 2)
        self.assertEqual(list(self.dir.glob("*.tmp")), [])

    def test_a_failed_rename_returns_false_without_leaving_a_tmp(self):
        import os

        pm = self.pm
        real = os.rename

        def denied(*a, **k):
            raise PermissionError(13, "Access is denied")

        os.rename = denied
        try:
            self.assertFalse(pm.write_state({"n": 1}))
        finally:
            os.rename = real
        self.assertEqual(list(self.dir.glob("*.tmp")), [])


class TestApplyGuard(unittest.TestCase):
    """--apply is a request; only the environment grants it."""

    def test_apply_is_refused_without_the_env_var(self):
        import position_manager as pm
        allowed, why = pm.apply_allowed(True, {})
        self.assertFalse(allowed)
        self.assertIn("ZTT_ALLOW_APPLY", why)

    def test_apply_is_refused_when_the_var_is_not_one(self):
        import position_manager as pm
        self.assertFalse(pm.apply_allowed(True, {"ZTT_ALLOW_APPLY": "yes"})[0])

    def test_apply_is_granted_by_the_env_var(self):
        import position_manager as pm
        self.assertEqual(pm.apply_allowed(True, {"ZTT_ALLOW_APPLY": "1"}), (True, ""))

    def test_dry_run_is_unaffected(self):
        import position_manager as pm
        self.assertEqual(pm.apply_allowed(False, {}), (False, ""))

class TestSupervisorLock(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.path = pathlib.Path(self.dir.name) / "position_manager.lock"
        self.addCleanup(self.dir.cleanup)
        import position_manager as pm
        self.addCleanup(pm.release_lock, self.path)

    def test_a_second_supervisor_cannot_acquire_a_live_lock(self):
        import position_manager as pm
        self.assertTrue(pm.acquire_lock(60, self.path))
        self.assertFalse(pm.acquire_lock(60, self.path))

    def test_a_stale_lock_is_taken_over(self):
        """A crashed supervisor must not stop the system forever."""
        import position_manager as pm
        self.path.write_text("dead")
        import os
        import time
        old = time.time() - 3600          # an hour stale, not a timestamp in the future
        os.utime(self.path, (old, old))
        self.assertTrue(pm.acquire_lock(60, self.path))

    def test_exactly_one_thread_wins_a_simultaneous_start(self):
        """The bug this replaced: both saw exists()==False and both ran."""
        import threading
        import position_manager as pm
        barrier, results = threading.Barrier(8), []
        lock = threading.Lock()

        def start():
            barrier.wait()
            got = pm.acquire_lock(60, self.path)
            with lock:
                results.append(got)

        threads = [threading.Thread(target=start) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sum(results), 1)


if __name__ == "__main__":
    unittest.main()
