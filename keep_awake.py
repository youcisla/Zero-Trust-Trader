"""Stop Windows going to sleep while the funnel scans.

Used as a context manager by run_funnel.py so the anti-sleep guard is active for
exactly the duration of the run and cannot be forgotten:

    from keep_awake import keep_awake
    with keep_awake():
        ...52 minutes of scanning...

Standalone, to hold the machine awake manually:

    python keep_awake.py --minutes 60

Notes
-----
* ES_SYSTEM_REQUIRED prevents sleep; display sleep is left alone (ES_DISPLAY
  would also pin the monitor on, which is not needed to keep MT5 connected).
* ES_CONTINUOUS makes the state stick until cleared; clearing on exit means a
  crashed scan does not leave the machine permanently unable to sleep — Windows
  resets the request if the process dies anyway, so this is belt-and-braces.
* No-ops off Windows so the module stays importable in tests/WSL.
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001

WINDOWS = sys.platform == "win32"


def _set(flags: int) -> int | None:
    """SetThreadExecutionState returns the PREVIOUS state, 0 on failure."""
    if not WINDOWS:
        return None
    try:
        return ctypes.windll.kernel32.SetThreadExecutionState(flags)
    except Exception:
        return None


class _KeepAwake:
    def __init__(self) -> None:
        self.active = False

    def __enter__(self) -> "_KeepAwake":
        prev = _set(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
        # 0 means the call failed; None means not Windows (nothing to guard).
        self.active = prev is not None and prev != 0
        return self

    def __exit__(self, *exc) -> None:
        _set(ES_CONTINUOUS)
        self.active = False


def keep_awake() -> _KeepAwake:
    return _KeepAwake()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=60.0)
    a = ap.parse_args()
    if not WINDOWS:
        print("not Windows — nothing to do")
        return 0
    with keep_awake() as k:
        if not k.active:
            print("FAILED: SetThreadExecutionState was rejected", file=sys.stderr)
            return 1
        print(f"holding system awake for {a.minutes:g} min (Ctrl-C to release)")
        try:
            time.sleep(a.minutes * 60)
        except KeyboardInterrupt:
            print("\nreleased")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
