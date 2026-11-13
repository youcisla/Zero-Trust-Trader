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
import json
import sys
import os
import re
from datetime import datetime, timezone
from pathlib import Path

SIG_PREFIX = " sig="
ANCHOR_NAME = "ledger_head.json"

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
                    # One chain check per total(), not one per reconcile row: it
                    # reads the whole ledger every time it runs. The pure chain
                    # state is used, never verify(), so verify() may call total()
                    # to compute its anchor count without recursing.
                    if reconciled is None:
                        key = ledger_key()
                        reconciled = (key, "ok" if key is None
                                      else _chain_state(lines, key)[0])
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


def _chain_state(lines: list[str], key: bytes | None) -> tuple[str, str]:
    """The hash chain alone: ("ok" | "unsigned" | "broken", detail).

    Only signed lines are chained, so the FAILED rows written by the
    orchestrator do not break it. Deleting a signed row, editing one, or
    reordering two all break the chain at the point it happened.
    """
    signed = [(i, ln) for i, ln in enumerate(lines, 1) if SIG_PREFIX in ln]
    if not signed:
        return "unsigned", "no signed rows (the ledger predates the chain)"
    if key is None:
        return "unsigned", f"{len(signed)} signed rows, but no key to verify them"
    prev = "genesis"
    for lineno, line in signed:
        body, sig = line.rsplit(SIG_PREFIX, 1)
        if line_sig(key, prev, body) != sig.strip():
            return "broken", f"chain breaks at line {lineno} - the ledger was edited"
        prev = sig.strip()
    return "ok", f"{len(signed)} signed rows, chain intact"


def _anchor_path(log: Path) -> Path:
    return log.parent / ANCHOR_NAME


def _anchor_sig(key: bytes, count: int, signed_tail: str) -> str:
    """A self-contained signature binding the anchored count to the chain tail."""
    return hmac.new(key, f"anchor|{count}|{signed_tail}".encode(),
                    hashlib.sha256).hexdigest()


def _load_anchor(log: Path) -> dict | None:
    """The signed anchor, or None when it does not exist yet.

    A present-but-unreadable anchor raises: fail closed rather than trust an
    anchor that cannot be checked.
    """
    p = _anchor_path(log)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        int(data["count"])
        str(data["signed_tail"])
        str(data["sig"])
        return data
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"ledger anchor is unreadable: {exc}") from exc


def write_anchor(count: int, signed_tail: str, key: bytes | None = None,
                 path: Path | None = None) -> dict | None:
    """Persist the signed head: {count, signed_tail, generated_utc, sig}.

    Keyless trees have no anchor to sign, so they return None and stay
    `unsigned` (stated, not trusted). The write is atomic: a half-written
    anchor must never be read back as a valid one.
    """
    k = key if key is not None else ledger_key()
    if k is None:
        return None
    log = path or LOG
    p = _anchor_path(log)
    rec = {"count": int(count), "signed_tail": str(signed_tail),
           "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "sig": _anchor_sig(k, int(count), str(signed_tail))}
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(rec, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, p)
    return rec


def refuses(state: str, path: Path | None = None, key: bytes | None = None) -> bool:
    """May a scan proceed on this ledger state?

    broken/truncated/rollback always refuse. unsigned/empty refuse only when a
    ledger key is configured AND an anchor already exists - i.e. a previously
    anchored, signed ledger was wiped or rolled back. On a fresh tree with no
    anchor yet, empty/unsigned must be allowed: that is the only state from
    which the first run can write its first signed row and seed the anchor.
    Refusing there would brick every new keyed deployment. This is the one
    predicate both run_funnel and tier3_deep ask, so they cannot drift.
    """
    if state in ("broken", "truncated", "rollback"):
        return True
    if state not in ("unsigned", "empty"):
        return False
    log = path or LOG
    if not _anchor_path(log).exists():
        return False  # no anchor yet: a fresh tree, let the first run start
    k = key if key is not None else ledger_key()
    return k is not None


def verify(path: Path | None = None, key: bytes | None = None,
           lines: list[str] | None = None, bootstrap: bool = True) -> tuple[str, str]:
    """("ok" | "unsigned" | "broken" | "empty" | "truncated" | "rollback", detail).

    The signature chain catches an edit or a deletion in the middle. The signed
    anchor (ledger_head.json) catches what the chain alone cannot see: a deleted
    newest signed row (truncated) or a ledger whose count fell below the
    anchored one (rollback), including emptying the file.

    Bootstrap: when a key is configured, signed rows exist and no anchor does
    yet, the chain is verified FIRST and only an `ok` chain may seed the anchor.
    `bootstrap=False` is for read-only callers (preflight, status): the chain is
    still checked, but nothing is written - the next scan seeds the anchor.
    """
    log = path or LOG
    k = key if key is not None else ledger_key()
    try:
        anchor = _load_anchor(log)
    except ValueError as exc:
        return "broken", str(exc)
    if lines is None:
        if not log.exists():
            if anchor is not None and k is not None:
                return "truncated", "the ledger is gone but an anchor exists"
            return "empty", "no ledger yet"
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    signed = [(i, ln) for i, ln in enumerate(lines, 1) if SIG_PREFIX in ln]
    if not signed:
        if anchor is not None and k is not None:
            return "truncated", "no signed rows remain but an anchor exists"
        if not log.exists():
            return "empty", "no ledger yet"
        return "unsigned", "no signed rows (the ledger predates the chain)"
    if k is None:
        return "unsigned", f"{len(signed)} signed rows, but no key to verify them"
    state, detail = _chain_state(lines, k)
    if state != "ok":
        return state, detail
    signed_tail = last_sig(log, lines=lines)
    try:
        count = sum(total(log, lines=lines))
    except ValueError as exc:
        # A count that cannot be established is an untrustworthy ledger, not a
        # crash: report it as broken so every gate refuses instead of raising.
        return "broken", f"cannot count the ledger: {exc}"
    if anchor is None:
        # One-time migration: the chain already verifies, so this is the only
        # state an anchor may be seeded from. A broken chain never gets here.
        if not bootstrap:
            return "ok", f"{detail}; no anchor yet (not seeded by a read-only check)"
        write_anchor(count, signed_tail, key=k, path=log)
        print(f"ledger anchor created: count={count} tail={signed_tail[:8]}",
              file=sys.stderr)
        return "ok", f"{detail}; anchor created"
    if not hmac.compare_digest(_anchor_sig(k, anchor["count"], anchor["signed_tail"]),
                               anchor["sig"]):
        return "broken", "ledger anchor signature does not verify - the anchor was edited"
    if count < anchor["count"]:
        return "rollback", (f"recomputed count {count} is below the anchored "
                            f"{anchor['count']}")
    if signed_tail != anchor["signed_tail"]:
        # The anchored tail must still be somewhere in the chain. Present means
        # rows were only appended past the anchor (a crash between the append
        # and the anchor write); absent means the newest signed rows were cut.
        if anchor["signed_tail"] not in {ln.rsplit(SIG_PREFIX, 1)[1].strip()
                                         for ln in lines if SIG_PREFIX in ln}:
            return "truncated", "the newest signed row was removed - chain tail differs from the anchor"
    return "ok", detail


if __name__ == "__main__":
    legacy, logged = total()
    print(legacy + logged)
    print(f"# legacy={legacy} logged={logged}", flush=True)
    state, detail = verify()
    print(f"# chain={state} {detail}", flush=True)
