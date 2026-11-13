#!/usr/bin/env python3
"""Snapshot one scan's per-candidate metrics into an append-only history.

`tier3_results.json` is overwritten every scan, so a candidate's trajectory
exists only in this file. The recorder is the safe seam: it reads an artifact
the engine already writes and appends one line to `candidate_metrics.jsonl`
(gitignored runtime). Idempotent by `scan_id`. Advisory only: it never gates,
sizes or trades, and the engine never learns it exists.

    python tools/record_candidate_metrics.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "tier3_results.json"
OUT = ROOT / "candidate_metrics.jsonl"


def snapshot(payload: dict) -> dict:
    """The one scan row as it lands in the history."""
    candidates = []
    for doc in payload.get("results") or []:
        sig = doc.get("significance") or {}
        perm = doc.get("permutation") or {}
        hist = doc.get("history") or {}
        gate = sig.get("gate") or "z"
        candidates.append({
            "symbol": doc.get("symbol"),
            "strategy": doc.get("strategy"),
            "gate": gate,
            "stat": sig.get("t_stat_R") if gate == "t" else sig.get("z"),
            "win_rate": hist.get("win_rate"),
            "occurrences": hist.get("occurrences"),
            "passes_corrected": bool(sig.get("passes_corrected")),
            "permutation_passes": bool(perm.get("passes")),
        })
    return {
        "scan_id": payload.get("scan_id"),
        "generated_utc": payload.get("generated_utc"),
        "stood_down": bool(payload.get("stood_down")),
        "shortlist": len(candidates),
        "passed_both": list(payload.get("passed_both_gates") or []),
        "trials_cumulative": payload.get("trials_cumulative"),
        "z_crit_corrected": payload.get("z_crit_corrected"),
        "candidates": candidates,
    }


def recorded_ids(path: Path) -> set[str]:
    ids: set[str] = set()
    if not path.exists():
        return ids
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            ids.add(json.loads(line).get("scan_id"))
        except ValueError:
            print(f"warning: {path.name} has an unreadable line; ignored", file=sys.stderr)
    return ids


def record(results: Path = RESULTS, out: Path = OUT) -> int:
    if not results.exists():
        print(f"no {results.name} — nothing to record (run a scan first)")
        return 0
    payload = json.loads(results.read_text(encoding="utf-8"))
    scan_id = payload.get("scan_id")
    if not scan_id:
        print(f"{results.name} has no scan_id — refusing to record an anonymous scan")
        return 1
    if scan_id in recorded_ids(out):
        print(f"scan {scan_id} already recorded — nothing to do")
        return 0
    row = snapshot(payload)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")
    print(f"recorded {scan_id}: {row['shortlist']} candidate(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results", type=Path, default=RESULTS)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    return record(args.results, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
