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


def _fake_run(calls: list, tmp: Path, writes: bool, count=150):
    """Stand in for subprocess.run: tier3 must leave its artifact behind."""
    def run(args, **kw):
        calls.append(list(args))
        scan_id = args[args.index("--scan-id") + 1]
        if args[0] == "tier2_patterns.py":
            (tmp / "tier2_candidates.json").write_text(json.dumps(
                {"top": [], "total_tests": count, "scan_id": scan_id}), encoding="utf-8")
        if writes and args and args[0] == "tier3_deep.py":
            (tmp / "tier3_results.json").write_text(
                json.dumps({"stood_down": True, "survivors": [], "results": [], "scan_id": scan_id}),
                encoding="utf-8")
        return _Proc()
    return run


class TestTGateReachesTier3(unittest.TestCase):
    def _main(self, extra_argv, writes: bool = True, count=150, ledger_text="", failed_stage=None):
        import run_funnel as rf

        calls: list[list[str]] = []
        audited: list[tuple] = []
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            fake_run = _fake_run(calls, tmp, writes, count)
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

    def test_tier3_timeout_preserves_completed_trial_count(self):
        rc, calls, ledger, _ = self._main([], failed_stage="tier3_deep.py")
        self.assertEqual(rc, 1)
        self.assertIn("total_tests=150", ledger)


if __name__ == "__main__":
    unittest.main()
