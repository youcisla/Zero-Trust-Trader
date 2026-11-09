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
import types
import unittest
from pathlib import Path
from unittest import mock


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


def _fake_run(calls: list, tmp: Path, writes: bool):
    """Stand in for subprocess.run: tier3 must leave its artifact behind."""
    def run(args, **kw):
        calls.append(list(args))
        if writes and args and args[0] == "tier3_deep.py":
            (tmp / "tier3_results.json").write_text(
                json.dumps({"stood_down": True, "survivors": [], "results": []}),
                encoding="utf-8")
        return _Proc()
    return run


class TestTGateReachesTier3(unittest.TestCase):
    def _main(self, extra_argv, writes: bool = True):
        import run_funnel as rf

        calls: list[list[str]] = []
        audited: list[tuple] = []
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            fake_run = _fake_run(calls, tmp, writes)
            (tmp / "liquid_universe.json").write_text(
                json.dumps({"symbols": [{"symbol": "BTCUSD"}]}), encoding="utf-8")
            (tmp / "tier2_candidates.json").write_text(
                json.dumps({"top": [], "total_tests": 150}), encoding="utf-8")
            ledger = tmp / "funnel_history.log"
            fake_trials = types.SimpleNamespace(total=lambda: [0])
            with mock.patch.object(rf, "HERE", tmp), \
                 mock.patch.object(rf, "LOG", ledger), \
                 mock.patch.object(rf, "LOCK", tmp / "funnel.lock"), \
                 mock.patch.object(rf, "run", fake_run), \
                 mock.patch.object(rf, "acquire_lock", lambda *a: True), \
                 mock.patch.object(rf, "release_lock", lambda *a: None), \
                 mock.patch.object(rf, "preflight", lambda: (True, [])), \
                 mock.patch.object(rf, "keep_awake", _Awake), \
                 mock.patch.object(rf.audit, "write", lambda *a, **k: audited.append(a)), \
                 mock.patch.dict(sys.modules, {"trials_total": fake_trials}), \
                 mock.patch.object(sys, "argv",
                                   ["run_funnel.py", "--at-utc", "14:00"] + list(extra_argv)):
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


if __name__ == "__main__":
    unittest.main()
