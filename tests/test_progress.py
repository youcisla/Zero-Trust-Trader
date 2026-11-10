"""The scan says where it is, and the pin comes from one place.

Two small contracts that a dashboard and a scheduler both depend on:

1. `run_funnel.progress()` writes a complete document or nothing — the UI polls
   it every second and a half-written file would render as a crash.
2. `run_funnel.load_env()` fills in what is missing and never overrides what the
   caller exported. An exported pin must win over the file, or a one-off run
   against another account silently becomes impossible.
"""

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import run_funnel


class ProgressFileTests(unittest.TestCase):
    def test_a_stage_is_written_whole(self):
        with TemporaryDirectory() as d:
            target = Path(d) / "scan_progress.json"
            with mock.patch.object(run_funnel, "PROGRESS", target):
                run_funnel.progress("tier2", symbols=120, strategies=["donchian"])
            doc = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(doc["stage"], "tier2")
        self.assertEqual(doc["status"], "running")
        self.assertEqual(doc["symbols"], 120)
        self.assertIn("updated_utc", doc)
        self.assertNotIn("scan_id", {k for k in doc if doc[k] is None})

    def test_no_temporary_file_is_left_behind(self):
        with TemporaryDirectory() as d:
            target = Path(d) / "scan_progress.json"
            with mock.patch.object(run_funnel, "PROGRESS", target):
                run_funnel.progress("tier3", tests=238)
            leftovers = sorted(p.name for p in Path(d).iterdir())
        self.assertEqual(leftovers, ["scan_progress.json"])

    def test_an_unwritable_path_never_fails_the_scan(self):
        with mock.patch.object(run_funnel, "PROGRESS", Path("Z:/nope/scan_progress.json")):
            run_funnel.progress("verdict", status="done")   # must not raise


class EnvLoadTests(unittest.TestCase):
    def test_the_file_fills_a_missing_key(self):
        with TemporaryDirectory() as d:
            env_file = Path(d) / ".env"
            env_file.write_text("MT5_EXPECTED_LOGIN=12345678\n# comment\n\n", encoding="utf-8")
            with mock.patch.object(run_funnel, "ENV_FILE", env_file), \
                 mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("MT5_EXPECTED_LOGIN", None)
                run_funnel.load_env()
                self.assertEqual(os.environ.get("MT5_EXPECTED_LOGIN"), "12345678")

    def test_an_exported_value_wins_over_the_file(self):
        with TemporaryDirectory() as d:
            env_file = Path(d) / ".env"
            env_file.write_text("MT5_EXPECTED_LOGIN=11111111\n", encoding="utf-8")
            with mock.patch.object(run_funnel, "ENV_FILE", env_file), \
                 mock.patch.dict(os.environ, {"MT5_EXPECTED_LOGIN": "99999999"}):
                run_funnel.load_env()
                self.assertEqual(os.environ.get("MT5_EXPECTED_LOGIN"), "99999999")

    def test_a_missing_file_is_not_an_error(self):
        with mock.patch.object(run_funnel, "ENV_FILE", Path("Z:/nope/.env")):
            run_funnel.load_env()          # must not raise


class TriggerCannotTradeTests(unittest.TestCase):
    """The dashboard's POST starts the research pipeline and nothing else."""

    def test_run_funnel_has_no_order_call(self):
        source = Path(run_funnel.__file__).read_text(encoding="utf-8")
        for forbidden in ("order_send", "positions_close", "order_close", "AutoTrading"):
            self.assertNotIn(forbidden, source,
                             f"{forbidden} must never appear in the scan path")


if __name__ == "__main__":
    unittest.main()
