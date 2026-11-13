"""Fault paths use synthetic broker objects; no terminal is imported."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import position_manager as pm
import process_lock
import run_funnel as rf


class TestBrokerFailures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(pm, "HERE", pathlib.Path(self.tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(pm.kill_switch, "MARKER",
                                    pathlib.Path(self.tmp.name) / "KILL_SWITCH")
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(rf, "EXPECTED_LOGIN", 12345)
        patcher.start()
        self.addCleanup(patcher.stop)
        # The pin is read from the environment at call time (a `.env` is loaded
        # into os.environ at import), so patching the constant alone is not
        # enough: the synthetic account must be the pinned one here too.
        patcher = mock.patch.dict(os.environ, {"MT5_EXPECTED_LOGIN": "12345"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.account = types.SimpleNamespace(login=12345, server="Synthetic-Demo", trade_mode=0,
                                             balance=100, equity=100, currency="TEST", leverage=30)
        self.pos = types.SimpleNamespace(ticket=42, type=0, symbol="FIXTURE", volume=0.02,
                                         price_open=100.0, sl=90.0, price_current=105.0,
                                         profit=1.0, tp=0.0)
        self.sent = []
        self.remaining = (self.pos,)
        self.mt5 = types.SimpleNamespace(
            terminal_info=lambda: types.SimpleNamespace(connected=True),
            account_info=lambda: self.account,
            positions_get=lambda **kw: self.remaining,
            symbol_info_tick=lambda symbol: types.SimpleNamespace(bid=105.0, ask=105.1, time=time.time()),
            symbol_info=lambda symbol: types.SimpleNamespace(trade_tick_size=0.25, point=0.01,
                                                           trade_stops_level=0, trade_freeze_level=0),
            last_error=lambda: (5, "synthetic observation error"),
            ORDER_FILLING_IOC=1, ORDER_FILLING_FOK=0, TRADE_ACTION_DEAL=1,
            TRADE_ACTION_SLTP=6, ORDER_TYPE_SELL=1, ORDER_TYPE_BUY=0,
            TRADE_RETCODE_DONE=10009, TRADE_RETCODE_INVALID_FILL=10030,
        )
        import marketdata as md
        patcher = mock.patch.object(md, "broker_offset", return_value=0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_once(self, apply=False):
        with mock.patch.dict(sys.modules, {"MetaTrader5": self.mt5}), \
             mock.patch.object(pm, "ensure_mt5"), mock.patch.object(pm, "_atr", return_value=(1.0, 0.1)):
            return pm.run_once(pm.Cfg(), apply, None)

    def test_positions_error_is_unknown_not_healthy_flat(self):
        self.remaining = None
        result = self.run_once()
        self.assertFalse(result["ok"])
        self.assertNotIn("positions", result)

    def test_successful_empty_observation_is_healthy_flat(self):
        self.remaining = ()
        result = self.run_once()
        self.assertTrue(result["ok"])
        self.assertEqual(result["positions"], [])

    def test_account_disappearing_after_preflight_is_a_failure(self):
        self.mt5.account_info = mock.Mock(side_effect=[self.account, None])
        self.assertFalse(self.run_once()["ok"])

    def test_partial_close_has_no_same_pass_full_volume_retry(self):
        def send(request):
            self.sent.append(request)
            self.remaining = (types.SimpleNamespace(**{**vars(self.pos), "volume": 0.01}),)
            return types.SimpleNamespace(retcode=10010)
        self.mt5.order_send = send
        ok, detail = pm._close(self.mt5, self.pos, None)
        self.assertFalse(ok)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("residual", detail)

    def test_only_unsupported_fill_allows_fresh_fallback(self):
        def send(request):
            self.sent.append(request)
            if len(self.sent) == 1:
                return types.SimpleNamespace(retcode=10030)
            self.remaining = ()
            return types.SimpleNamespace(retcode=10009)
        self.mt5.order_send = send
        self.assertTrue(pm._close(self.mt5, self.pos, None)[0])
        self.assertEqual([r["type_filling"] for r in self.sent], [1, 0])

    def test_ambiguous_close_response_is_not_retried(self):
        self.mt5.order_send = lambda request: self.sent.append(request)
        self.assertFalse(pm._close(self.mt5, self.pos, None)[0])
        self.assertEqual(len(self.sent), 1)

    def test_claimed_done_with_unknown_remaining_is_failure(self):
        def send(request):
            self.remaining = None
            return types.SimpleNamespace(retcode=10009)
        self.mt5.order_send = send
        self.assertFalse(pm._close(self.mt5, self.pos, None)[0])

    def test_wire_stop_respects_symbol_tick_and_protective_direction(self):
        inf = self.mt5.symbol_info("FIXTURE")
        self.assertEqual(pm.wire_stop(1, 99, 100.11, 102, inf), 100.25)
        self.assertEqual(pm.wire_stop(-1, 103, 101.99, 100, inf), 101.75)
        self.assertIsNone(pm.wire_stop(1, 100.25, 100.11, 102, inf))
        self.assertIsNone(pm.wire_stop(1, 99, 100.11, 100.2, inf))
        inf.trade_freeze_level = 100
        self.assertIsNone(pm.wire_stop(1, 99, 100.11, 100.5, inf))

    def test_exact_submitted_stop_is_tick_aligned(self):
        self.mt5.order_send = lambda request: self.sent.append(request) or types.SimpleNamespace(retcode=10009)
        self.assertTrue(self.run_once(apply=True)["ok"])
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["sl"], 103.0)
        self.assertEqual(self.sent[0]["position"], self.pos.ticket)

    def test_nonfinite_risk_inputs_are_refused(self):
        for field in (float("nan"), float("inf")):
            self.assertIsNone(pm.next_stop(1, 100, 90, 105, field, 0.1, pm.Cfg())[0])


class TestApplyRefusals(unittest.TestCase):
    """The money path: every refusal between observing and sending.

    These branches are all that stands between a position that changed between
    the observation and the send, and a stop-modify against the wrong position.
    Deleting any one of them used to pass the whole suite.
    """

    setUp = TestBrokerFailures.setUp
    run_once = TestBrokerFailures.run_once

    def _why(self, result):
        return result.get("error") or ""

    def test_position_mutation_between_observation_and_send_refuses(self):
        calls = {"n": 0}

        def positions_get(**kw):
            calls["n"] += 1
            if calls["n"] == 1:          # the first, pre-check snapshot
                return (self.pos,)
            return (types.SimpleNamespace(**{**vars(self.pos), "type": 1}),)

        self.mt5.positions_get = positions_get
        self.mt5.order_send = lambda request: self.sent.append(request) or types.SimpleNamespace(retcode=10009)
        r = self.run_once(apply=True)
        self.assertFalse(r["ok"])
        self.assertIn("changed", self._why(r))
        self.assertEqual(self.sent, [])

    def test_unusable_quote_before_stop_update_refuses(self):
        self.mt5.symbol_info_tick = lambda symbol: types.SimpleNamespace(bid=0.0, ask=0.0, time=time.time())
        self.mt5.order_send = lambda request: self.sent.append(request) or types.SimpleNamespace(retcode=10009)
        r = self.run_once(apply=True)
        self.assertFalse(r["ok"])
        self.assertIn("unusable quote", self._why(r))
        self.assertEqual(self.sent, [])

    def test_negative_take_profit_observation_refuses(self):
        self.remaining = (types.SimpleNamespace(**{**vars(self.pos), "tp": -1.0}),)
        self.mt5.order_send = lambda request: self.sent.append(request) or types.SimpleNamespace(retcode=10009)
        r = self.run_once(apply=True)
        self.assertFalse(r["ok"])
        self.assertIn("take-profit", self._why(r))
        self.assertEqual(self.sent, [])

    def test_preflight_flip_between_observation_and_send_refuses(self):
        self.mt5.terminal_info = mock.Mock(side_effect=[
            types.SimpleNamespace(connected=True),
            types.SimpleNamespace(connected=False),
        ])
        self.mt5.order_send = lambda request: self.sent.append(request) or types.SimpleNamespace(retcode=10009)
        r = self.run_once(apply=True)
        self.assertFalse(r["ok"])
        self.assertIn("refused stop update", self._why(r))
        self.assertEqual(self.sent, [])

    def test_the_sltp_request_carries_the_full_mt5_contract(self):
        self.mt5.order_send = lambda request: self.sent.append(request) or types.SimpleNamespace(retcode=10009)
        self.assertTrue(self.run_once(apply=True)["ok"])
        self.assertEqual(len(self.sent), 1)
        # A dropped key is rejected by the real terminal and no test would notice.
        self.assertEqual(set(self.sent[0]), {"action", "symbol", "position", "sl", "tp"})


class TestProcessLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "test.lock"
        self.addCleanup(process_lock.release, self.path)

    def test_fresh_legacy_lease_is_honored(self):
        """An old-format lock file (plain pid) that is fresh still owns the lock.

        Old programs write no OS lock, so age is the only evidence - but a fresh
        one must be believed, or two supervisors overlap during the upgrade.
        """
        self.path.write_text("999", encoding="utf-8")
        self.assertFalse(process_lock.acquire(self.path, 3600))

    def test_old_timestamp_cannot_evict_an_active_owner(self):
        self.assertTrue(process_lock.acquire(self.path, 1))
        os.utime(self.path, (time.time() - 10000,) * 2)
        self.assertFalse(process_lock.acquire(self.path, 1))

    def test_only_direct_child_can_share_an_actually_held_parent_lock(self):
        code = ("import pathlib,sys,process_lock; "
                "print(process_lock.parent_holds(pathlib.Path(sys.argv[1])))")
        env = {**os.environ, "ZTT_FUNNEL_PARENT": str(os.getpid())}
        self.assertTrue(process_lock.acquire(self.path, 0))
        child = subprocess.run([sys.executable, "-c", code, str(self.path)], cwd=ROOT,
                               env=env, capture_output=True, text=True, check=True)
        self.assertEqual(child.stdout.strip(), "True")
        process_lock.release(self.path)
        child = subprocess.run([sys.executable, "-c", code, str(self.path)], cwd=ROOT,
                               env=env, capture_output=True, text=True, check=True)
        self.assertEqual(child.stdout.strip(), "False")


    def test_process_crash_releases_ownership_without_unlink_race(self):
        code = ("import pathlib,sys,process_lock; "
                "print(process_lock.acquire(pathlib.Path(sys.argv[1]),0),flush=True); "
                "sys.stdin.read(1)")
        child = subprocess.Popen([sys.executable, "-c", code, str(self.path)], cwd=ROOT,
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "True")
            self.assertFalse(process_lock.acquire(self.path, 0))
            child.terminate()
            child.wait(timeout=10)
            self.assertTrue(process_lock.acquire(self.path, 0))
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)
            child.stdin.close()
            child.stdout.close()


class TestStopMap(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = pathlib.Path(self.tmp.name) / "stops.json"

    def test_concurrent_updates_preserve_each_ticket(self):
        import concurrent.futures
        import stops
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda ticket: stops.record({"ticket": ticket, "sl": "1.25"}, self.path), range(1, 17)))
        self.assertEqual(set(stops.load(self.path)), {str(i) for i in range(1, 17)})

    def test_failed_publish_preserves_previous_map(self):
        import stops
        stops.record({"ticket": 1, "sl": "1.25"}, self.path)
        before = self.path.read_bytes()
        with mock.patch.object(stops.os, "replace", side_effect=PermissionError("synthetic deny")), \
             self.assertRaises(PermissionError):
            stops.record({"ticket": 2, "sl": "1.50"}, self.path)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".stops.*.tmp")), [])

    def test_invalid_stop_or_ticket_is_refused(self):
        import stops
        for position in ({"ticket": None, "sl": "1.2"}, {"ticket": 1, "sl": "NaN"},
                         {"ticket": 1, "sl": "Infinity"}, {"ticket": 1, "sl": "-1"}):
            with self.subTest(position=position), self.assertRaises(ValueError):
                stops.record(position, self.path)
        self.assertIsNone(stops.record({"ticket": 1, "sl": "0.00000"}, self.path))
        self.assertFalse(self.path.exists())


class TestMaintenance(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.object(rf.kill_switch, "MARKER",
                                    pathlib.Path(self.tmp.name) / "KILL_SWITCH")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_marker_refuses_funnel_and_supervisor_before_terminal_or_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            (root / ".ztt-maintenance").touch()
            with mock.patch.object(rf, "HERE", root), mock.patch.object(pm, "HERE", root), \
                 mock.patch.object(rf, "preflight") as preflight, \
                 mock.patch.object(rf, "acquire_lock") as acquire, \
                 mock.patch.object(sys, "argv", ["run_funnel.py"]):
                self.assertEqual(rf.main(), 2)
                self.assertEqual(pm.main(), 2)
                self.assertFalse(pm.run_once(pm.Cfg(), True, None)["ok"])
                preflight.assert_not_called()
                acquire.assert_not_called()

    def test_at_utc_cannot_change_clock_for_a_real_scan(self):
        with mock.patch.object(sys, "argv", ["run_funnel.py", "--at-utc", "14:00"]), \
             mock.patch.object(rf, "preflight") as preflight, \
             self.assertRaises(SystemExit) as ctx:
            rf.main()
        self.assertEqual(ctx.exception.code, 2)
        preflight.assert_not_called()



if __name__ == "__main__":
    unittest.main()
