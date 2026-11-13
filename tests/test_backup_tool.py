"""The backup tool must import; it once imported a module that does not exist.

`backup_market_watch.py` read `artifact_symbols` from `purge_market_watch`, which
was deleted, so the script raised ModuleNotFoundError before it could back up
anything. The stub keeps the MetaTrader5 import working off-Windows.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
import _mt5stub  # noqa: E402

_mt5stub.install()
import backup_market_watch  # noqa: E402


class BackupTool(unittest.TestCase):
    def test_it_imports_and_exposes_the_artifact_lookup(self):
        self.assertTrue(callable(backup_market_watch.artifact_symbols))

    def test_the_lookup_returns_a_symbol_set(self):
        self.assertIsInstance(backup_market_watch.artifact_symbols(), set)


if __name__ == "__main__":
    unittest.main()
