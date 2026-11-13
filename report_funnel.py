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
import re
from datetime import datetime, timezone
from pathlib import Path

# history: the cron runner used to copy this script into ~/hermes/scripts, where
# __file__ pointed at the wrong directory. It runs in place now.
HERE = Path(__file__).resolve().parent
LOG = HERE / "funnel_history.log"
T3 = HERE / "tier3_results.json"
LOT = 0.01


def last_run() -> str | None:
    if not LOG.exists():
        return None
    with LOG.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - 16384))
        if size > 16384:
            stream.readline()
        lines = stream.read(16384).decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        if line.strip():
            return line.strip()
    return None


def why_failed(doc: dict) -> str:
    """Plain-language reason a candidate was refused."""
    s, pm = doc["significance"], doc.get("permutation") or {}
    gate = s.get("gate", "z")
    z = s.get("t_stat_R") if gate == "t" else s["z"]
    zc = s.get("t_crit") or s["z_crit_bonferroni"]
    if pm and not pm.get("passes", True) and s["passes_corrected"]:
        return (f"cleared the {zc:.2f} {gate} bar but the permutation test says "
                f"p={pm.get('p_value')} — not better than entering at random")
    if s["passes_naive"] and not s["passes_corrected"]:
        return (f"{gate}={z:+.2f} beats a naive 1.96 but not the corrected {zc:.2f} — "
                f"that is what scanning thousands of hypotheses produces by luck")
    if z <= -1.0:
        return f"{gate}={z:+.2f} is negative; positive net-return evidence is absent"
    return f"{gate}={z:+.2f} does not establish an edge against the {zc:.2f} bar"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    a = ap.parse_args()

    try:
        run = last_run()
    except OSError:
        print("MT5 funnel: ledger is unreadable; latest run unknown.")
        return 2
    if run is None:
        print("MT5 funnel: no runs logged yet.")
        return 2

    if "FAILED" in run:
        stamp = run.split("|")[0].strip()
        print(f"⚠ MT5 funnel FAILED at {stamp}: {run.split('|', 2)[-1].strip()[:200]}")
        return 2

    try:
        doc = json.loads(T3.read_text())
    except (OSError, ValueError) as e:
        print(f"⚠ MT5 funnel: tier3_results.json is unreadable: {e}")
        return 2
    match = re.search(r"\bscan_id=([A-Za-z0-9_-]+)", run)
    if not isinstance(doc, dict) or not match or doc.get("scan_id") != match.group(1):
        print("MT5 funnel: historical or mismatched stage evidence; current proposal unknown.")
        return 2

    # Describe the run the numbers actually came from. A standalone re-run of
    # tier3 overwrites this file, so pairing it with the log's stamp would
    # report one run's verdict using another run's evidence.
    stamp = doc.get("generated_utc", "?")
    stale = ""
    try:
        observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age_h = (datetime.now(timezone.utc) - observed).total_seconds() / 3600.0
        if age_h < -1 / 60:
            raise ValueError("future timestamp")
        if age_h > 3:
            stale = (f" [STALE: these results are {age_h:.0f}h old — the funnel has "
                     f"stopped running (check funnel_scheduler.out)]")
    except (ValueError, TypeError, AttributeError):
        print("MT5 funnel: result timestamp unverifiable; current proposal unknown.")
        return 2
    if stale:
        print(f"MT5 funnel {stamp}:{stale}; historical evidence only.")
        return 2

    results = doc.get("results", [])
    zc = doc.get("z_crit_corrected")
    trials = doc.get("trials_declared")
    cumulative = doc.get("trials_cumulative")

    if not doc.get("stood_down") and doc.get("survivors"):
        from report import verified_survivors
        if verified_survivors(doc) != doc["survivors"]:
            print("MT5 funnel: survivor contradicts gate/positive-return evidence; refusing proposal.")
            return 2
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
        print("   Exit policy protective trailing / break-even / session / horizon; no take-profit")
        print(f"   Risk        {c['risk']} per unit at {d['tier2'].get('class')} "
              f"spread {d['tier2'].get('spread_pct_atr')}% of ATR")
        ml = c.get("max_loss")
        if ml:
            print(f"   Max loss    {ml['amount']:.2f} {ml['currency']} at the stop "
                  f"({ml['lots']} lot, {ml['basis']}) — a stop is a plan, not a "
                  f"guarantee: a gap fills past it")
        else:
            print("   Max loss    unknown — this artifact carries no contract size")
        gate = s.get("gate", "z")
        metric = s.get("t_stat_R") if gate == "t" else s["z"]
        bar = s.get("t_crit") if gate == "t" else zc
        bar_txt = f"{bar:.2f}" if isinstance(bar, (int, float)) else "unknown"
        print(f"   Evidence    {gate}={metric:+.2f} vs corrected {bar_txt} | "
              f"perm p={pm.get('p_value')} | n={h['n_decided']} | "
              f"win {h['win_rate']:.1%} | maxDD {h['max_drawdown_R']:+.2f}R")
        print(f"   Trials      {trials} this scan; {cumulative} cumulative")
        print("   ACTION      Human APPROVED plus fresh independent bridge consent is required. "
              "This report cannot execute orders.")
        return 0

    if not results:
        print(f"MT5 funnel {stamp}: stood down before the deep tier — nothing triggered.")
        return 0

    def metric(row):
        stats = row["significance"]
        return stats.get("t_stat_R") if stats.get("gate") == "t" else stats["z"]
    best = max(results, key=metric)
    h = best["history"]
    print(f"MT5 funnel {stamp}: STAND DOWN — nothing cleared both gates "
          f"({len(results)} candidates deep-tested, {trials} scan trials, {cumulative} cumulative, "
          f"z bar {zc:.2f}).{stale}")
    print(f"Best candidate {best['symbol']}/{best['strategy']}: n={h['n_decided']}, "
          f"win {h['win_rate']:.1%}, mean net R={h.get('avg_R')}, "
          f"{why_failed(best)}.")
    if a.full:
        print("\nAll tested:")
        for r in sorted(results, key=lambda row: -metric(row)):
            gate = r["significance"].get("gate", "z")
            print(f"  {r['symbol']}/{r['strategy']:9} {gate} {metric(r):+6.2f}  "
                  f"p {(r['permutation'] or {}).get('p_value', float('nan')):.3f}  "
                  f"n {r['history']['n_decided']:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
