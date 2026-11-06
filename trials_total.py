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

import hashlib
import hmac
import os
import re
from pathlib import Path

SIG_PREFIX = " sig="

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


def ledger_key(environ=None) -> bytes | None:
    """Signing key for the ledger chain: env var first, then ./.env (gitignored).

    Without a key the ledger still works, but it is only as trustworthy as the
    disk it sits on - `verify()` reports that honestly rather than pretending.
    """
    env = os.environ if environ is None else environ
    k = env.get("ZTT_LEDGER_KEY")
    if k:
        return k.encode()
    f = HERE / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith("ZTT_LEDGER_KEY="):
                return line.split("=", 1)[1].strip().strip('"').encode()
    return None


def line_sig(key: bytes, prev: str, body: str) -> str:
    return hmac.new(key, f"{prev}|{body}".encode(), hashlib.sha256).hexdigest()[:16]


def last_sig(path: Path | None = None) -> str:
    log = path or LOG
    if not log.exists():
        return "genesis"
    for line in reversed(log.read_text(encoding="utf-8", errors="replace").splitlines()):
        if SIG_PREFIX in line:
            return line.rsplit(SIG_PREFIX, 1)[1].strip()
    return "genesis"


def sign_line(body: str, path: Path | None = None, key: bytes | None = None) -> str:
    """Append the chain signature to one ledger line."""
    k = key if key is not None else ledger_key()
    if k is None:
        return body
    return f"{body}{SIG_PREFIX}{line_sig(k, last_sig(path), body)}"


def verify(path: Path | None = None, key: bytes | None = None) -> tuple[str, str]:
    """("ok" | "unsigned" | "broken" | "empty", detail).

    Only signed lines are chained, so the FAILED rows written by the
    orchestrator do not break it. Deleting a signed row, editing one, or
    reordering two all break the chain at the point it happened.
    """
    log = path or LOG
    k = key if key is not None else ledger_key()
    if not log.exists():
        return "empty", "no ledger yet"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    signed = [(i, ln) for i, ln in enumerate(lines, 1) if SIG_PREFIX in ln]
    if not signed:
        return "unsigned", "no signed rows (the ledger predates the chain)"
    if k is None:
        return "unsigned", f"{len(signed)} signed rows, but no key to verify them"
    prev = "genesis"
    for lineno, line in signed:
        body, sig = line.rsplit(SIG_PREFIX, 1)
        if line_sig(k, prev, body) != sig.strip():
            return "broken", f"chain breaks at line {lineno} - the ledger was edited"
        prev = sig.strip()
    return "ok", f"{len(signed)} signed rows, chain intact"


if __name__ == "__main__":
    legacy, logged = total()
    print(legacy + logged)
    print(f"# legacy={legacy} logged={logged}", flush=True)
    state, detail = verify()
    print(f"# chain={state} {detail}", flush=True)
