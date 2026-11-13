"""Synthetic regressions for the statistical/data audit; never connects to MT5."""

import json
import math
import pathlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from _mt5stub import install

install()
import marketdata as md
import process_lock
import tier2_patterns as t2
import tier3_deep as t3
import timemachine_mt5 as tm


def bars(closes=(100.0, 100.0, 100.0, 200.0)):
    c = np.asarray(closes)
    return {"c": c, "o": c.copy(), "h": c + 0.1, "l": c - 0.1,
            "t": np.arange(len(c)) * 900.0}


class TestExitContract(unittest.TestCase):
    def test_horizon_settles_on_last_processed_bar(self):
        b = bars()
        self.assertEqual(tm._resolve(b, 0, "long", 100, 90, 300, 10, 2)[2], 2)
        result = tm._resolve_live(b, 0, "long", 100, 90, 10, np.zeros(4), 0, 2)
        self.assertEqual((result[2], result[3]), (2, 100.0))

    def test_short_stop_is_already_an_ask_fill(self):
        b = bars((100.0, 100.7, 100.0))
        b["h"][1] = 100.8
        result = tm._resolve_live(b, 0, "short", 100, 101, 1, np.zeros(3), 0.2, 2)
        self.assertEqual(result[0], "stop")
        self.assertAlmostEqual(result[3], 101.0)

    def test_short_supervisor_receives_ask_and_spread(self):
        b = bars((100.0, 99.0, 99.0))
        with mock.patch.object(tm, "next_stop", return_value=(None, "fixture")) as trail:
            tm._resolve_live(b, 0, "short", 100, 105, 5, np.ones(3), 0.2, 1)
        self.assertAlmostEqual(trail.call_args.args[3], 99.2)
        self.assertAlmostEqual(trail.call_args.args[5], 0.2)

    def test_session_close_uses_utc_completed_bar_time(self):
        b = bars((100.0, 100.0, 100.0, 100.0))
        start = datetime(2026, 11, 9, 18, 15, tzinfo=timezone.utc).timestamp()
        b.update(t=np.arange(4) * 900 + start, offset=7200, tf="M15")
        result = tm._resolve_live(b, 0, "long", 100, 90, 10, np.zeros(4), 0, 3,
                                  close_utc="17:00")
        self.assertEqual((result[0], result[2]), ("session", 2))

    def test_nonpositive_horizon_is_refused(self):
        for horizon in (0, -1):
            with self.assertRaises(ValueError):
                tm._resolve(bars(), 0, "long", 100, 90, 110, 10, horizon)

    def test_entry_eligibility_uses_the_same_completed_utc_clock(self):
        b = bars()
        start = datetime(2026, 11, 9, 14, 30, tzinfo=timezone.utc).timestamp()
        b.update(t=np.arange(4) * 900 + start, offset=7200, tf="M15",
                 entry_window_utc=(13 * 60, 17 * 60))
        self.assertFalse(tm._entry_eligible(b, 0))  # completes 12:45 UTC
        self.assertTrue(tm._entry_eligible(b, 1))   # completes 13:00 UTC


class TestProfitabilityRefusal(unittest.TestCase):
    def test_negative_mean_is_refused_in_both_modes(self):
        rs = [0.01] * 138 + [-1.0] * 112
        for use_t in (False, True):
            doc = tm.significance(138 / 250, 250, 1.5, rs, 2205, use_t)
            self.assertFalse(doc["passes_corrected"])
            self.assertFalse(doc["passes_naive"])

    def test_nonfinite_mean_is_refused(self):
        for bad in (float("nan"), float("inf"), -float("inf")):
            doc = tm.significance(0.9, 30, 1.5, [1.0] * 29 + [bad], 1)
            self.assertFalse(doc["passes_corrected"])
            json.dumps(doc, allow_nan=False)

    def test_losing_observation_never_passes_a_losing_null(self):
        b = bars(tuple(np.linspace(100, 101, 100)))
        trades = [{"side": "long", "R": 0.01}] * 28 + [{"side": "long", "R": -1.0}] * 22
        with mock.patch.object(tm, "_resolve_live", return_value=("stop", 1, 30, 0.0)):
            result = tm.permutation_test(b, trades, 1.5, 1.5, 2, permutations=1000)
        self.assertIsNotNone(result)
        self.assertFalse(result["passes"])


class TestUnverifiableCorrelation(unittest.TestCase):
    def test_tier2_quarantines_missing_and_nonfinite_correlations(self):
        hits = [{"symbol": s, "strategy": "donchian", "score": 1} for s in ("AAA", "BBB")]
        for rho in (None, math.nan):
            with mock.patch.object(md, "fetch", return_value=bars()), \
                 mock.patch.object(md, "corr_returns", return_value=rho):
                kept, dropped = t2.dedupe(hits, 0.7, "H1")
            self.assertEqual([r["symbol"] for r in kept], ["AAA"])
            self.assertEqual([r["symbol"] for r in dropped], ["BBB"])

    def test_tier3_quarantines_missing_and_nonfinite_correlations(self):
        hits = [{"symbol": s, "significance": {"z": 5}} for s in ("AAA", "BBB")]
        for rho in (None, math.nan):
            with mock.patch.object(t3, "correlation", return_value=rho):
                kept, dropped = t3.dedupe(hits, 0.7)
            self.assertEqual([r["symbol"] for r in kept], ["AAA"])
            self.assertIsNone(dropped[0]["correlation"])

    def test_failed_cleanup_remains_pending(self):
        with mock.patch.object(md, "_AUTO_SELECTED", {"FIXTURE"}), \
             mock.patch.object(md.mt5, "symbol_select", return_value=False):
            self.assertEqual(md.release_auto_selected(), 0)
            self.assertEqual(md._AUTO_SELECTED, {"FIXTURE"})


class TestQuotesAndBars(unittest.TestCase):
    def test_tick_spread_rejects_missing_crossed_stale_or_nonfinite_quotes(self):
        good = SimpleNamespace(bid=100.0, ask=100.2, time=10000)
        with mock.patch.object(md.time, "time", return_value=10000):
            self.assertAlmostEqual(md.tick_spread(good), 0.2)
            for tick in (None, SimpleNamespace(bid=101, ask=100, time=10000),
                         SimpleNamespace(bid=100, ask=100, time=10000),
                         SimpleNamespace(bid=100, ask=math.nan, time=10000),
                         SimpleNamespace(bid=100, ask=101, time=9000)):
                self.assertIsNone(md.tick_spread(tick))

    def test_fetch_rejects_invalid_ohlc_and_duplicate_times(self):
        good = [{"time": 100, "open": 100, "high": 102, "low": 99, "close": 101},
                {"time": 1000, "open": 101, "high": 103, "low": 100, "close": 102}]
        for field, value in (("close", math.nan), ("low", 104), ("open", -1), ("time", 100)):
            rates = [dict(r) for r in good]
            rates[1][field] = value
            with mock.patch.object(md.mt5, "symbol_info", return_value=SimpleNamespace(visible=True)), \
                 mock.patch.object(md.mt5, "symbol_select", return_value=True), \
                 mock.patch.object(md.mt5, "copy_rates_from_pos", return_value=rates), \
                 mock.patch.object(md.time, "time", return_value=10000):
                self.assertIsNone(md.fetch("FIXTURE", "M15", 2, 0))


class TestTier3Accounting(unittest.TestCase):
    def _main(self, source, argv, minimum=8690):
        import trials_total
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            path = root / "tier2.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with mock.patch.object(sys, "argv", ["tier3_deep.py", "--tier2", str(path)] + argv), \
                 mock.patch.object(t3, "OUT", root / "tier3.json"), \
                 mock.patch.object(t3, "HERE", root), \
                 mock.patch.object(t3, "log_run") as log, \
                 mock.patch.object(trials_total, "required_trials", return_value=minimum):
                rc = t3.main()
                payload = json.loads((root / "tier3.json").read_text()) if (root / "tier3.json").exists() else None
            return rc, payload, log

    def test_empty_scan_writes_coherent_artifact_with_authoritative_total(self):
        rc, payload, log = self._main({"top": [], "total_tests": 90, "scan_id": "scan-a"}, ["--scan-id", "scan-a"])
        self.assertEqual(rc, 0)
        self.assertEqual(payload["scan_id"], "scan-a")
        self.assertEqual(payload["trials_cumulative"], 8690)
        self.assertTrue(payload["stood_down"])
        log.assert_called_once()

    def test_understated_trial_argument_is_refused(self):
        rc, payload, log = self._main({"top": [], "total_tests": 90}, ["--trials", "90"])
        self.assertNotEqual(rc, 0)
        self.assertIsNone(payload)
        log.assert_not_called()

    def test_stage_identity_mismatch_is_refused(self):
        rc, payload, log = self._main({"top": [], "total_tests": 90, "scan_id": "old"}, ["--scan-id", "new"])
        self.assertNotEqual(rc, 0)
        self.assertIsNone(payload)
        log.assert_not_called()

    def test_manual_rerun_gets_a_new_identity(self):
        rc, payload, _log = self._main({"top": [], "total_tests": 90, "scan_id": "old"}, [])
        self.assertEqual(rc, 0)
        self.assertNotEqual(payload["scan_id"], "old")

    def test_broken_ledger_chain_refuses_the_deep_test(self):
        import trials_total as tt
        key = b"k" * 32
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            log = root / "funnel_history.log"
            body = ("2026-11-09T13:00:00Z | universe=1 tested=1 total_tests=100 "
                    "scan_id=old | STAND_DOWN")
            log.write_text(tt.sign_line(body, log, key) + "\n", encoding="utf-8")
            log.write_text(log.read_text(encoding="utf-8").replace(
                "total_tests=100", "total_tests=10"), encoding="utf-8")
            src = root / "tier2.json"
            src.write_text(json.dumps({"top": [], "total_tests": 90, "scan_id": "scan-a"}),
                           encoding="utf-8")
            with mock.patch.object(tt, "LOG", log), \
                 mock.patch.object(tt, "ledger_key", return_value=key), \
                 mock.patch.object(t3, "OUT", root / "tier3.json"), \
                 mock.patch.object(t3, "HERE", root), \
                 mock.patch.object(sys, "argv",
                                   ["tier3_deep.py", "--tier2", str(src), "--scan-id", "scan-a"]):
                rc = t3.main()
            self.assertEqual(rc, 1)
            self.assertFalse((root / "tier3.json").exists())
            self.assertEqual(log.read_text(encoding="utf-8").count("\n"), 1)


class TestResearchLock(unittest.TestCase):
    def test_explicit_scan_id_cannot_bypass_another_research_owner(self):
        for module in (t2, t3):
            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                lock = root / "funnel.lock"
                self.assertTrue(process_lock.acquire(lock, 0))
                try:
                    with mock.patch.object(module, "HERE", root), \
                         mock.patch.object(module, "_main") as work, \
                         mock.patch.object(sys, "argv", [module.__name__, "--scan-id", "fixture"]):
                        self.assertNotEqual(module.main(), 0)
                        work.assert_not_called()
                finally:
                    process_lock.release(lock)

    def test_maintenance_refuses_before_research(self):
        for module in (t2, t3):
            with tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                (root / ".ztt-maintenance").touch()
                with mock.patch.object(module, "HERE", root), \
                     mock.patch.object(module, "_main") as work:
                    self.assertNotEqual(module.main(), 0)
                    work.assert_not_called()


if __name__ == "__main__":
    unittest.main()
