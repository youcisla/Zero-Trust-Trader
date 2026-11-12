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
# The count lives in timemachine_mt5; a second literal is how the two drift.
from timemachine_mt5 import PERMUTATIONS  # noqa: E402  (1000, CLI-locked)

import argparse
import atexit
import json
import os
import subprocess
import sys
import time
import uuid

import kill_switch
from datetime import datetime, timezone
from pathlib import Path

from keep_awake import keep_awake

import audit
import process_lock

HERE = Path(__file__).resolve().parent
LOG = HERE / "funnel_history.log"
LOCK = HERE / "funnel.lock"
PY = sys.executable

# Anti-sleep plus a hard pre-flight: a 50-minute scan against a disconnected
# terminal would otherwise grind through 1,711 empty symbol probes and report a
# quiet market. Abort instead.
ENV_FILE = HERE / ".env"


def load_env() -> None:
    """Load .env into the process, without overriding what is already set.

    The pin used to live only in `run_funnel.local.cmd`, so a scan started from
    anywhere else - a shell, the dashboard button - was refused with
    "MT5_EXPECTED_LOGIN is not set". That refusal was correct and the launcher was
    the only caller that could satisfy it. Reading the same file the desk already
    reads keeps one source for the pin and leaves the launcher (and any exported
    override) winning.
    """
    try:
        lines = ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value



load_env()

def _int_env(name: str) -> int:
    """A malformed pin must not raise at import: the funnel's job is to refuse cleanly."""
    try:
        return int(os.environ.get(name, "0") or 0)
    except ValueError:
        return 0


# Import-time mirror, kept because `build_public.py` rewrites this exact line when
# it sanitises a copy of the tree. Pre-flight does NOT read it (see below).
EXPECTED_LOGIN = _int_env("MT5_EXPECTED_LOGIN")
EXPECTED_SERVER_SUBSTR = "demo"
_SCAN_ID = ""


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
        raw_pin = os.environ.get("MT5_EXPECTED_LOGIN", "")
        # Read at call time, never from the import-time constant: the launcher, a
        # shell export and a `.env` all have to apply without a restart, and an
        # unset pin must refuse rather than inherit whatever import saw.
        expected = _int_env("MT5_EXPECTED_LOGIN")
        if raw_pin and not raw_pin.isdecimal():
            problems.append("MT5_EXPECTED_LOGIN is not a numeric account id — refusing")
        elif not expected:
            # Fail closed: an unset pin used to skip the check silently, which
            # means a clone with no env var would scan whatever account it found.
            problems.append("MT5_EXPECTED_LOGIN is not set — refusing to scan an unpinned account")
        elif int(acct.login) != expected:
            problems.append(f"logged into account {acct.login}, expected {expected}")
        if EXPECTED_SERVER_SUBSTR not in (acct.server or "").lower():
            problems.append(
                f"server {acct.server!r} is not a DEMO server — refusing to scan a live account")
        if getattr(acct, "trade_mode", None) != 0:
            problems.append("account trade mode is not DEMO — refusing unverifiable account mode")
    return problems


def preflight() -> tuple[bool, list[str]]:
    if (HERE / ".ztt-maintenance").exists():
        return False, ["maintenance in progress"]
    import marketdata as md
    if not md.mt5.initialize():
        return False, [f"mt5.initialize() failed: {md.mt5.last_error()}"]
    problems = preflight_check(md.mt5.terminal_info(), md.mt5.account_info())
    return (not problems), problems


def log(line: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} | {line}" + (f" scan_id={_SCAN_ID}" if _SCAN_ID else "") + "\n")
    print(line)


def run(args: list[str], timeout: int = 5400) -> subprocess.CompletedProcess:
    if (HERE / ".ztt-maintenance").exists():
        raise RuntimeError("maintenance in progress; subprocess refused")
    print(f"\n$ {' '.join(args)}", flush=True)
    return subprocess.run([PY] + args, cwd=HERE, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, "ZTT_FUNNEL_PARENT": str(os.getpid())})


def tail(text: str, n: int = 12) -> str:
    """Last n lines, flattened onto ONE line.

    The ledger is line-based: a traceback joined with newlines turns a single
    failed run into several rows and reads like a crash dump in status output.
    Failure context is still kept, just on one line.
    """
    lines = [l.strip() for l in (text or "").strip().splitlines() if l.strip()]
    return " / ".join(lines[-n:])


def _progress_path() -> Path:
    """Resolved at call time: a test that patches HERE must not write into the tree."""
    return HERE / "scan_progress.json"


def progress(stage: str, status: str = "running", **extra) -> None:
    """Where the scan is right now, for a dashboard that cannot ask it anything.

    Written atomically: the UI polls this file every second and must never read
    half a document. A failure to write is never allowed to fail the scan - the
    scan is the product, the progress line is a convenience.
    """
    doc = {"scan_id": _SCAN_ID, "stage": stage, "status": status,
           "updated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    doc.update(extra)
    target = _progress_path()
    try:
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        pass


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
    """Hold OS ownership; age alone can never evict a live process."""
    return process_lock.acquire(LOCK, max_age_min * 60)


def release_lock() -> None:
    process_lock.release(LOCK)


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


def preflight_report() -> int:
    """Five seconds, no lock, no scan: can this tree run at all right now?

    Every refusal here is one the 35-minute pass would hit anyway, later and at
    the cost of the whole run. It reads; it never writes.
    """
    import kill_switch
    import trials_total

    t0 = time.monotonic()
    why = kill_switch.active()
    if why:
        print(f"PREFLIGHT REFUSED  kill switch active ({why})")
        return kill_switch.EXIT_KILLED

    status, detail = trials_total.verify()
    print(f"trials     {status}{(' — ' + detail) if detail else ''}")
    if status != "ok":
        print("PREFLIGHT REFUSED  the trial chain does not verify; nothing can be counted")
        return 1

    try:
        import marketdata as md
    except Exception as e:  # noqa: BLE001 - the terminal module is optional off-machine
        print(f"terminal   unavailable: {type(e).__name__}: {e}")
        return 1
    if not md.mt5.initialize():
        print(f"terminal   not running: {md.mt5.last_error()}")
        return 1
    term = md.mt5.terminal_info()
    acct = md.mt5.account_info()
    server = getattr(term, "name", None) or "?"
    problems = preflight_check(term, acct)
    offset = md.broker_offset()
    print(f"terminal   {server} · login {getattr(acct, 'login', '?')} · "
          f"broker offset {offset:+d}s")
    # One symbol, one bar: enough to prove the closed-bar path works.
    bars = md.fetch("EURUSD", "H1", 5)
    print(f"bars       {'5 closed EURUSD H1' if bars is not None else 'none — closed-bar path refused'}")
    for p_ in problems:
        print(f"PROBLEM    {p_}")
    secs = time.monotonic() - t0
    ok = not problems and bars is not None
    print(f"{'PREFLIGHT OK' if ok else 'PREFLIGHT REFUSED'}  {secs:.2f}s")
    return 0 if ok else 1


def main() -> int:
    try:
        return _main()
    finally:
        release_lock()


def _main() -> int:
    global _SCAN_ID
    _SCAN_ID = uuid.uuid4().hex
    t0 = time.monotonic()
    # Before anything touches the terminal, the lock or the ledger: a human may
    # have stopped the system, and a scan that runs anyway wastes 35 minutes and
    # rewrites the universe with a market they were trying to freeze.
    why = kill_switch.active()
    if why:
        print(f"REFUSED: kill switch active ({why}). Nothing started.")
        return kill_switch.EXIT_KILLED
    ap = argparse.ArgumentParser()
    load_env()
    ap.add_argument("--preflight", action="store_true",
                    help="five-second readiness check (kill switch, trial chain, terminal, "
                         "clock, session) and exit; does not scan")
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
    ap.add_argument("--pool", type=int, default=0,
                    help="candidates considered before correlation dedupe; 0 = all measured")
    ap.add_argument("--top", type=int, default=0,
                    help="intraday universe size: 120 covers shares as well as FX, "
                         "indices, commodities and crypto; 50 held no shares outside the top 15")
    ap.add_argument("--shortlist", type=int, default=5)
    ap.add_argument("--bars", type=int, default=15000)
    ap.add_argument("--horizon", type=int, default=32)
    ap.add_argument("--permutations", type=int, default=PERMUTATIONS,
                    choices=[PERMUTATIONS],
                    help="locked at 1000: rejects a lower count rather than clamping")
    ap.add_argument("--t-gate", action="store_true",
                    help="gate the deep test on the mean-R t instead of the win-rate z. "
                         "The live exit policy books no fixed target, so p0=1/(1+rr) is "
                         "unreachable and the binomial only measures who lost more slowly.")
    ap.add_argument("--lock-max-age-min", type=float, default=120.0)
    a = ap.parse_args()

    if a.preflight:
        return preflight_report()

    if a.at_utc and not a.plan_only:
        ap.error("--at-utc is available only with --plan-only")

    if (HERE / ".ztt-maintenance").exists() and not a.plan_only:
        print("REFUSED: maintenance in progress; no terminal connection or scan")
        return 2

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
    progress("preflight", strategies=fams)
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
        audit.write("refused", stage="preflight", problems=problems)
        # A refusal must not leave the strip saying "pre-flight, running" forever.
        progress("preflight", status="failed", error="; ".join(problems))
        return 2

    # Hold the machine awake for the run; atexit guarantees release on every
    # return path, and Windows drops the request anyway if the process dies.
    awake = keep_awake()
    awake.__enter__()
    atexit.register(awake.__exit__, None, None, None)
    print(f"preflight ok | anti-sleep: {'active' if awake.active else 'inactive'} "
          f"| mode: {'rebuild' if a.rebuild else 'intraday'} | tf: {a.tf}")
    progress("universe", strategies=fams)

    if a.rebuild:
        # min-days 160 => ~15,400 M15 bars available, the deep-test window.
        try:
            t1 = run(["tier1_universe.py", "--min-days", "160", "--h1-bars", "6000",
                      "--lock-held"])
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            log(f"FAILED tier1 {type(exc).__name__} | {exc}")
            return 1
        if t1.returncode != 0:
            log(f"FAILED tier1 rc={t1.returncode} | {tail(t1.stderr, 3)}")
            return 1
        print(tail(t1.stdout, 6))
        # Pass both caps. Leaving `--pool` out meant "everything" rebuilt 150
        # candidates and kept 93 — fewer symbols than the 120 cap it replaced.
        try:
            t1b = run(["make_liquid.py", "--top", str(a.top), "--pool", str(a.pool),
                       "--tf", a.tf, "--lock-held"])
        except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
            log(f"FAILED make_liquid {type(exc).__name__} | {exc}")
            return 1
        if t1b.returncode != 0:
            log(f"FAILED make_liquid rc={t1b.returncode} | {tail(t1b.stderr, 3)}")
            return 1
        print(tail(t1b.stdout, 6))
        # Log what was written, not what was requested: `--top 0` means "everything
        # tier 1 kept", and the ledger should carry the count, never the flag.
        try:
            wrote = json.loads((HERE / "liquid_universe.json").read_text(encoding="utf-8")).get("count")
        except (OSError, ValueError):
            wrote = None
        log(f"REBUILT universe={wrote if wrote is not None else 'unknown'} from {tail(t1.stdout, 1)}")
        return 0

    if not (HERE / "liquid_universe.json").exists():
        print("no liquid_universe.json — run with --rebuild first")
        log("FAILED intraday | liquid_universe.json missing")
        return 2
    try:
        universe = json.loads((HERE / "liquid_universe.json").read_text())["symbols"]
    except (OSError, ValueError, KeyError):
        universe = []
    if not universe:
        # Scanning nothing and printing STAND_DOWN is indistinguishable from a
        # working funnel with no edge. It is not the same thing and must not log
        # as if it were: nine runs went by before anyone noticed.
        print("liquid_universe.json has 0 symbols — nothing to scan")
        log("FAILED intraday | liquid_universe.json has 0 symbols — rebuild")
        return 2

    try:
        import trials_total
        prior = trials_total.required_trials(0, LOG)
    except (OSError, ValueError, TypeError, ImportError) as exc:
        log(f"FAILED trial accounting before scan | {type(exc).__name__}: {exc}")
        return 1

    progress("tier2", symbols=len(universe), strategies=fams)
    try:
        t2 = run(["tier2_patterns.py", "--tier1", "liquid_universe.json",
                  "--tf", a.tf, "--top", str(a.shortlist),
                  "--strategies", ",".join(fams), "--scan-id", _SCAN_ID])
    except RuntimeError as exc:
        log(f"FAILED before tier2 | {exc}")
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        log(f"FAILED tier2 total_tests=UNKNOWN | {type(exc).__name__}")
        return 1
    if t2.returncode != 0:
        count = "UNKNOWN"
        try:
            failed_stage = json.loads((HERE / "tier2_candidates.json").read_text(encoding="utf-8"))
            if not isinstance(failed_stage, dict):
                raise ValueError("Tier 2 artifact must be an object")
            if failed_stage.get("scan_id") == _SCAN_ID:
                trials_total.required_trials(failed_stage["total_tests"], LOG)
                count = str(failed_stage["total_tests"])
        except (OSError, ValueError, TypeError, KeyError):
            pass
        log(f"FAILED tier2 rc={t2.returncode} total_tests={count} | {tail(t2.stderr, 3)}")
        return 1
    print(tail(t2.stdout, 10))

    # The gate must price EVERY hypothesis ever tested, including this scan.
    # Reading only the per-scan count let a 132-test scan apply a 3.55 bar
    # after 5,388 trials - the exact data-snooping the project exists to stop.
    try:
        stage2 = json.loads((HERE / "tier2_candidates.json").read_text(encoding="utf-8"))
        if not isinstance(stage2, dict):
            raise ValueError("Tier 2 artifact must be an object")
        if stage2.get("scan_id") != _SCAN_ID:
            raise ValueError("Tier 2 artifact belongs to another scan")
        scan_tests = stage2["total_tests"]
        cumulative = trials_total.required_trials(scan_tests, LOG)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        log(f"FAILED trial accounting total_tests=UNKNOWN | {type(exc).__name__}: {exc}")
        return 1
    print(f"trials: {prior} prior + {scan_tests} this scan = {cumulative}")
    progress("tier3", tests=scan_tests, cumulative=cumulative)

    t3_args = ["tier3_deep.py", "--tf", a.tf, "--bars", str(a.bars),
               "--horizon", str(a.horizon), "--permutations", str(a.permutations),
               "--trials", str(cumulative), "--scan-id", _SCAN_ID]
    if a.t_gate:
        t3_args.append("--t-gate")
    if a.window_utc:
        # The deep test must close at the end of the window it is testing into;
        # deriving it from --window-utc stops the two from drifting apart.
        t3_args += ["--session-close", a.window_utc.partition("-")[2].strip()]
    t3_started = time.time()
    try:
        t3 = run(t3_args)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        log(f"FAILED tier3 total_tests={scan_tests} | {type(exc).__name__}")
        return 1
    # tier3 exits 2 on a clean stand-down: that is success, not failure
    if t3.returncode not in (0, 2):
        log(f"FAILED tier3 rc={t3.returncode} total_tests={scan_tests} | {tail(t3.stderr, 3)}")
        return 1
    print(tail(t3.stdout, 14))

    # An exit code is the child's claim about itself. Check the artifact it was
    # supposed to write: a scan that dies between its last write and its exit
    # leaves last run's file in place, and reading that would report a previous
    # verdict — and log it to the audit trail — as if this scan had produced it.
    art = HERE / "tier3_results.json"
    try:
        if art.stat().st_mtime < t3_started - 1:
            log(f"FAILED tier3 wrote no artifact this run total_tests={scan_tests} — refusing to report a "
                "previous scan's verdict")
            return 1
        doc = json.loads(art.read_text())
        if doc.get("scan_id") != _SCAN_ID:
            raise ValueError("Tier 3 artifact belongs to another scan")
    except FileNotFoundError:
        log(f"FAILED tier3 wrote no artifact this run total_tests={scan_tests}")
        return 1
    except Exception as e:
        log(f"FAILED reading tier3_results.json total_tests={scan_tests}: {e}")
        return 1
    try:
        universe = json.loads((HERE / "tier2_candidates.json").read_text())
    except Exception:
        universe = {}

    # Tier 3 writes the funnel_history.log line itself (it owns the trials count,
    # so manual runs are recorded too). Do not log a summary here as well.
    verdict = "STAND_DOWN" if doc.get("stood_down") else "PROPOSAL"
    print(f"\ntier3 verdict: {'STAND_DOWN' if doc.get('stood_down') else 'WINNER ' + ','.join(doc.get('survivors', []))}")
    progress("verdict", status="done", verdict=verdict, tests=scan_tests,
             cumulative=cumulative, survivors=doc.get("survivors", []))
    audit.write(
        "verdict",
        scan_id=_SCAN_ID,
        verdict="STAND_DOWN" if doc.get("stood_down") else "PROPOSAL",
        survivors=doc.get("survivors", []),
        # How long the scan took. Without it, a missed schedule slot and a slow
        # scan look identical in the log, and that ambiguity cost an afternoon:
        # the 09:30 break in cadence was CPU contention from builds, not the
        # 3-strategy window that opens four hours later.
        dur_s=round(time.monotonic() - t0, 1),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
