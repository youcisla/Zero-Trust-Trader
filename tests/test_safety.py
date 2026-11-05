"""Safety-critical behaviour: the demo-only guard, the run window, the lock.

These are the parts where a bug does not produce a wrong number, it produces an
order. They must be tested without a terminal attached.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

# The expected login is read from the environment at import time, so set it
# before importing run_funnel.
os.environ["MT5_EXPECTED_LOGIN"] = "12345"

import run_funnel as rf  # noqa: E402


class T:  # terminal_info stand-in
    def __init__(self, connected=True):
        self.connected = connected


class A:  # account_info stand-in
    def __init__(self, login=12345, server="PepperstoneUK-Demo"):
        self.login, self.server = login, server


class TestPreflight(unittest.TestCase):
    def test_healthy_demo_passes(self):
        self.assertEqual(rf.preflight_check(T(), A()), [])

    def test_live_account_is_blocked(self):
        """The single most important assertion in this repository: a live
        account must abort the run before a single symbol is scanned."""
        problems = rf.preflight_check(T(), A(server="PepperstoneUK-Live"))
        self.assertTrue(any("DEMO" in p for p in problems), problems)

    def test_disconnected_terminal_is_blocked(self):
        self.assertTrue(any("NOT connected" in p
                            for p in rf.preflight_check(T(connected=False), A())))

    def test_wrong_login_is_blocked(self):
        self.assertTrue(any("expected 12345" in p
                            for p in rf.preflight_check(T(), A(login=999))))

    def test_missing_terminal_and_account(self):
        self.assertEqual(len(rf.preflight_check(None, None)), 2)

    def test_case_insensitive_demo_match(self):
        self.assertEqual(rf.preflight_check(T(), A(server="some-demo-server")), [])


class TestWindow(unittest.TestCase):
    def _t(self, h, m):
        return datetime(2026, 11, 5, h, m, tzinfo=timezone.utc)

    def test_overlap_boundaries(self):
        for h, m, want in [(12, 59, False), (13, 0, True), (16, 59, True),
                           (17, 0, False), (9, 30, False), (0, 0, False)]:
            with self.subTest(t=f"{h:02d}:{m:02d}"):
                self.assertIs(rf.in_window("13:00-17:00", self._t(h, m)), want)

    def test_window_wrapping_midnight(self):
        self.assertTrue(rf.in_window("22:00-02:00", self._t(23, 0)))
        self.assertTrue(rf.in_window("22:00-02:00", self._t(1, 0)))
        self.assertFalse(rf.in_window("22:00-02:00", self._t(12, 0)))


class TestLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self._orig = rf.LOCK
        rf.LOCK = pathlib.Path(self.tmp.name) / "funnel.lock"

    def tearDown(self):
        rf.release_lock()
        rf.LOCK = self._orig
        self.tmp.cleanup()

    def test_acquire_then_refuse_overlap(self):
        self.assertTrue(rf.acquire_lock(120))
        self.assertTrue(rf.LOCK.exists())
        self.assertFalse(rf.acquire_lock(120), "a second run must be refused")

    def test_release_frees_it(self):
        self.assertTrue(rf.acquire_lock(120))
        rf.release_lock()
        self.assertFalse(rf.LOCK.exists())
        self.assertTrue(rf.acquire_lock(120), "must be usable after release")

    def test_stale_lock_is_taken_over(self):
        """A crashed run must not block the system forever."""
        rf.LOCK.write_text("999")
        old = time.time() - 200 * 60
        os.utime(rf.LOCK, (old, old))
        self.assertTrue(rf.acquire_lock(120))

    def test_lock_contains_pid(self):
        rf.acquire_lock(120)
        self.assertIn(str(os.getpid()), rf.LOCK.read_text())


if __name__ == "__main__":
    unittest.main()
