"""What the desk said, how often, and what it cost.

The desk writes every run to `desk_history.sqlite` and nothing has ever read it
back as a whole. This prints the three questions an operator actually asks:

  1. what did the jury decide, and how often,
  2. how often did a role's note survive the guard, and
  3. what does the desk cost per day.

It is a report, not a gate: exit 0 whenever it could read the database, and the
only failure is a missing or empty one. `docs/PRIOR-ART.md` records why this
exists — a decision log that is never settled is a log nobody can act on.

    python tools/desk_scorecard.py [--root DIR] [--limit N] [--json]

Read-only: it opens the SQLite file with `mode=ro` and never writes.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _rows(con: sqlite3.Connection, sql: str) -> list[tuple]:
    return con.execute(sql).fetchall()


def _verdicts(con: sqlite3.Connection) -> list[dict]:
    out = []
    for run_id, payload in _rows(
            con, "select run_id, payload from answers where role='jury' and status='ok' order by id"):
        try:
            doc = json.loads(payload)
        except (TypeError, ValueError):
            continue
        out.append({"run": run_id, "verdict": doc.get("verdict"), "why": doc.get("why") or ""})
    return out


def report(root: Path, limit: int = 10) -> dict:
    db = root / "desk_history.sqlite"
    if not db.exists():
        raise SystemExit(f"no desk history at {db} - run agent_brief.py first")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        runs, first, last, tokens_in, tokens_out = con.execute(
            "select count(*), min(generated_utc), max(generated_utc), "
            "coalesce(sum(prompt_tokens), 0), coalesce(sum(completion_tokens), 0) from runs").fetchone()
        if not runs:
            raise SystemExit("desk history is empty")
        roles = {}
        for role, status, n in _rows(con, "select role, status, count(*) from answers group by role, status"):
            roles.setdefault(role, {})[status] = n
        verdicts = _verdicts(con)
        day = {}
        for r in _rows(con, "select substr(generated_utc, 1, 10), count(*) from runs group by 1"):
            day[r[0]] = r[1]
    finally:
        con.close()

    counts = {}
    for v in verdicts:
        counts[v["verdict"] or "unknown"] = counts.get(v["verdict"] or "unknown", 0) + 1
    # A contradiction claim is the desk's expensive failure mode: it sends a human
    # to reconcile two numbers. Counting them is the point of this report.
    flagged = [v for v in verdicts if v["verdict"] == "escalate"]
    return {
        "runs": runs, "first": first, "last": last, "runs_per_day": day,
        "roles": roles, "verdicts": counts, "escalations": flagged[-limit:],
        "escalations_total": len(flagged), "notes": len(verdicts),
        "tokens": {"in": tokens_in, "out": tokens_out},
        "tokens_per_run": {"in": round(tokens_in / runs), "out": round(tokens_out / runs)},
        "per_day_at_current_cadence": round((tokens_in + tokens_out) / max(1, runs) * 48),
    }


def render(doc: dict) -> str:
    lines = [f"DESK SCORECARD   {doc['runs']} runs, {doc['first']} .. {doc['last']}", ""]
    lines.append(f"{'role':9s}{'ok':>5s}{'rejected':>10s}{'error':>7s}   other")
    for role in ("scout", "skeptic", "chief", "jury"):
        s = doc["roles"].get(role, {})
        other = ", ".join(f"{k} {v}" for k, v in s.items() if k not in ("ok", "rejected", "error")) or "-"
        lines.append(f"{role:9s}{s.get('ok', 0):>5d}{s.get('rejected', 0):>10d}"
                     f"{s.get('error', 0):>7d}   {other}")
    total_ok = sum(s.get("ok", 0) for s in doc["roles"].values())
    total_bad = sum(s.get("rejected", 0) for s in doc["roles"].values())
    lines.append(f"{'':9s}{total_ok:>5d}{total_bad:>10d}"
                 f"{sum(s.get('error', 0) for s in doc['roles'].values()):>7d}   "
                 f"notes that survived the guard: {total_ok}/{total_ok + total_bad}")
    lines += ["", "jury verdicts  " + "   ".join(f"{k} {v}" for k, v in sorted(doc["verdicts"].items()))]
    lines.append(f"escalations    {doc['escalations_total']} of {doc['notes']} judged runs")
    # desk_store keeps ONE call's usage, not the run's total, and the journal keeps
    # none - so summing these columns would publish a number that means something
    # else. The desk card already shows the run total from role_metadata.
    lines.append("cost           per-role and run totals are on the desk card; "
                 "this file stores one call's usage and is not summed here")
    if doc["escalations"]:
        lines += ["", f"last {len(doc['escalations'])} escalations - read the reasons, they are the product"]
        for e in doc["escalations"]:
            why = " ".join(e["why"].split())
            lines.append(f"  run {e['run']:>4}  {why[:220]}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    doc = report(a.root, a.limit)
    print(json.dumps(doc, indent=1) if a.json else render(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
