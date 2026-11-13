"""TIER 3 — deep Time Machine on the Tier 2 shortlist, two required gates.

A candidate is only a survivor if it clears BOTH:

   1. the corrected statistic for cumulative historical and current trials, and
   2. the random-entry permutation test on the same instrument, costs and exits.

Bonferroni does not require independent comparisons; it relies on calibrated
individual tests. Shared data do not make these two gates independent.

Survivors are then de-duplicated by correlation: two instruments above --corr-max
are essentially one trade, and holding both doubles risk without doubling signal.
The higher z wins; the other is dropped with a note.

    python tier3_deep.py
    python tier3_deep.py --permutations 1000 --corr-max 0.7
"""

from __future__ import annotations

# Locked at 1000. A gate whose resolution changes between runs is not a gate:
# at 300 permutations the smallest attainable p-value is 1/301, so the same
# candidate can clear the bar on one run and miss it on the next. This is not a
# performance knob - if a scan is too slow, shrink the universe, not the test.
from timemachine_mt5 import PERMUTATIONS  # noqa: E402  (one literal, one gate)

import argparse
import json
import math
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import marketdata as md
import process_lock
import timemachine_mt5 as tm

HERE = Path(__file__).resolve().parent
TIER2 = HERE / "tier2_candidates.json"
OUT = HERE / "tier3_results.json"

STAND_DOWN = "Market is choppy, no statistically significant edge found today. Standing down."


def log_run(src: dict, payload: dict) -> None:
    """Append the run to funnel_history.log.

    Tier 3 owns this line, not the orchestrator: every deep test declares trials,
    so a run started by hand must be recorded exactly like a scheduled one.
    Leaving it to run_funnel.py meant manual runs left no trace and the trials
    ledger could silently undercount.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    verdict = ("STAND_DOWN" if payload["stood_down"]
               else "WINNER:" + ",".join(payload["survivors"]))
    line = (f"{stamp} | universe={src.get('symbols_in', '?')} "
            f"tested={src.get('symbols_tested', '?')} "
            f"strategies={src.get('strategies_tested', '?')} "
            f"total_tests={payload['trials_declared']} "
            f"triggering={src.get('triggering', '?')} "
            f"deduped={len(src.get('deduped') or [])} "
            f"shortlist={len(payload['results'])} "
            f"passed_both={len(payload['passed_both_gates'])} "
            f"trials={payload['trials_declared']} "
            f"cumulative={payload.get('trials_cumulative', payload['trials_declared'])} "
            f"scan_id={payload['scan_id']} "
            f"permutations={payload['permutations']} | {verdict}")
    # Sign the row into the hash chain: an edited or deleted line breaks
    # verification, which status.py reports loudly. No key = unsigned, stated
    # as such rather than silently trusted.
    import trials_total as tt
    path = HERE / "funnel_history.log"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(tt.sign_line(line, path) + "\n")


def correlation(sym_a: str, sym_b: str, bars_n: int = 3000, tf: str = "H1",
                cache: dict | None = None) -> float | None:
    """Delegates to the shared implementation so Tier 2 and Tier 3 cannot drift."""
    return md.correlation(sym_a, sym_b, bars_n, tf, cache)


def dedupe(survivors: list[dict], corr_max: float,
           stat_key: str = "z", tf: str = "H1") -> tuple[list[dict], list[dict]]:
    """Greedy: keep by descending ``stat_key``, drop anything correlated with a keeper.

    The caller sorts by the statistic the gate actually used, so under --t-gate
    the higher-t edge survives and the higher-z one is the one dropped. The
    correlation is measured on the scan timeframe, the one Tier 2 deduped on.
    One per-run cache serves every pair from one fetch per symbol.
    """
    kept, dropped = [], []
    cache: dict = {}
    for cand in survivors:
        clash = None
        for k in kept:
            rho = correlation(cand["symbol"], k["symbol"], tf=tf, cache=cache)
            if rho is None or not math.isfinite(rho) or abs(rho) > corr_max:
                clash = (k["symbol"], rho)
                break
        if clash:
            dropped.append({"symbol": cand["symbol"], "kept_instead": clash[0],
                            "correlation": round(clash[1], 3) if clash[1] is not None and math.isfinite(clash[1]) else None,
                            "reason": "correlated or unverifiable",
                            "gate": cand["significance"].get("gate", "z"),
                            stat_key: cand["significance"][stat_key]})
        else:
            kept.append(cand)
    return kept, dropped


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier2", default=str(TIER2))
    ap.add_argument("--scan-id", default=None)
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--horizon", type=int, default=32,
                    help="bars allowed before a trade exits at market; 32 M15 bars = 8h, "
                         "which keeps the thesis intraday and compatible with "
                         "no-overnight-holds")
    # A hard session close, not just a horizon: 32 bars from a 16:45 entry runs
    # to 00:45 unless the session end is itself an exit. run_funnel passes the
    # end of its own --window-utc so the two cannot drift apart.
    ap.add_argument("--session-close", default="17:00", metavar="HH:MM",
                    help="UTC time at which an open test trade is closed (default 17:00)")
    ap.add_argument("--trials", type=int, default=None)
    ap.add_argument("--bars", type=int, default=15000,
                    help="closed bars of the selected timeframe for the deep test")
    ap.add_argument("--replays", type=int, default=10000)
    ap.add_argument("--permutations", type=int, default=PERMUTATIONS,
                    choices=[PERMUTATIONS],
                    help="locked at 1000: other values are rejected, never clamped")
    ap.add_argument("--corr-max", type=float, default=0.7)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--t-gate", action="store_true",
                    help="gate on the mean-R t instead of the win-rate z. p0=1/(1+rr) "
                         "needs a fixed target; the live exit policy books none, so the "
                         "binomial only measures who lost more slowly.")
    ap.add_argument("--donchian", type=int, default=20)
    # Must match tier2's --ema-fast/--ema-slow: the trend gate is part of the
    # rule being tested, so the backtest cannot use a different one.
    ap.add_argument("--ema-fast", type=int, default=50)
    ap.add_argument("--ema-slow", type=int, default=200)
    ap.add_argument("--atr-k", type=float, default=1.5)
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    src = json.loads(Path(a.tier2).read_text())
    top = src.get("top", [])
    scan_id = a.scan_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", scan_id):
        print("invalid --scan-id")
        return 1
    if a.scan_id is not None and src.get("scan_id") != a.scan_id:
        print("Tier 2 scan identity does not match --scan-id; refusing stale evidence")
        return 1
    # Two numbers that must never be conflated:
    #   scan_tests - hypotheses THIS scan tested; this is what the ledger adds
    #   trials     - cumulative hypotheses ever tested; this sets the gate
    # Logging the cumulative made trials_total sum it again on the next run, so
    # the bar doubled every scan until nothing could clear it.
    scan_tests = src.get("total_tests")
    if type(scan_tests) is not int or scan_tests < len(top):
        print("Tier 2 total_tests must be a nonnegative integer covering the shortlist")
        return 1
    try:
        import trials_total
        # One read for the chain check; the cumulative count is then read
        # fresh by required_trials, so a snapshot can never understate the bar.
        ledger = trials_total.read_lines()
        state, detail = trials_total.verify(lines=ledger)
        if state == "broken":
            print(f"ledger chain is broken ({detail}); refusing")
            return 1
        if state != "ok":
            print(f"ledger chain {state}: {detail}")
        minimum = trials_total.required_trials(scan_tests)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"cannot establish cumulative trials: {exc}")
        return 1
    if a.trials is not None and a.trials < minimum:
        print(f"--trials understates authoritative cumulative count ({minimum}); refusing")
        return 1
    trials = minimum if a.trials is None else a.trials
    zc = tm.bonferroni_z(trials)
    # With --t-gate the statistic is a Student-t, so its critical value is taken
    # from that distribution per candidate (df = n - 1) and recorded beside the
    # normal bar rather than silently replacing it.
    if not top:
        # No candidates is a normal outcome, not a failure. It must still be
        # recorded: the scan tested scan_tests hypotheses even though none
        # reached the deep pass, and dropping the row silently under-counts the
        # cumulative trials the Bonferroni gate is computed from -- which makes
        # the bar weaker than it claims to be. Exit 0 and log the row.
        print("Tier 2 produced no candidates.")
        print(STAND_DOWN)
        payload = {"generated_utc": md.now_utc(), "scan_id": scan_id,
                   **tm.source_provenance(),
                   "results": [], "passed_both_gates": [], "survivors": [],
                   "correlation_filtered": [], "corr_max": a.corr_max,
                   "stood_down": True, "trials_declared": scan_tests,
                   "trials_cumulative": trials, "z_crit_corrected": zc,
                   "permutations": a.permutations}
        # Ledger row first, artifact second. The reverse left a 349-byte
        # tier3_results.json claiming 90 trials and cumulative 9183 at
        # 2026-11-09T21:28:53Z with no matching ledger row: the artifact is the
        # claim, the ledger is the accounting, and the accounting has to win. A
        # row without an artifact is recoverable; an artifact without a row
        # silently understates every future bar.
        log_run(src, payload)
        OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return 0

    # These were two different numbers printed as one sentence: the cumulative
    # count went out as "declaring 8952 tests (62 symbols x 3 strategies)", and
    # 62 x 3 is 186. Anyone reading the log would think the accounting was
    # broken. Say which number is which.
    print(f"Tier 2 shortlist: {len(top)} | this scan declares {scan_tests} tests "
          f"({src.get('symbols_tested', '?')} symbols x {src.get('strategies_tested', '?')} "
          f"strategies) | cumulative {trials} | z_crit {zc:.2f}"
          + (" (t-gate: per-candidate df, shown per row)" if a.t_gate else "") + " | "
          f"permutations {a.permutations} | corr_max {a.corr_max}\n")

    md.connect()
    results = []
    for row in top:
        sym = row["symbol"]
        strat = row.get("strategy", "donchian")
        label = f"{sym}/{strat}"
        doc = tm.analyse(sym, a.tf, strat, a.donchian, a.ema_fast, a.ema_slow,
                         a.atr_k, a.rr, a.horizon,
                         # test the side Tier 2 actually proposes, not both
                         row.get("side", "both"),
                         a.bars, a.replays, a.seed, 2, 1500, trials,
                         a.permutations, a.session_close, a.t_gate)
        if doc is None:
            # Also returned when the present bar has no setup: no proposal is
            # the correct answer even if history is full of trades.
            print(f"{label}: no current setup — skipped")
            continue
        doc["tier2"] = {k: row.get(k) for k in ("class", "strategy", "side", "strength",
                                                "score", "spread_pct_atr")}
        results.append(doc)
        h, s, pm = doc["history"], doc["significance"], doc["permutation"] or {}
        ok = s["passes_corrected"] and pm.get("passes", False)
        print(f"{label:22} n={h['occurrences']:>4} win {h['win_rate']:6.1%} "
              f"{(s.get('gate') or 'z')} "
              f"{(s['t_stat_R'] if s.get('gate') == 't' else s['z']):+6.2f}/{zc:.2f} "
              f"p={pm.get('p_value', float('nan')):.4f} "
              f"avgR {h['avg_R']:+.3f} maxDD {h['max_drawdown_R']:+7.2f}R  "
              f"{'PASS' if ok else 'fail'}")

    # analyse() fetches, and fetching selects into Market Watch — release them.
    print(f"market watch released: {md.release_auto_selected()} symbols")

    # BOTH gates
    survivors = [d for d in results
                 if d["significance"]["passes_corrected"]
                 and (d["permutation"] or {}).get("passes")]
    stat_key = "t_stat_R" if a.t_gate else "z"
    survivors.sort(key=lambda d: d["significance"][stat_key], reverse=True)

    kept, dropped = (dedupe(survivors, a.corr_max, stat_key, a.tf)
                     if len(survivors) > 1 else (survivors, []))

    payload = {"generated_utc": md.now_utc(), "scan_id": scan_id,
               **tm.source_provenance(),
               "trials_declared": scan_tests,
               "trials_cumulative": trials,
               "z_crit_corrected": zc, "permutations": a.permutations,
               "t_crit_corrected": next((d["significance"].get("t_crit") for d in results
                                         if d["significance"].get("t_crit")), None),
               "corr_max": a.corr_max, "results": results,
               "passed_both_gates": [d["symbol"] for d in survivors],
               "correlation_filtered": dropped,
               "survivors": [d["symbol"] for d in kept],
               "stood_down": not kept}
    # See the no-candidates path: accounting before claim.
    log_run(src, payload)
    OUT.write_text(json.dumps(payload, indent=2) + "\n")

    # One readable page per scan, next to the machine-readable JSON. A scan that
    # produces only a log line is very hard to argue with six weeks later.
    try:
        import report
        print(f"report: {report.write_report(OUT)}")
    except Exception as exc:  # reporting must never break the scan
        print(f"report: skipped ({exc})")

    print()
    if not kept:
        for d in results:
            s, pm = d["significance"], d["permutation"] or {}
            tag = f"{d['symbol']}/{d.get('strategy')}"
            if s["passes_naive"] and not s["passes_corrected"]:
                print(f"NOTE: {tag} cleared a naive 1.96 but not {zc:.2f} "
                      f"— multiple-comparisons trap.")
            if s["passes_corrected"] and not pm.get("passes", False):
                print(f"NOTE: {tag} cleared the z gate but the permutation "
                      f"test says p={pm.get('p_value')} — not better than random entry.")
        print(STAND_DOWN)
        print(f"wrote {OUT.name}")
        return 2

    if dropped:
        print("=== correlation filter (abs(corr) > %.2f) ===" % a.corr_max)
        for d in dropped:
            print(f"  dropped {d['symbol']}: corr {d['correlation']} with "
                  f"{d['kept_instead']} (higher z)")
    print("=== SURVIVORS (cleared z AND permutation, de-correlated) ===")
    for n, d in enumerate(kept, 1):
        h, s, pm = d["history"], d["significance"], d["permutation"]
        print(f"{n}. {d['symbol']:12} z {s['z']:+.2f}  p {pm['p_value']:.4f}  "
              f"avgR {h['avg_R']:+.3f}  maxDD {h['max_drawdown_R']:+.2f}R  n={h['n_decided']}")
    print(f"wrote {OUT.name}")
    return 0


def main() -> int:
    if (HERE / ".ztt-maintenance").exists():
        print("maintenance marker present; refusing research")
        return 1
    lock = HERE / "funnel.lock"
    owned = not process_lock.parent_holds(lock)
    if owned and not process_lock.acquire(lock, 120 * 60):
        print("another research process holds funnel.lock")
        return 1
    try:
        return _main()
    finally:
        try:
            md.release_auto_selected()
        finally:
            if owned:
                process_lock.release(lock)


if __name__ == "__main__":
    raise SystemExit(main())
