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
import sys
import os
import re
from pathlib import Path

SIG_PREFIX = " sig="

HERE = Path(__file__).resolve().parent
LOG = HERE / "funnel_history.log"

LEGACY_TRIALS = 2205

TOTAL_RE = re.compile(r"\btotal_tests=(\d+)(?=\s|\||$)")
TRIALS_RE = re.compile(r"\btrials=(\d+)(?=\s|\||$)")
SCAN_RE = re.compile(r"\bscan_id=([A-Za-z0-9_-]+)(?=\s|\||$)")


def read_lines(path: Path | None = None) -> list[str]:
    """The ledger split into lines, with the same strict decode total() uses.

    A process that needs verify() and total() together reads once and passes the
    result: the ledger is append-only and keeps growing forever.
    """
    log = path or LOG
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def total(path: Path | None = None, lines: list[str] | None = None) -> tuple[int, int]:
    """(legacy, logged) — logged prefers total_tests, falls back to trials."""
    logged = 0
    scans: dict[str, int] = {}
    unresolved: set[str] = set()
    log = path or LOG
    if lines is None:
        lines = read_lines(log)
    reconciled: tuple[bytes | None, str] | None = None
    for line in lines:
        scan = SCAN_RE.search(line)
        if "total_tests=UNKNOWN" in line and scan:
            unresolved.add(scan.group(1))
            continue
        m = TOTAL_RE.search(line) if "total_tests=" in line else TRIALS_RE.search(line)
        if m:
            count = int(m.group(1))
            if scan:
                scans[scan.group(1)] = max(scans.get(scan.group(1), 0), count)
                if re.search(r"(?:^|\|\s*)TRIALS_RECONCILED\b", line):
                    # One chain verification per total(), not one per reconcile
                    # row: it reads the whole ledger every time it runs.
                    if reconciled is None:
                        key = ledger_key()
                        reconciled = (key, "ok" if key is None
                                      else verify(log, key, lines=lines)[0])
                    key, chain = reconciled
                    if key is not None and (SIG_PREFIX not in line or chain != "ok"):
                        raise ValueError("trial reconciliation must have a verified ledger signature")
                    unresolved.discard(scan.group(1))
            else:
                logged += count
        elif "total_tests=" in line or re.search(r"\btrials=", line):
            raise ValueError("ledger contains an unverifiable trial count")
    if unresolved:
        raise ValueError("unresolved scan trial count; append verified TRIALS_RECONCILED record")
    return LEGACY_TRIALS, logged + sum(scans.values())


def required_trials(scan_tests: int, path: Path | None = None,
                    lines: list[str] | None = None) -> int:
    """Cumulative correction input; an unverifiable count must stop the scan."""
    if type(scan_tests) is not int or scan_tests < 0:
        raise ValueError("total_tests must be a nonnegative integer")
    return sum(total(path, lines)) + scan_tests


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


def last_sig(path: Path | None = None, lines: list[str] | None = None) -> str:
    log = path or LOG
    if lines is None:
        if not log.exists():
            return "genesis"
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        if SIG_PREFIX in line:
            return line.rsplit(SIG_PREFIX, 1)[1].strip()
    return "genesis"


_WARNED_UNSIGNED = False


def sign_line(body: str, path: Path | None = None, key: bytes | None = None,
              lines: list[str] | None = None) -> str:
    """Append the chain signature to one ledger line."""
    global _WARNED_UNSIGNED
    k = key if key is not None else ledger_key()
    if k is None:
        # Unsigned is a supported state, not an error, but it must not be a
        # quiet one: an operator who never set ZTT_LEDGER_KEY ends up with a
        # ledger only as trustworthy as the disk, while believing otherwise.
        if not _WARNED_UNSIGNED:
            _WARNED_UNSIGNED = True
            print("WARNING: no ZTT_LEDGER_KEY (env or .env) — the ledger is being "
                  "written UNSIGNED and cannot be checked for tampering",
                  file=sys.stderr)
        return body
    return f"{body}{SIG_PREFIX}{line_sig(k, last_sig(path, lines), body)}"


def verify(path: Path | None = None, key: bytes | None = None,
           lines: list[str] | None = None) -> tuple[str, str]:
    """("ok" | "unsigned" | "broken" | "empty", detail).

    Only signed lines are chained, so the FAILED rows written by the
    orchestrator do not break it. Deleting a signed row, editing one, or
    reordering two all break the chain at the point it happened.
    """
    log = path or LOG
    k = key if key is not None else ledger_key()
    if lines is None:
        if not log.exists():
            return "empty", "no ledger yet"
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    elif not lines and not log.exists():
        return "empty", "no ledger yet"
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
