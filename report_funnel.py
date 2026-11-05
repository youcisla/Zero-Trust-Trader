"""Daily funnel report — one line if we stood down, a full ticket if we won.

Reads the newest `funnel_history.log` line for the run verdict, then
`tier3_results.json` for the detail behind it. Stdout is the message (this is
meant to be driven by a no_agent cron job), so it stays short on purpose.

    python report_funnel.py
    python report_funnel.py --full      # include every candidate that was tested
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = Path(__file__).resolve().parent
# The cron runner copies this into ~/hermes/scripts, where __file__ points at the
# wrong directory — resolve the data next to the funnel instead.
if WORK.exists():
    HERE = WORK
LOG = HERE / "funnel_history.log"
T3 = HERE / "tier3_results.json"
LOT = 0.01


def last_run() -> str | None:
    if not LOG.exists():
        return None
    for line in reversed(LOG.read_text(encoding="utf-8").splitlines()):
        if line.strip():
            return line.strip()
    return None


def why_failed(doc: dict) -> str:
    """Plain-language reason a candidate was refused."""
    s, pm = doc["significance"], doc.get("permutation") or {}
    z, zc = s["z"], s["z_crit_bonferroni"]
    if pm and not pm.get("passes", True) and s["passes_corrected"]:
        return (f"cleared the {zc:.2f} z bar but the permutation test says "
                f"p={pm.get('p_value')} — not better than entering at random")
    if s["passes_naive"] and not s["passes_corrected"]:
        return (f"z={z:+.2f} beats a naive 1.96 but not the corrected {zc:.2f} — "
                f"that is what scanning thousands of hypotheses produces by luck")
    if z <= -1.0:
        return f"z={z:+.2f} is meaningfully negative — worse than a coin flip with these targets"
    return f"z={z:+.2f} is indistinguishable from noise against the {zc:.2f} bar"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()

    run = last_run()
    if run is None:
        print("MT5 funnel: no runs logged yet.")
        return 0

    if "FAILED" in run:
        stamp = run.split("|")[0].strip()
        print(f"⚠ MT5 funnel FAILED at {stamp}: {run.split('|', 2)[-1].strip()[:200]}")
        return 0

    try:
        doc = json.loads(T3.read_text())
    except Exception as e:
        print(f"⚠ MT5 funnel: tier3_results.json is unreadable: {e}")
        return 0

    # Describe the run the numbers actually came from. A standalone re-run of
    # tier3 overwrites this file, so pairing it with the log's stamp would
    # report one run's verdict using another run's evidence.
    stamp = doc.get("generated_utc", "?")
    stale = ""
    try:
        from datetime import datetime, timezone
        age_h = (datetime.now(timezone.utc)
                 - datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                 ).total_seconds() / 3600.0
        if age_h > 3:
            stale = (f" [STALE: these results are {age_h:.0f}h old — the funnel has "
                     f"stopped running (check funnel_scheduler.out)]")
    except Exception:
        pass

    results = doc.get("results", [])
    zc = doc.get("z_crit_corrected")
    trials = doc.get("trials_declared")

    if not doc.get("stood_down") and doc.get("survivors"):
        sym = doc["survivors"][0]
        d = next((r for r in results if r["symbol"] == sym), None)
        if d is None:
            print(f"⚠ Winner {sym} reported but absent from results — inspect manually.")
            return 0
        c, s, pm = d["current"], d["significance"], d["permutation"] or {}
        h = d["history"]
        print(f"✅ MT5 FUNNEL WINNER — {sym}/{d['strategy']} ({stamp}){stale}")
        print(f"   Side        {c['side'].upper()}")
        print(f"   Lot         {LOT}")
        print(f"   Entry       {c['proposed_entry']}")
        print(f"   Stop        {c['proposed_stop']}")
        print(f"   Target      {c['proposed_target']}")
        print(f"   Risk        {c['risk']} per unit at {d['tier2'].get('class')} "
              f"spread {d['tier2'].get('spread_pct_atr')}% of ATR")
        print(f"   Evidence    z={s['z']:+.2f} vs corrected {zc:.2f} | "
              f"perm p={pm.get('p_value')} | n={h['n_decided']} | "
              f"win {h['win_rate']:.1%} | maxDD {h['max_drawdown_R']:+.2f}R")
        print(f"   Trials      {trials} declared")
        print("   ACTION      AutoTrading is OFF. Reply APPROVED to execute, "
              "or DISCARD to skip. Nothing is placed without that word.")
        return 0

    if not results:
        print(f"MT5 funnel {stamp}: stood down before the deep tier — nothing triggered.")
        return 0

    best = max(results, key=lambda r: r["significance"]["z"])
    h = best["history"]
    print(f"MT5 funnel {stamp}: STAND DOWN — nothing cleared both gates "
          f"({len(results)} candidates deep-tested, {trials} trials declared, "
          f"z bar {zc:.2f}).{stale}")
    print(f"Best candidate {best['symbol']}/{best['strategy']}: n={h['n_decided']}, "
          f"win {h['win_rate']:.1%} vs break-even {best['significance']['break_even']:.1%}, "
          f"{why_failed(best)}.")
    if a.full:
        print("\nAll tested:")
        for r in sorted(results, key=lambda x: -x["significance"]["z"]):
            print(f"  {r['symbol']}/{r['strategy']:9} z {r['significance']['z']:+6.2f}  "
                  f"p {(r['permutation'] or {}).get('p_value', float('nan')):.3f}  "
                  f"n {r['history']['n_decided']:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
