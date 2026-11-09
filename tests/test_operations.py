"""Privacy, release completeness and redacted diagnostics use synthetic inputs."""

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import build_public, check_public
import status


class PrivacyTests(unittest.TestCase):
    def test_private_artifact_names_and_allowlisted_public_inputs(self):
        for name in ["engine_state.42.json", "engine_state.42.json.tmp", "idempotency.db-wal",
                     ".env.production", "run_funnel.local.cmd", "exports/range.csv",
                     "dashboard/target/release/binary.exe", ".ztt-maintenance"]:
            with self.subTest(name=name):
                self.assertTrue(check_public.private_path(name))
        for name in ["dashboard/Cargo.lock", ".env.example", "tools/check_public.py"]:
            self.assertFalse(check_public.private_path(name))

    def test_content_scan_includes_workflow_and_never_prints_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = ".github/workflows/ci.yml"
            path = root / name
            path.parent.mkdir(parents=True)
            secret = "ghp_" + "x" * 30
            path.write_text(secret + "\n" + "12345678", encoding="utf-8")
            failures = check_public.scan_files(root, [name], "12345678")
            self.assertIn((name, "credential-token"), failures)
            self.assertIn((name, "private-identifier"), failures)
            output = io.StringIO()
            with mock.patch.object(check_public, "tracked_files", return_value=[name]), \
                 mock.patch.dict(os.environ, {"ZTT_PRIVATE_IDENTIFIER": "12345678"}), \
                 contextlib.redirect_stderr(output):
                code = check_public.main(["--root", str(root)])
            self.assertEqual(code, 1)
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn("12345678", output.getvalue())

    def test_missing_source_is_scan_error_not_clean(self):
        with mock.patch.object(check_public, "tracked_files", side_effect=OSError):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(check_public.main([]), 2)

    def test_portable_gitignore_without_local_exclusions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True)
            (root / ".gitignore").write_bytes((ROOT / ".gitignore").read_bytes())
            names = ["engine_state.42.json", "engine_state.42.json.tmp", "idempotency.db-shm",
                     ".env.production", "run_rebuild.local.cmd", ".ztt-maintenance", "exports/trades.csv"]
            result = subprocess.run(["git", "check-ignore", "--stdin"], cwd=root,
                                    input=("\n".join(names) + "\n").encode(), capture_output=True, check=True)
            self.assertEqual(set(result.stdout.decode().splitlines()), set(names))
            for name in [".env.example", "dashboard/Cargo.lock"]:
                self.assertEqual(subprocess.run(["git", "check-ignore", name], cwd=root,
                                                capture_output=True).returncode, 1)


class ReleaseTests(unittest.TestCase):
    def test_copy_requires_complete_tracked_sources_and_preserves_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            for name in build_public.REQUIRED:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# fixture\n" if name.endswith(".py") else "fixture\n", encoding="utf-8")
            destination = Path(directory) / "artifact"
            names = sorted(build_public.REQUIRED)
            with mock.patch.object(build_public, "tracked_files", return_value=names), \
                 mock.patch.object(build_public.subprocess, "run", return_value=SimpleNamespace(stdout="fixture-revision\n")):
                manifest = build_public.build(root, destination)
                self.assertEqual(set(manifest["files_sha256"]), set(names))
                with self.assertRaisesRegex(ValueError, "destination must be empty"):
                    build_public.build(root, destination)
            self.assertTrue((destination / "PUBLIC-MANIFEST.json").exists())
            with mock.patch.object(build_public, "tracked_files", return_value=names[1:]):
                with self.assertRaisesRegex(ValueError, "required tracked sources missing"):
                    build_public.build(root, Path(directory) / "incomplete")

    def test_untracked_local_import_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            for name in build_public.REQUIRED:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("# fixture\n" if name.endswith(".py") else "fixture\n")
            (root / "private_helper.py").write_text("# must not be required by release\n")
            (root / "run_funnel.py").write_text("import private_helper\n")
            with mock.patch.object(build_public, "tracked_files", return_value=sorted(build_public.REQUIRED)):
                with self.assertRaisesRegex(ValueError, "local import missing"):
                    build_public.build(root, Path(directory) / "artifact")


class StatusTests(unittest.TestCase):
    def test_policy_and_account_pin_are_redacted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.toml").write_text(
                'account=12345678\napi_key="dummy-secret"\n[policy]\n'
                'auto_approve_notional="0.001"\nmax_notional_per_trade=0.02\nmax_orders_per_minute=1\n'
                'future_sensitive_field="do-not-print"\n', encoding="utf-8")
            output = io.StringIO()
            with mock.patch.object(status, "HERE", root), \
                 mock.patch.dict(os.environ, {"MT5_EXPECTED_LOGIN": "12345678"}), \
                 contextlib.redirect_stdout(output):
                status.config_block()
            text = output.getvalue()
            for secret in ["12345678", "dummy-secret", "do-not-print", str(root)]:
                self.assertNotIn(secret, text)
            self.assertIn("max_orders_per_minute=1", text)

    def test_unknown_positions_are_not_flat_and_shutdown_always_runs(self):
        terminal = SimpleNamespace(trade_allowed=False)
        account = SimpleNamespace(login=12345678, server="private-demo-server", balance=100, equity=100, currency="EUR")
        api = SimpleNamespace(initialize=lambda: True, terminal_info=lambda: terminal,
                              account_info=lambda: account, positions_get=lambda: None,
                              orders_get=lambda: None, symbols_get=lambda: (), shutdown=mock.Mock())
        output = io.StringIO()
        with mock.patch.dict(sys.modules, {"MetaTrader5": api}), contextlib.redirect_stdout(output):
            status.terminal_block()
        self.assertIn("UNKNOWN (retrieval failed)", output.getvalue())
        self.assertNotIn("12345678", output.getvalue())
        self.assertNotIn("private-demo-server", output.getvalue())
        api.shutdown.assert_called_once()


class VendorTests(unittest.TestCase):
    def test_vendored_scripts_match_reviewed_manifest_and_licenses_exist(self):
        vendor = ROOT / "dashboard/web/vendor"
        metadata = json.loads((vendor / "PROVENANCE.json").read_text())
        for asset in metadata["assets"]:
            self.assertEqual(hashlib.sha256((vendor / asset["file"]).read_bytes()).hexdigest(), asset["sha256"])
            text = (vendor / asset["license"]).read_text()
            self.assertIn("Apache License" if asset["package"] == "htm" else "MIT", text)



class MaintenanceVisibilityTests(unittest.TestCase):
    """A fail-closed sentinel must not stop the system silently.

    `.ztt-maintenance` was left behind after a remediation. The only symptom was
    HermesMT5Funnel reporting Last Result=2 while still showing "Ready" - the
    operator had nothing to read. status.py now reports it with its age and the
    note that was left in it.
    """

    def _render(self, root):
        out = io.StringIO()
        with mock.patch.object(status, "HERE", root), contextlib.redirect_stdout(out):
            status.maintenance_block()
        return out.getvalue()

    def test_an_active_sentinel_names_its_age_and_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".ztt-maintenance").write_text("audit remediation\n", encoding="utf-8")
            text = self._render(root)
        self.assertIn("ACTIVE", text)
        self.assertIn("audit remediation", text)
        self.assertIn("clear with", text)

    def test_absent_sentinel_says_none_instead_of_staying_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            text = self._render(Path(directory))
        self.assertIn("maintenance   : none", text)
        self.assertNotIn("ACTIVE", text)

if __name__ == "__main__":
    unittest.main()
