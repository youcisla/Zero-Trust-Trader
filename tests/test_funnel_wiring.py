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


class TestTGateReachesTier3(unittest.TestCase):
    def _main(self, extra_argv):
        import run_funnel as rf

        calls: list[list[str]] = []
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            (tmp / "liquid_universe.json").write_text(
                json.dumps({"symbols": [{"symbol": "BTCUSD"}]}), encoding="utf-8")
            (tmp / "tier2_candidates.json").write_text(
                json.dumps({"top": [], "total_tests": 150}), encoding="utf-8")
            (tmp / "tier3_results.json").write_text(
                json.dumps({"stood_down": True, "survivors": [], "results": []}),
                encoding="utf-8")
            ledger = tmp / "funnel_history.log"
            fake_trials = types.SimpleNamespace(total=lambda: [0])
            with mock.patch.object(rf, "HERE", tmp), \
                 mock.patch.object(rf, "LOG", ledger), \
                 mock.patch.object(rf, "LOCK", tmp / "funnel.lock"), \
                 mock.patch.object(rf, "run",
                                   lambda args, **kw: (calls.append(list(args)), _Proc())[1]), \
                 mock.patch.object(rf, "acquire_lock", lambda *a: True), \
                 mock.patch.object(rf, "release_lock", lambda *a: None), \
                 mock.patch.object(rf, "preflight", lambda: (True, [])), \
                 mock.patch.object(rf, "keep_awake", _Awake), \
                 mock.patch.object(rf.audit, "write", lambda *a, **k: None), \
                 mock.patch.dict(sys.modules, {"trials_total": fake_trials}), \
                 mock.patch.object(sys, "argv",
                                   ["run_funnel.py", "--at-utc", "14:00"] + list(extra_argv)):
                rc = rf.main()
            wrote_ledger = ledger.exists()
        return rc, calls, wrote_ledger

    def test_flag_is_forwarded_to_the_deep_test(self):
        rc, calls, wrote_ledger = self._main(["--t-gate"])
        deep = [c for c in calls if c and c[0] == "tier3_deep.py"]
        self.assertEqual(rc, 0)
        self.assertEqual(len(deep), 1, f"tier3 was run {len(deep)} times: {calls}")
        self.assertIn("--t-gate", deep[0])
        self.assertFalse(wrote_ledger, "the test wrote a funnel_history.log row")

    def test_flag_is_absent_when_not_requested(self):
        rc, calls, _ = self._main([])
        deep = [c for c in calls if c and c[0] == "tier3_deep.py"]
        self.assertEqual(rc, 0)
        self.assertEqual(len(deep), 1)
        self.assertNotIn("--t-gate", deep[0])


if __name__ == "__main__":
    unittest.main()
