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
import desk_store as store

HERE = Path(__file__).resolve().parent


def feeds_from_config(root: Path) -> list[str]:
    """`news_feeds = [...]` from desk.toml.

    Deliberately not config.toml: that file is the MCP bridge's own policy, its
    schema forbids unknown sections, and `[agents]` there broke policy_check.py
    with extra_forbidden.
    """
    try:
        import tomllib
        data = tomllib.loads((root / "desk.toml").read_text(encoding="utf-8"))
        return [u for u in (data.get("news_feeds") or []) if isinstance(u, str)]
    except (OSError, ValueError):
        return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="AI desk: notes, never orders")
    ap.add_argument("--root", default=str(HERE))
    ap.add_argument("--offline", action="store_true", help="print the digest, call nothing")
    ap.add_argument("--feeds", default="", help="comma-separated RSS/Atom URLs")
    ap.add_argument("--model", default=None, help="override ZTT_AGENT_MODEL")
    ap.add_argument("--json", action="store_true", help="print the notes as JSON")
    ap.add_argument("--window-only", action="store_true",
                    help="do nothing outside 05:00-17:00 UTC, for the scheduled watcher")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve()
    if a.window_only:
        # The watcher is scheduled around the clock; the model bill is not. The
        # desk speaks during the same hours the funnel scans, and exits 0 quietly
        # outside them so a green task never means "I worked all night".
        from datetime import datetime, timezone
        import run_funnel
        if not run_funnel.in_window("05:00-17:00", datetime.now(timezone.utc)):
            print("desk: outside the scan window - nothing called")
            return 0
    if a.offline:
        print(desk.digest(root))
        return 0

    feeds = [u.strip() for u in a.feeds.split(",") if u.strip()] or feeds_from_config(root)
    notes = desk.run(root, feeds, a.model)
    path = desk.write_notes(root, notes)
    journal = desk.append_journal(root, notes)
    # The JSON above is the current state; the database is the record. Both are
    # written even when a role failed, because a failure is an answer too.
    store.record(root, notes)
    store.export(root)

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
    return 0 if notes["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
