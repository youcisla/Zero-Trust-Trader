#!/usr/bin/env python3
"""Publish the desk's answers as text, so the database can stay private.

`desk_history.sqlite` is runtime state: it grows on every 15-minute desk run, it
carries third-party headline text verbatim, and two machines editing it produce a
binary merge conflict every time. It was tracked until this export existed — 14
re-commits of a file whose only job is to be a local record.

What ships instead is a **view**: the last N runs, with the role, the status, the
headline and the token counts. No raw payloads, no prompts, no RSS bodies, and
every string passes through `desk_store._redact` before it is written.

    python tools/export_desk_history.py            # -> docs/desk-history.jsonl
    python tools/export_desk_history.py --runs 20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))

import desk_store  # noqa: E402

OUT = HERE / "docs" / "desk-history.jsonl"


def rows(root: Path, runs: int) -> list[dict]:
    secrets = desk_store._secrets(root)
    out = []
    for run in desk_store.history(root, runs):
        roles = {}
        for role, body in (run.get("roles") or {}).items():
            roles[role] = {
                "status": desk_store._redact(str(body.get("status") or ""), secrets),
                "headline": desk_store._redact(str(body.get("headline") or ""), secrets),
            }
        out.append({
            "generated_utc": run.get("generated_utc"),
            "model": run.get("model"),
            "status": run.get("status"),
            "usage": run.get("usage") or {},
            "roles": roles,
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=str(HERE))
    ap.add_argument("--runs", type=int, default=100)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args(argv)

    root = Path(a.root).resolve()
    data = rows(root, a.runs)
    if not data:
        print("no desk runs stored — nothing exported (a missing record is not an empty one)")
        return 1
    target = Path(a.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data)
    target.write_text(body, encoding="utf-8")
    print(f"exported {len(data)} runs -> {target.name} ({len(body.encode())} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
