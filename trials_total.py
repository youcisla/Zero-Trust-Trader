"""Authoritative trials total for the botlab --trials flag.

    python trials_total.py

funnel_history.log is complete only from the moment Tier 3 took over writing it
(2026-11-05T16:37Z). Everything tested before that is recorded in TRIALS.md but
never made it into the log:

    587    pre-funnel research (EURUSD pullback grid x18, XAUUSD, singles)
    568    Phase 3 funnel run
    525    subset smoke (175 x 3) — first matrix pass
    525    subset, revised families
    ----
    2,205  LEGACY_TRIALS

Anything after that cut-over is summed straight from the log, where each line
carries the run's own total_tests. Keep LEGACY_TRIALS frozen: it is history, not
a knob.
"""

from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(__file__).resolve().parent
if WORK.exists():
    HERE = WORK
LOG = HERE / "funnel_history.log"

LEGACY_TRIALS = 2205

TOTAL_RE = re.compile(r"total_tests=(\d+)")
TRIALS_RE = re.compile(r"trials=(\d+)")


def total() -> tuple[int, int]:
    """(legacy, logged) — logged prefers total_tests, falls back to trials."""
    logged = 0
    if LOG.exists():
        for line in LOG.read_text(encoding="utf-8").splitlines():
            if "FAILED" in line:
                continue
            m = TOTAL_RE.search(line) or TRIALS_RE.search(line)
            if m:
                logged += int(m.group(1))
    return LEGACY_TRIALS, logged


if __name__ == "__main__":
    legacy, logged = total()
    print(legacy + logged)
    print(f"# legacy={legacy} logged={logged}", flush=True)
