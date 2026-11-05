"""TIER 3 — deep Time Machine on the Tier 2 shortlist, two independent gates.

A candidate is only a survivor if it clears BOTH:

  1. the Bonferroni-corrected z for the size of the scan that produced it
     (analytic; assumes independence across tests, which is generous), and
  2. the random-entry permutation test (resampling; keeps the instrument's own
     volatility clustering and barrier geometry, so it absorbs the correlation
     between candidates that the analytic z cannot see).

Survivors are then de-duplicated by correlation: two instruments above --corr-max
are essentially one trade, and holding both doubles risk without doubling signal.
The higher z wins; the other is dropped with a note.

    python tier3_deep.py
    python tier3_deep.py --permutations 1000 --corr-max 0.7
"""

from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

import marketdata as md
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
            f"permutations={payload['permutations']} | {verdict}")
    with (HERE / "funnel_history.log").open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def correlation(sym_a: str, sym_b: str, bars_n: int = 3000, tf: str = "H1") -> float | None:
    """Delegates to the shared implementation so Tier 2 and Tier 3 cannot drift."""
    return md.correlation(sym_a, sym_b, bars_n, tf)


def dedupe(survivors: list[dict], corr_max: float) -> tuple[list[dict], list[dict]]:
    """Greedy: keep by descending z, drop anything too correlated with a keeper."""
    kept, dropped = [], []
    for cand in survivors:
        clash = None
        for k in kept:
            rho = correlation(cand["symbol"], k["symbol"])
            if rho is not None and abs(rho) > corr_max:
                clash = (k["symbol"], rho)
                break
        if clash:
            dropped.append({"symbol": cand["symbol"], "kept_instead": clash[0],
                            "correlation": round(clash[1], 3),
                            "z": cand["significance"]["z"]})
        else:
            kept.append(cand)
    return kept, dropped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier2", default=str(TIER2))
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--horizon", type=int, default=32,
                    help="bars allowed before a trade exits at market; 32 M15 bars = 8h, "
                         "which keeps the thesis intraday and compatible with "
                         "no-overnight-holds")
    ap.add_argument("--trials", type=int, default=None)
    ap.add_argument("--bars", type=int, default=15000,
                    help="H1 bars of history for the deep test (G1 needs n>=300 trades)")
    ap.add_argument("--replays", type=int, default=10000)
    ap.add_argument("--permutations", type=int, default=1000)
    ap.add_argument("--corr-max", type=float, default=0.7)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--donchian", type=int, default=20)
    ap.add_argument("--atr-k", type=float, default=1.5)
    ap.add_argument("--rr", type=float, default=1.5)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    src = json.loads(Path(a.tier2).read_text())
    top = src.get("top", [])
    if not top:
        print("Tier 2 produced no candidates.")
        print(STAND_DOWN)
        return 1

    trials = a.trials or max(
        int(src.get("total_tests") or (src.get("symbols_tested", 1)
                                       * len(src.get("strategies", ["donchian"])))),
        len(top))
    zc = tm.bonferroni_z(trials)
    print(f"Tier 2 shortlist: {len(top)} | declaring {trials} tests "
          f"({src.get('symbols_tested', '?')} symbols x {src.get('strategies_tested', '?')} "
          f"strategies) | z_crit {zc:.2f} | permutations {a.permutations} | "
          f"corr_max {a.corr_max}\n")

    md.connect()
    results = []
    for row in top:
        sym = row["symbol"]
        strat = row.get("strategy", "donchian")
        label = f"{sym}/{strat}"
        doc = tm.analyse(sym, a.tf, strat, a.donchian, 20, 50, a.atr_k, a.rr, a.horizon,
                         "both", a.bars, a.replays, a.seed, 2, 1500, trials,
                         a.permutations)
        if doc is None:
            print(f"{label}: no setups fired — skipped")
            continue
        doc["tier2"] = {k: row.get(k) for k in ("class", "strategy", "side", "strength",
                                                "score", "spread_pct_atr")}
        results.append(doc)
        h, s, pm = doc["history"], doc["significance"], doc["permutation"] or {}
        ok = s["passes_corrected"] and pm.get("passes", False)
        print(f"{label:22} n={h['occurrences']:>4} win {h['win_rate']:6.1%} "
              f"z {s['z']:+6.2f}/{zc:.2f} p={pm.get('p_value', float('nan')):.4f} "
              f"avgR {h['avg_R']:+.3f} maxDD {h['max_drawdown_R']:+7.2f}R  "
              f"{'PASS' if ok else 'fail'}")

    # analyse() fetches, and fetching selects into Market Watch — release them.
    print(f"market watch released: {md.release_auto_selected()} symbols")

    # BOTH gates
    survivors = [d for d in results
                 if d["significance"]["passes_corrected"]
                 and (d["permutation"] or {}).get("passes")]
    survivors.sort(key=lambda d: d["significance"]["z"], reverse=True)

    kept, dropped = dedupe(survivors, a.corr_max) if len(survivors) > 1 else (survivors, [])

    payload = {"generated_utc": md.now_utc(), "trials_declared": trials,
               "z_crit_corrected": zc, "permutations": a.permutations,
               "corr_max": a.corr_max, "results": results,
               "passed_both_gates": [d["symbol"] for d in survivors],
               "correlation_filtered": dropped,
               "survivors": [d["symbol"] for d in kept],
               "stood_down": not kept}
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    log_run(src, payload)

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
            print(f"  dropped {d['symbol']}: corr {d['correlation']:+.2f} with "
                  f"{d['kept_instead']} (higher z)")
    print("=== SURVIVORS (cleared z AND permutation, de-correlated) ===")
    for n, d in enumerate(kept, 1):
        h, s, pm = d["history"], d["significance"], d["permutation"]
        print(f"{n}. {d['symbol']:12} z {s['z']:+.2f}  p {pm['p_value']:.4f}  "
              f"avgR {h['avg_R']:+.3f}  maxDD {h['max_drawdown_R']:+.2f}R  n={h['n_decided']}")
    print(f"wrote {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
