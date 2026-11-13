"""The publication build applies the identifier rule the standalone check does.

On the machine that commits, the only place the protected login exists is the
gitignored `.env`. `check_public` learned to read it; `build_public` used to
scan with an empty identifier and shipped a fixture that carried it. The build
is the last chance to refuse, so it must read the same source.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import build_public, check_public

LOGIN = "20481736"


class IdentifierFallback(unittest.TestCase):
    def test_the_env_file_supplies_the_identifier_when_the_variable_is_unset(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / ".env").write_text(f"MT5_EXPECTED_LOGIN={LOGIN}\n", encoding="utf-8")
            self.assertEqual(check_public.private_identifier(root), LOGIN)

    def test_no_env_file_and_no_variable_means_no_identifier(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(check_public.private_identifier(Path(d)), "")

    def test_build_refuses_a_fixture_carrying_the_login_from_the_env_file(self):
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.dict(os.environ, {"ZTT_PRIVATE_IDENTIFIER": ""}):
            root = Path(d) / "tree"
            root.mkdir()
            (root / ".env").write_text(f"MT5_EXPECTED_LOGIN={LOGIN}\n", encoding="utf-8")
            (root / "fixture.py").write_text(f"LOGIN = {LOGIN}\n", encoding="utf-8")
            out = Path(d) / "out"
            out.mkdir()
            with mock.patch.object(build_public, "tracked_files",
                                   return_value=["fixture.py"]), \
                 mock.patch.object(build_public, "REQUIRED", set()):
                with self.assertRaises(ValueError) as ctx:
                    build_public.build(root, out)
        self.assertIn("fixture.py", str(ctx.exception))
        self.assertIn("private-identifier", str(ctx.exception))

    def test_a_clean_tree_still_builds(self):
        done = mock.Mock(stdout="deadbeef\n", returncode=0)
        with tempfile.TemporaryDirectory() as d, \
             mock.patch.dict(os.environ, {"ZTT_PRIVATE_IDENTIFIER": ""}):
            root = Path(d) / "tree"
            root.mkdir()
            (root / "fixture.py").write_text("print('ok')\n", encoding="utf-8")
            out = Path(d) / "out"
            out.mkdir()
            with mock.patch.object(build_public, "tracked_files",
                                   return_value=["fixture.py"]), \
                 mock.patch.object(build_public, "REQUIRED", set()), \
                 mock.patch.object(build_public.subprocess, "run",
                                   return_value=done):
                manifest = build_public.build(root, out)
        self.assertEqual(manifest["source_revision"], "deadbeef")


if __name__ == "__main__":
    unittest.main()
