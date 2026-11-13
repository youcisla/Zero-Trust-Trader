"""--t-gate must actually reach tier3_deep.py.

run() is mocked and every path is redirected into a temp dir, so this exercises
the real command-builder in main() with no MT5 call and no write to
funnel_history.log — that ledger is append-only and a test must not add a row.

The flag is the whole difference between a working funnel and a mute one: with
`actual > 0` enforced in permutation_test, a live-exit strategy cannot pass the
null while gating on the binomial z.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest import mock
from datetime import datetime, timezone


class _Proc:
    def __init__(self, rc: int = 0):
        self.returncode = rc
        self.stdout = ""
        self.stderr = ""


class _Awake:
    active = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _fake_run(calls: list, tmp: Path, writes: bool, count=150,
              rc: dict | None = None, stale: bool = False):
    """Stand in for subprocess.run: tier3 must leave its artifact behind."""
    rc = rc or {}

    def run(args, **kw):
        calls.append(list(args))
        stage = args[0] if args else ""
        scan_id = args[args.index("--scan-id") + 1] if "--scan-id" in args else ""
        if stage == "tier2_patterns.py":
            (tmp / "tier2_candidates.json").write_text(json.dumps(
                {"top": [], "total_tests": count, "scan_id": scan_id}), encoding="utf-8")
        if writes and stage == "tier3_deep.py":
            art = tmp / "tier3_results.json"
            art.write_text(
                json.dumps({"stood_down": True, "survivors": [], "results": [], "scan_id": scan_id}),
                encoding="utf-8")
            if stale:
                old = 1_700_000_000  # a previous scan's file, in the mtime sense
                os.utime(art, (old, old))
        return _Proc(rc.get(stage, 0))
    return run


class TestTGateReachesTier3(unittest.TestCase):
    def _main(self, extra_argv, writes: bool = True, count=150, ledger_text="", failed_stage=None,
              rc: dict | None = None, stale: bool = False):
        import run_funnel as rf

        calls: list[list[str]] = []
        audited: list[tuple] = []
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            fake_run = _fake_run(calls, tmp, writes, count, rc, stale)
            if failed_stage:
                normal_run = fake_run
                def fake_run(args, **kwargs):
                    if args[0] == failed_stage:
                        raise subprocess.TimeoutExpired(args[0], 1)
                    return normal_run(args, **kwargs)
            (tmp / "liquid_universe.json").write_text(
                json.dumps({"symbols": [{"symbol": "BTCUSD"}]}), encoding="utf-8")
            (tmp / "tier2_candidates.json").write_text(
                json.dumps({"top": [], "total_tests": 150}), encoding="utf-8")
            ledger = tmp / "funnel_history.log"
            if ledger_text:
                ledger.write_text(ledger_text, encoding="utf-8")
            clock = mock.Mock(wraps=datetime)
            clock.now.return_value = datetime(2026, 11, 9, 14, tzinfo=timezone.utc)
            with mock.patch.object(rf, "HERE", tmp), \
                 mock.patch.object(rf.kill_switch, "MARKER", tmp / "KILL_SWITCH"), \
                 mock.patch.object(rf, "_progress_path", lambda: tmp / "scan_progress.json"), \
                 mock.patch.object(rf, "LOG", ledger), \
                 mock.patch.object(rf, "LOCK", tmp / "funnel.lock"), \
                 mock.patch.object(rf, "run", fake_run), \
                 mock.patch.object(rf, "acquire_lock", lambda *a: True), \
                 mock.patch.object(rf, "release_lock", lambda *a: None), \
                 mock.patch.object(rf, "preflight", lambda: (True, [])), \
                 mock.patch.object(rf, "keep_awake", _Awake), \
                 mock.patch.object(rf.audit, "write", lambda *a, **k: audited.append(a)), \
                 mock.patch.object(rf, "datetime", clock), \
                 mock.patch.object(sys, "argv",
                                   ["run_funnel.py"] + list(extra_argv)):
                rc = rf.main()
            wrote_ledger = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
        return rc, calls, wrote_ledger, audited

    def test_flag_is_forwarded_to_the_deep_test(self):
        rc, calls, wrote_ledger, _ = self._main(["--t-gate"])
        deep = [c for c in calls if c and c[0] == "tier3_deep.py"]
        self.assertEqual(rc, 0)
        self.assertEqual(len(deep), 1, f"tier3 was run {len(deep)} times: {calls}")
        self.assertIn("--t-gate", deep[0])
        self.assertEqual(wrote_ledger, "", "the test wrote a funnel_history.log row")

    def test_flag_is_absent_when_not_requested(self):
        rc, calls, _, _ = self._main([])
        deep = [c for c in calls if c and c[0] == "tier3_deep.py"]
        self.assertEqual(rc, 0)
        self.assertEqual(len(deep), 1)
        self.assertNotIn("--t-gate", deep[0])


    def test_a_scan_that_wrote_nothing_is_a_failure_not_last_weeks_verdict(self):
        rc, calls, ledger, audited = self._main(["--t-gate"], writes=False)
        self.assertEqual(rc, 1, "a missing artifact must fail the run")
        self.assertEqual(audited, [], "no verdict may be logged from a stale artifact")
        # the failure itself belongs in the ledger — but as a FAILED row, never
        # as a scan row carrying a verdict this run did not produce
        self.assertIn("FAILED tier3 wrote no artifact", ledger)
        self.assertNotIn("universe=", ledger)

    def test_malformed_previous_count_refuses_before_tier2(self):
        rc, calls, ledger, _ = self._main([], ledger_text="total_tests=UNKNOWN | FAILED\n")
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [])
        self.assertIn("FAILED trial accounting before scan", ledger)

    def test_invalid_current_count_never_reaches_tier3(self):
        for count in (-1, "5", 1.5, True, None):
            with self.subTest(count=count):
                rc, calls, ledger, _ = self._main([], count=count)
                self.assertEqual(rc, 1)
                self.assertFalse(any(c[0] == "tier3_deep.py" for c in calls))
                self.assertIn("total_tests=UNKNOWN", ledger)

    def test_identity_and_cumulative_count_are_forwarded(self):
        rc, calls, _, _ = self._main([])
        self.assertEqual(rc, 0)
        first, second = calls
        self.assertEqual(first[first.index("--scan-id") + 1], second[second.index("--scan-id") + 1])
        self.assertEqual(int(second[second.index("--trials") + 1]), 2205 + 150)

    def test_tier2_timeout_requires_explicit_count_reconciliation(self):
        rc, calls, ledger, _ = self._main([], failed_stage="tier2_patterns.py")
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [])
        self.assertIn("total_tests=UNKNOWN", ledger)

    def test_tier2_failure_recovers_the_real_count_from_its_own_artifact(self):
        """A non-zero exit that still wrote its count must not become UNKNOWN.

        `trials_total` parses these rows; an UNKNOWN there under-pays the bar and
        makes every later scan look more significant than it is.
        """
        rc, calls, ledger, _ = self._main([], rc={"tier2_patterns.py": 1})
        self.assertEqual(rc, 1)
        self.assertIn("FAILED tier2", ledger)
        self.assertIn("total_tests=150", ledger)
        self.assertNotIn("total_tests=UNKNOWN", ledger)
        self.assertFalse(any(c[0] == "tier3_deep.py" for c in calls))

    def test_a_previous_scans_artifact_is_not_read_as_this_one(self):
        rc, _, ledger, audited = self._main(["--t-gate"], stale=True)
        self.assertEqual(rc, 1)
        self.assertIn("wrote no artifact", ledger)
        self.assertEqual(audited, [])

    def test_a_rebuild_records_a_tier1_failure_in_the_ledger(self):
        rc, _, ledger, _ = self._main(["--rebuild"], rc={"tier1_universe.py": 1})
        self.assertEqual(rc, 1)
        self.assertIn("FAILED tier1", ledger)

    def test_tier3_timeout_preserves_completed_trial_count(self):
        rc, calls, ledger, _ = self._main([], failed_stage="tier3_deep.py")
        self.assertEqual(rc, 1)
        self.assertIn("total_tests=150", ledger)

    def test_a_broken_ledger_chain_refuses_before_tier2(self):
        import trials_total as tt
        key = b"k" * 32
        body = ("2026-11-09T13:00:00Z | universe=1 tested=1 total_tests=100 "
                "scan_id=aaaa | STAND_DOWN")
        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "ledger.log"
            log.write_text(tt.sign_line(body, log, key) + "\n", encoding="utf-8")
            tampered = log.read_text(encoding="utf-8").replace(
                "total_tests=100", "total_tests=10")
        with mock.patch.object(tt, "ledger_key", return_value=key):
            rc, calls, ledger, _ = self._main([], ledger_text=tampered)
        self.assertEqual(rc, 1)
        self.assertFalse(any(c and c[0] == "tier2_patterns.py" for c in calls))
        self.assertIn("FAILED ledger chain", ledger)


if __name__ == "__main__":
    unittest.main()
