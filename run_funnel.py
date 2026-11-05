"""Run the whole funnel: Tier 1 -> Tier 2 -> Tier 3, then log the verdict.

One command, one log line per run, non-zero-free exit so a scheduler never
mistakes a stand-down for a failure. The funnel standing down is a *successful*
run — that is the whole point of the gates.

    python run_funnel.py
    python run_funnel.py --target-utc 13:30        # no-op unless it is ~13:30 UTC

--target-utc exists because Windows Task Scheduler stores local time and this
machine is Europe/Paris: a "14:30 daily" task fires at 12:30 UTC once DST flips.
Scheduling the task hourly and letting the wrapper decide keeps it at 13:30 UTC
all year. Omit the flag to always run.
"""

from __future__ import annotations

# Locked at 1000, mirroring tier3_deep.PERMUTATIONS. Duplicated rather than
# imported: importing tier3_deep pulls MetaTrader5 at module load, which would
# break this module's importability on CI and in tests. A test asserts the two
# values are equal, so they cannot drift apart silently.
PERMUTATIONS = 1000

import argparse
import atexit
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from keep_awake import keep_awake

HERE = Path(__file__).resolve().parent
LOG = HERE / "funnel_history.log"
LOCK = HERE / "funnel.lock"
PY = sys.executable

# Anti-sleep plus a hard pre-flight: a 50-minute scan against a disconnected
# terminal would otherwise grind through 1,711 empty symbol probes and report a
# quiet market. Abort instead.
EXPECTED_LOGIN = int(os.environ.get("MT5_EXPECTED_LOGIN", "0"))
EXPECTED_SERVER_SUBSTR = "demo"


def preflight_check(term, acct) -> list[str]:
    """Problems that must stop the run before it starts. Empty list = go."""
    problems: list[str] = []
    if term is None:
        problems.append("terminal_info() is None — MetaTrader 5 is not running")
    elif not getattr(term, "connected", False):
        problems.append("terminal reports NOT connected to the broker")

    if acct is None:
        problems.append("account_info() is None — not logged in")
    else:
        if EXPECTED_LOGIN and int(acct.login) != EXPECTED_LOGIN:
            problems.append(f"logged into account {acct.login}, expected {EXPECTED_LOGIN}")
        if EXPECTED_SERVER_SUBSTR not in (acct.server or "").lower():
            problems.append(
                f"server {acct.server!r} is not a DEMO server — refusing to scan a live account")
    return problems


def preflight() -> tuple[bool, list[str]]:
    import marketdata as md
    if not md.mt5.initialize():
        return False, [f"mt5.initialize() failed: {md.mt5.last_error()}"]
    problems = preflight_check(md.mt5.terminal_info(), md.mt5.account_info())
    return (not problems), problems


def log(line: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} | {line}\n")
    print(line)


def run(args: list[str], timeout: int = 5400) -> subprocess.CompletedProcess:
    print(f"\n$ {' '.join(args)}", flush=True)
    return subprocess.run([PY] + args, cwd=HERE, capture_output=True, text=True,
                          timeout=timeout)


def tail(text: str, n: int = 12) -> str:
    """Last n lines, flattened onto ONE line.

    The ledger is line-based: a traceback joined with newlines turns a single
    failed run into several rows and reads like a crash dump in status output.
    Failure context is still kept, just on one line.
    """
    lines = [l.strip() for l in (text or "").strip().splitlines() if l.strip()]
    return " / ".join(lines[-n:])


def in_window(spec: str, now: datetime) -> bool:
    """Is `now` (UTC) inside "HH:MM-HH:MM"? Start inclusive, end exclusive.

    Handles a window that wraps midnight (e.g. "22:00-02:00"), which a naive
    `lo <= t < hi` would treat as always-false.
    """
    start_s, _, end_s = spec.partition("-")
    to_min = lambda s: int(s.split(":")[0]) * 60 + int(s.split(":")[1])
    lo, hi = to_min(start_s), to_min(end_s)
    t = now.hour * 60 + now.minute
    return (lo <= t < hi) if lo < hi else (t >= lo or t < hi)


def acquire_lock(max_age_min: float) -> bool:
    """Exclusive-create the lock. Returns False when another run is active.

    A crashed run must not block the system forever, so an old lock is taken
    over rather than obeyed — the alternative is a permanent silent stop, which
    is the failure mode this whole system exists to avoid.
    """
    if LOCK.exists():
        age_min = (time.time() - LOCK.stat().st_mtime) / 60.0
        if age_min <= max_age_min:
            print(f"funnel.lock held ({age_min:.1f}m old) — another run is active, aborting")
            return False
        print(f"funnel.lock stale ({age_min:.0f}m > {max_age_min:.0f}m) — taking over")
        LOCK.unlink(missing_ok=True)
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        print("funnel.lock appeared mid-acquire — aborting")
        return False
    os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}\n".encode())
    os.close(fd)
    atexit.register(release_lock)
    return True


def release_lock() -> None:
    try:
        LOCK.unlink()
    except FileNotFoundError:
        pass


SESSION_MATRIX = (
    ("05:00", "08:00", ("donchian",)),
    ("08:00", "13:00", ("donchian", "bollinger")),
    ("13:00", "17:00", ("donchian", "rsi", "bollinger")),
)


def session_strategies(now: datetime) -> tuple[str, ...]:
    """Which strategy families to scan at this UTC minute.

    Fewer families early, because every family is another hypothesis the
    Bonferroni bar has to pay for. A trend breakout needs the least structure to
    justify scanning it; mean reversion is only worth testing once London is
    open and the range is real. Empty tuple = outside every session.
    """
    minute = now.hour * 60 + now.minute
    for start, end, fams in SESSION_MATRIX:
        s = int(start[:2]) * 60 + int(start[3:])
        e = int(end[:2]) * 60 + int(end[3:])
        if s <= minute < e:
            return fams
    return ()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true",
                    help="run Tier 1 then rebuild liquid_universe.json, then exit "
                         "(no trade scan). This is the only mode that pays the "
                         "~35 minute whole-universe cost.")
    ap.add_argument("--target-utc", default=None, help='e.g. "13:30" (rebuild window)')
    ap.add_argument("--window-utc", default=None,
                    help='e.g. "05:00-17:00" — start inclusive, end exclusive. '
                         'Outside it the run exits 0 immediately. Enforced in Python '
                         'rather than in Task Scheduler because the scheduler stores '
                         'LOCAL time and Europe/Paris shifts by an hour at DST.')
    ap.add_argument("--at-utc", default=None, metavar="HH:MM",
                    help="pretend it is this UTC time (for verifying the session matrix)")
    ap.add_argument("--plan-only", action="store_true",
                    help="print the session plan and expected test count, then exit")
    ap.add_argument("--tolerance-min", type=int, default=10)
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--top", type=int, default=50, help="intraday universe size")
    ap.add_argument("--shortlist", type=int, default=5)
    ap.add_argument("--bars", type=int, default=15000)
    ap.add_argument("--horizon", type=int, default=32)
    ap.add_argument("--permutations", type=int, default=PERMUTATIONS,
                    choices=[PERMUTATIONS],
                    help="locked at 1000: rejects a lower count rather than clamping")
    ap.add_argument("--lock-max-age-min", type=float, default=120.0)
    a = ap.parse_args()

    # Lock first: an overlapping run must cost nothing, not a preflight + MT5 init.
    if not acquire_lock(a.lock_max_age_min):
        return 0

    now = datetime.now(timezone.utc)
    if a.at_utc:
        hh_a, mm_a = (int(x) for x in a.at_utc.split(":"))
        now = now.replace(hour=hh_a, minute=mm_a, second=0, microsecond=0)
        print(f"--at-utc: pretending it is {now:%H:%M}Z")

    if a.target_utc:
        hh, mm = (int(x) for x in a.target_utc.split(":"))
        delta = abs((now.hour * 60 + now.minute) - (hh * 60 + mm))
        delta = min(delta, 1440 - delta)
        if delta > a.tolerance_min:
            print(f"outside the {a.target_utc}Z window (delta {delta}m) — no-op")
            return 0

    if a.window_utc:
        if not in_window(a.window_utc, now):
            print(f"outside the {a.window_utc}Z window ({now:%H:%M}Z) — no-op")
            return 0

    fams = session_strategies(now)
    print(f"session matrix @ {now:%H:%M}Z: {', '.join(fams) if fams else 'none'}")
    if a.plan_only:
        n_sym = 50
        try:
            n_sym = len(json.loads((HERE / "liquid_universe.json").read_text())["symbols"])
        except Exception:
            pass
        print(f"plan: {n_sym} symbols x {len(fams)} strategies = "
              f"{n_sym * len(fams)} tests this scan")
        try:
            import trials_total
            print(f"prior trials on record: {sum(trials_total.total())}")
        except Exception:
            pass
        return 0
    if not fams:
        print(f"outside every session in the matrix ({now:%H:%M}Z) — no-op")
        return 0

    ok, problems = preflight()
    if not ok:
        print("\n" + "=" * 72)
        print("PREFLIGHT FAILED — aborting before the scan")
        for p in problems:
            print(f"  * {p}")
        print("A long scan against a dead terminal only produces empty data.")
        print("=" * 72)
        log(f"FAILED preflight | {' | '.join(problems)}")
        return 2

    # Hold the machine awake for the run; atexit guarantees release on every
    # return path, and Windows drops the request anyway if the process dies.
    awake = keep_awake()
    awake.__enter__()
    atexit.register(awake.__exit__, None, None, None)
    print(f"preflight ok | anti-sleep: {'active' if awake.active else 'inactive'} "
          f"| mode: {'rebuild' if a.rebuild else 'intraday'} | tf: {a.tf}")

    if a.rebuild:
        # min-days 160 => ~15,400 M15 bars available, the deep-test window.
        t1 = run(["tier1_universe.py", "--min-days", "160", "--h1-bars", "6000"])
        if t1.returncode != 0:
            log(f"FAILED tier1 rc={t1.returncode} | {tail(t1.stderr, 3)}")
            return 1
        print(tail(t1.stdout, 6))
        t1b = run(["make_liquid.py", "--top", str(a.top), "--tf", a.tf])
        if t1b.returncode != 0:
            log(f"FAILED make_liquid rc={t1b.returncode} | {tail(t1b.stderr, 3)}")
            return 1
        print(tail(t1b.stdout, 6))
        log(f"REBUILT universe={a.top} from {tail(t1.stdout, 1)}")
        return 0

    if not (HERE / "liquid_universe.json").exists():
        print("no liquid_universe.json — run with --rebuild first")
        log("FAILED intraday | liquid_universe.json missing")
        return 2

    t2 = run(["tier2_patterns.py", "--tier1", "liquid_universe.json",
              "--tf", a.tf, "--top", str(a.shortlist),
              "--strategies", ",".join(fams)])
    if t2.returncode != 0:
        log(f"FAILED tier2 rc={t2.returncode} | {tail(t2.stderr, 3)}")
        return 1
    print(tail(t2.stdout, 10))

    # The gate must price EVERY hypothesis ever tested, including this scan.
    # Reading only the per-scan count let a 132-test scan apply a 3.55 bar
    # after 5,388 trials - the exact data-snooping the project exists to stop.
    try:
        import trials_total
        prior = sum(trials_total.total())
    except Exception:
        prior = 0
    try:
        scan_tests = int(json.loads((HERE / "tier2_candidates.json").read_text())
                         .get("total_tests") or 0)
    except Exception:
        scan_tests = 0
    cumulative = prior + scan_tests
    print(f"trials: {prior} prior + {scan_tests} this scan = {cumulative}")

    t3 = run(["tier3_deep.py", "--tf", a.tf, "--bars", str(a.bars),
              "--horizon", str(a.horizon), "--permutations", str(a.permutations),
              "--trials", str(cumulative)])
    # tier3 exits 2 on a clean stand-down: that is success, not failure
    if t3.returncode not in (0, 2):
        log(f"FAILED tier3 rc={t3.returncode} | {tail(t3.stderr, 3)}")
        return 1
    print(tail(t3.stdout, 14))

    try:
        doc = json.loads((HERE / "tier3_results.json").read_text())
    except Exception as e:
        log(f"FAILED reading tier3_results.json: {e}")
        return 1
    try:
        universe = json.loads((HERE / "tier2_candidates.json").read_text())
    except Exception:
        universe = {}

    # Tier 3 writes the funnel_history.log line itself (it owns the trials count,
    # so manual runs are recorded too). Do not log a summary here as well.
    print(f"\ntier3 verdict: {'STAND_DOWN' if doc.get('stood_down') else 'WINNER ' + ','.join(doc.get('survivors', []))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
