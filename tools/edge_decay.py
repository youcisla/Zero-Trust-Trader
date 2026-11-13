#!/usr/bin/env python3
"""Per-candidate trajectories from the append-only metrics history.

`tools/record_candidate_metrics.py` keeps what `tier3_results.json` forgets.
This report reads it: how often a candidate was seen, its z/t and win-rate
trajectory, and whether it is decaying — a corrected pass that no longer
passes, or a win rate that fell at every one of the last few snapshots.

Advisory only: it never gates, sizes or trades. An empty history prints what
to run next and exits 0 — a monitor with nothing to measure says so.

    python tools/edge_decay.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HISTORY = ROOT / "candidate_metrics.jsonl"


def load(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except ValueError:
            print(f"warning: {path.name} has an unreadable line; ignored", file=sys.stderr)
    return rows


def trails(rows: list[dict]) -> dict[str, list[dict]]:
    """One ordered snapshot per candidate, keyed `symbol/strategy`."""
    out: dict[str, list[dict]] = {}
    for row in rows:
        for cand in row.get("candidates") or []:
            name = f"{cand.get('symbol')}/{cand.get('strategy')}"
            out.setdefault(name, []).append(
                {"scan_id": row.get("scan_id"), "seen": row.get("generated_utc"), **cand})
    return out


def decay_reasons(trail: list[dict], window: int = 5) -> list[str]:
    """Why this candidate looks like it is decaying. Empty list = none seen.

    Two literal signals, no model: a corrected pass that a later snapshot no
    longer shows, and a win rate lower at every one of the last `window` rows.
    """
    reasons = []
    passed = [t for t in trail if t.get("passes_corrected")]
    if passed and not trail[-1].get("passes_corrected"):
        reasons.append(f"gate: passed {passed[0]['seen']}, fails {trail[-1]['seen']}")
    rates = [t.get("win_rate") for t in trail[-window:]]
    rates = [r for r in rates if r is not None]
    if len(rates) >= 2 and all(a > b for a, b in zip(rates, rates[1:])):
        reasons.append(f"win-rate falling over last {len(rates)} snapshots "
                       f"({rates[0]:.1%} → {rates[-1]:.1%})")
    return reasons


def report(rows: list[dict]) -> list[str]:
    if not rows:
        return ["no history yet — run tools/record_candidate_metrics.py after a scan"]
    lines = [f"scans recorded: {len(rows)}  "
             f"{rows[0].get('generated_utc')} → {rows[-1].get('generated_utc')}"]
    if len(rows) >= 2:
        passes = sum(len(r.get("passed_both") or []) for r in rows)
        lines.append(f"candidates seen: {len(trails(rows))}  passes (both gates): {passes}")
    for name, trail in sorted(trails(rows).items()):
        last = trail[-1]
        stat = last.get("stat")
        rate = last.get("win_rate")
        lines.append(
            f"{name:28} seen {len(trail):>2}  "
            f"{trail[0].get('seen')} → {last.get('seen')}  "
            f"{last.get('gate')} {'n/a' if stat is None else f'{stat:+.2f}'}  "
            f"win {'n/a' if rate is None else f'{rate:.1%}'}  "
            f"passes {sum(1 for t in trail if t.get('passes_corrected'))}")
        for reason in decay_reasons(trail):
            lines.append(f"    decay? {reason}")
    lines.append("")
    lines.append("scan trend (shortlist → passed both gates):")
    for row in rows[-20:]:
        lines.append(f"  {row.get('generated_utc')}  {row.get('shortlist', 0)} → "
                     f"{len(row.get('passed_both') or [])}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--history", type=Path, default=HISTORY)
    args = ap.parse_args(argv)
    print("\n".join(report(load(args.history))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
