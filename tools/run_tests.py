"""Run local regression tests without a real terminal or external sockets."""

from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "tests"), str(ROOT)]
from _mt5stub import install


def main() -> int:
    install()
    original_connect = socket.socket.connect

    def local_connect(sock, address):
        if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "localhost", "::1"):
            raise RuntimeError("tests refuse external socket connections")
        return original_connect(sock, address)

    socket.socket.connect = local_connect
    try:
        suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    finally:
        socket.socket.connect = original_connect


if __name__ == "__main__":
    raise SystemExit(main())
