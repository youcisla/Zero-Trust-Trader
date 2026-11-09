"""Offline safety regression entry point; never connects to a trading terminal.

The former direct-order test bypassed bridge consent. Entry execution belongs
exclusively to the human-approved bridge; policy replay cannot verify consent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent
    sys.path[:0] = [str(root), str(root / "tests")]
    from _mt5stub import install
    install()
    print("Offline safety checks: MT5 is stubbed; no terminal or order calls.")
    suite = unittest.defaultTestLoader.loadTestsFromNames(
        ["test_safety", "test_position_manager", "test_supervisor_failures"])
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
