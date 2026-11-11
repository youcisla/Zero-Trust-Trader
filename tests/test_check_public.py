"""The privacy scan must fail on the identifier this tree actually protects.

The login sat in a tracked test fixture for weeks and the scan passed, because the
scan only knows an identifier when one is configured - and on the machine that
commits, the only place it exists is `.env`. This proves the comparison works,
so the next slip fails before it is pushed rather than after.
"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tools")]

import check_public  # noqa: E402


class TheIdentifierCheck(unittest.TestCase):
    def test_a_file_carrying_the_identifier_fails(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "fixture.py").write_text("LOGIN = 20481736\n", encoding="utf-8")
            found = check_public.scan_files(root, ["fixture.py"], "20481736")
        self.assertEqual(found, [("fixture.py", "private-identifier")])

    def test_the_same_file_passes_when_the_identifier_is_not_configured(self):
        # This is the state CI is in without the secret: generic patterns only.
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "fixture.py").write_text("LOGIN = 20481736\n", encoding="utf-8")
            self.assertEqual(check_public.scan_files(root, ["fixture.py"], ""), [])

    def test_a_machine_path_still_fails_without_any_identifier(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "notes.md").write_text("see C:" + "\\Users\\someone\\x\n", encoding="utf-8")
            found = check_public.scan_files(root, ["notes.md"], "")
        self.assertTrue(any(rule == "machine-path" for _, rule in found), found)


if __name__ == "__main__":
    unittest.main()
