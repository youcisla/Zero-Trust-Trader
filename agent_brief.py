"""Run the desk and write `agent_notes.json`.

    python agent_brief.py                 # calls the model, writes notes
    python agent_brief.py --offline       # prints the fact sheet, calls nothing
    python agent_brief.py --feeds a,b     # RSS feeds the scout may read

Read-only with respect to the trading system: it opens artifacts, calls an HTTP
API, writes one derived JSON file and one audit row. It cannot place, size or
cancel anything — there is no code path to the terminal in this file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import audit
import desk

HERE = Path(__file__).resolve().parent


def feeds_from_config(root: Path) -> list[str]:
    """`[agents] news_feeds = ["url", ...]` in config.toml, if it is there."""
    cfg = root / "config.toml"
    try:
        import tomllib
        data = tomllib.loads(cfg.read_text(encoding="utf-8"))
        return [u for u in (data.get("agents", {}).get("news_feeds") or []) if isinstance(u, str)]
    except (OSError, ValueError):
        return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI desk: notes, never orders")
    ap.add_argument("--root", default=str(HERE))
    ap.add_argument("--offline", action="store_true", help="print the digest, call nothing")
    ap.add_argument("--feeds", default="", help="comma-separated RSS/Atom URLs")
    ap.add_argument("--model", default=None, help="override ZTT_AGENT_MODEL")
    ap.add_argument("--json", action="store_true", help="print the notes as JSON")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve()
    if a.offline:
        print(desk.digest(root))
        return 0

    feeds = [u.strip() for u in a.feeds.split(",") if u.strip()] or feeds_from_config(root)
    notes = desk.run(root, feeds, a.model)
    path = desk.write_notes(root, notes)
    journal = desk.append_journal(root, notes)

    if a.json:
        print(json.dumps(notes, indent=1))
    else:
        print(f"desk: {notes['status']} · {notes['model']} · {path.name} (+ {journal.name})")
        for role, note in notes["roles"].items():
            state = note.get("status", "ok")
            line = note.get("summary") or note.get("verdict") or note.get("reason") or ""
            print(f"  {role:<8} {state:<12} {str(line)[:110]}")

    audit.write("agent_brief", status=notes["status"], model=notes["model"],
                facts_sha=notes["facts_sha"], feeds=len(feeds))

    # A missing key is an operator error, not a quiet no-op: exit non-zero so a
    # scheduled task shows a failure instead of a green run that did nothing.
    return 2 if notes["status"] == "no key" else 0


if __name__ == "__main__":
    sys.exit(main())
