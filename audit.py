"""Append-only audit trail: one JSON object per line, never rewritten.

Every script that can decide or act writes here, so the dashboard and a human
reading `audit.jsonl` see the same story: what ran, what it decided, what it
refused, and why. The trials ledger stays a test ledger; this is the record of
events that are not trials.

    python audit.py            # print the last 20 records

Write with audit.write("verdict", run="05:00", verdict="STAND_DOWN").
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIT = HERE / "audit.jsonl"


def write(event: str, **fields) -> None:
    """Never raises: auditing must not be able to break what it audits."""
    record = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "event": event,
        **fields,
    }
    line = json.dumps(record, separators=(",", ":"), default=str)
    try:
        with AUDIT.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError as exc:  # pragma: no cover - disk full / permission
        print(f"audit write failed: {exc}", file=sys.stderr)


def tail(n: int = 20) -> list[dict]:
    if not AUDIT.exists():
        return []
    out = []
    for line in AUDIT.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"raw": line})
    return out


if __name__ == "__main__":
    for rec in tail(int(sys.argv[1]) if len(sys.argv) > 1 else 20):
        print(json.dumps(rec, indent=2))
