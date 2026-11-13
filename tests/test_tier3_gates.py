"""Tier 3 admits a candidate only when BOTH gates pass.

Gate 5 (corrected z) and gate 6 (permutation null) are not redundant, and the
survivor rule is the line that says so. Deleting its permutation half would let
a candidate that is indistinguishable from random entry reach `survivors`, so
one candidate here clears the z gate and fails the null; the other clears both.

`permutation_test` gets a positive control on synthetic bars: a real edge with a
fixed seed must pass, or the null's own arithmetic has drifted.
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile
import unittest
import unittest.mock

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]

import _mt5stub  # noqa: E402

_mt5stub.install()

import numpy as np  # noqa: E402

import tier3_deep  # noqa: E402
import timemachine_mt5 as tm  # noqa: E402


def synthetic_bars(n: int = 300, seed: int = 5) -> dict:
    """An oscillating, trendless path: random long entries win about half the time."""
    rng = np.random.default_rng(seed)
    close = 1.0 + 0.02 * np.sin(np.arange(n) / 4.0) + rng.normal(0, 0.002, n)
    high = close + 0.004
    low = close - 0.004
    return {
        "c": close,
        "h": high,
        "l": low,
        "t": np.arange(n, dtype=np.int64) * 900 + 1_700_000_000,
    }


class ThePermutationPositiveControl(unittest.TestCase):
    def test_a_real_edge_passes_the_null(self):
        bars = synthetic_bars()
        # 100 long trades booked at +1.5R: an edge no random replay reproduces.
        trades = [{"side": "long", "R": 1.5} for _ in range(100)]
        out = tm.permutation_test(
            bars,
            trades,
            k_atr=1.5,
            rr=1.5,
            horizon=8,
            permutations=100,
            seed=19,
            spread=0.0,
        )
        self.assertIsNotNone(out)
        self.assertGreater(out["actual_z"], 5.0)
        self.assertTrue(out["passes"], out)
        # the add-one form is why p is never zero
        self.assertAlmostEqual(out["p_value"], 1 / 101, places=4)


class OnlyABothGatesPassBecomesASurvivor(unittest.TestCase):
    @staticmethod
    def doc(symbol: str, z: float, z_ok: bool, perm_ok: bool) -> dict:
        return {
            "symbol": symbol,
            "strategy": "donchian",
            "history": {
                "occurrences": 80,
                "n_decided": 60,
                "win_rate": 0.4,
                "avg_R": 0.2,
                "max_drawdown_R": -3.0,
            },
            "significance": {"passes_naive": z_ok, "passes_corrected": z_ok, "z": z},
            "permutation": {"passes": perm_ok, "p_value": 0.01 if perm_ok else 0.30},
        }

    def test_a_z_pass_with_a_failed_null_is_not_kept(self):
        # BBB clears the corrected z with a HIGHER statistic than AAA, so only
        # the permutation half of the survivor rule can exclude it.
        docs = [
            self.doc("AAA", 5.0, z_ok=True, perm_ok=True),
            self.doc("BBB", 6.0, z_ok=True, perm_ok=False),
        ]
        fake_report = unittest.mock.MagicMock()
        fake_report.write_report.return_value = "report"
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tier2 = pathlib.Path(tmp.name) / "tier2_candidates.json"
        tier2.write_text(
            json.dumps(
                {
                    "scan_id": "t3-test",
                    "total_tests": 2,
                    "symbols_tested": 1,
                    "strategies_tested": 2,
                    "symbols_in": 1,
                    "top": [
                        {"symbol": "AAA", "strategy": "donchian"},
                        {"symbol": "BBB", "strategy": "donchian"},
                    ],
                }
            )
        )
        out = pathlib.Path(tmp.name) / "tier3_results.json"
        with (
            unittest.mock.patch.object(
                sys, "argv", ["tier3_deep.py", "--tier2", str(tier2)]
            ),
            unittest.mock.patch.object(tier3_deep, "OUT", out),
            unittest.mock.patch.object(tier3_deep, "log_run") as logged,
            unittest.mock.patch.object(tier3_deep.tm, "analyse", side_effect=docs),
            unittest.mock.patch.object(
                tier3_deep.tm,
                "source_provenance",
                return_value={"code_sha256": "x", "git_revision": "y"},
            ),
            unittest.mock.patch.object(tier3_deep.md, "connect"),
            unittest.mock.patch.object(
                tier3_deep.md, "release_auto_selected", return_value=0
            ),
            unittest.mock.patch("trials_total.verify", return_value=("ok", "fixture")),
            unittest.mock.patch("trials_total.required_trials", return_value=100),
            unittest.mock.patch.dict(sys.modules, {"report": fake_report}),
        ):
            rc = tier3_deep._main()
        self.assertEqual(rc, 0)
        payload = json.loads(out.read_text())
        self.assertEqual(payload["passed_both_gates"], ["AAA"])
        self.assertEqual(payload["survivors"], ["AAA"])
        self.assertEqual(payload["stood_down"], False)
        self.assertEqual(len(logged.call_args_list), 1)
        self.assertEqual(logged.call_args.args[1]["survivors"], ["AAA"])


if __name__ == "__main__":
    unittest.main()
