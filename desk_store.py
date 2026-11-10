"""Every answer the desk ever gave, kept in one queryable file.

`agent_notes.json` is a snapshot: it holds what the agents said the last time
they ran, and the next run overwrites it. That is right for the dashboard and
useless for a review — "what did the judge say before the last three scans?"
had no answer short of reading `desk_journal.jsonl` line by line.

This module writes the same notes into `desk_history.sqlite`, one row per run
and one row per role, and exports the most recent runs to
`desk_history.json` so the Rust dashboard can show them without a SQLite
driver. The database is the record; the JSON is a view of it.

Nothing here can reach the terminal. It reads the dict that `desk.run()`
already returned and writes it down.

    python -m desk_store --history 20      # the last 20 runs, per role
    python -m desk_store --query "jury"    # every jury answer, oldest first
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB = "desk_history.sqlite"
EXPORT = "desk_history.json"
KEEP = 5000          # rows kept; git grows forever and so would this file
EXPORT_RUNS = 24     # runs mirrored into desk_history.json for the dashboard

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id              INTEGER PRIMARY KEY,
    generated_utc   TEXT NOT NULL UNIQUE,
    model           TEXT,
    facts_sha       TEXT,
    status          TEXT,
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    recorded_utc    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS answers (
    id          INTEGER PRIMARY KEY,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    status      TEXT,
    headline    TEXT,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS answers_run  ON answers(run_id);
CREATE INDEX IF NOT EXISTS answers_role ON answers(role, run_id);
"""

# The one secret this file could ever carry: the broker login, if an artifact
# ever echoed it. The database is committed, so the check runs before the write.
# Shapes that have actually appeared: a Windows drive path, an MSYS /c/Users path,
# a POSIX /Users path and a UNC share.
_PATH = re.compile(r"[A-Za-z]:\\[^\s\"']+|/[A-Za-z]/Users/[^\s\"']+|/Users/[^\s\"']+|\\\\[^\s\"']+")
# Any account-shaped 8-digit token. Over-redaction is the safe direction for a
# committed artifact: a price or a token count can be lost, a login cannot.
_LOGIN = re.compile(r"\b\d{8}\b")


def _redact(text: str, secrets: list[str]) -> str:
    """The file is committed: a login or a machine path must not survive this."""
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, "•••")
    text = _PATH.sub("[path]", text)
    text = _LOGIN.sub("[login]", text)
    return text


def _secrets(root: Path) -> list[str]:
    out = [os.environ.get("MT5_EXPECTED_LOGIN", "")]
    env = root / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("MT5_EXPECTED_LOGIN="):
                out.append(line.split("=", 1)[1].strip().strip('"').strip("'"))
    return [s for s in out if s]


def connect(root: Path) -> sqlite3.Connection:
    con = sqlite3.connect(root / DB)
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


def headline(role: str, note: dict) -> str:
    """One line that is the answer, whatever shape the role returned."""
    items = note.get("items")
    if isinstance(items, list) and items:
        mapped = sum(1 for i in items if isinstance(i, dict) and i.get("symbols"))
        return f"{len(items)} items, {mapped} mapped" if mapped else f"{len(items)} items"
    for key in ("verdict", "summary", "reason", "status"):
        value = note.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def record(root: Path, notes: dict) -> Path:
    """Write one run. Re-recording the same timestamp replaces nothing: the
    timestamp is unique, so a retry after a crash cannot duplicate a run."""
    secrets = _secrets(root)
    con = connect(root)
    try:
        with con:
            cur = con.execute(
                "INSERT OR IGNORE INTO runs (generated_utc, model, facts_sha, status,"
                " prompt_tokens, completion_tokens, recorded_utc) VALUES (?,?,?,?,?,?,?)",
                (notes.get("generated_utc") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 notes.get("model"), notes.get("facts_sha"), notes.get("status"),
                 (notes.get("usage") or {}).get("prompt_tokens"),
                 (notes.get("usage") or {}).get("completion_tokens"),
                 datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")))
            if cur.rowcount == 0:                     # this run is already stored
                return root / DB
            run_id = cur.lastrowid
            for role, note in (notes.get("roles") or {}).items():
                if not isinstance(note, dict):
                    continue
                payload = _redact(json.dumps(note, ensure_ascii=False, sort_keys=True), secrets)
                con.execute(
                    "INSERT INTO answers (run_id, role, status, headline, payload) VALUES (?,?,?,?,?)",
                    (run_id, role, note.get("status"), _redact(headline(role, note), secrets), payload))
            con.execute(
                "DELETE FROM runs WHERE id <= (SELECT MAX(id) - ? FROM runs)", (KEEP,))
    finally:
        con.close()
    return root / DB


def export(root: Path, runs: int = EXPORT_RUNS) -> Path:
    """The dashboard's view: the last `runs` runs, one object each."""
    rows = history(root, runs)
    out = {"generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "count": len(rows), "runs": rows}
    path = root / EXPORT
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def history(root: Path, limit: int = 24) -> list[dict]:
    path = root / DB
    if not path.exists():
        return []
    con = connect(root)
    try:
        runs = con.execute(
            "SELECT id, generated_utc, model, status, prompt_tokens, completion_tokens"
            " FROM runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for run_id, when, model, status, p_tok, c_tok in runs:
            roles = {}
            for role, r_status, h in con.execute(
                    "SELECT role, status, headline FROM answers WHERE run_id = ? ORDER BY id",
                    (run_id,)):
                roles[role] = {"status": r_status, "headline": h}
            out.append({"generated_utc": when, "model": model, "status": status,
                        "usage": {"prompt_tokens": p_tok, "completion_tokens": c_tok},
                        "roles": roles})
        return out
    finally:
        con.close()


def answers(root: Path, role: str, limit: int = 50) -> list[dict]:
    """Every stored answer for one role, newest first."""
    path = root / DB
    if not path.exists():
        return []
    con = connect(root)
    try:
        return [{"generated_utc": when, "run_id": run_id, "status": status,
                 "payload": json.loads(payload)}
                for when, run_id, status, payload in con.execute(
                    "SELECT r.generated_utc, a.run_id, a.status, a.payload"
                    " FROM answers a JOIN runs r ON r.id = a.run_id"
                    " WHERE a.role = ? ORDER BY a.id DESC LIMIT ?", (role, limit))]
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="what the agents said, over time")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent))
    ap.add_argument("--history", type=int, default=12, help="show the last N runs")
    ap.add_argument("--query", default="", help="show every answer of one role")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    root = Path(a.root).resolve()

    if a.query:
        rows = answers(root, a.query, a.history)
        if a.json:
            print(json.dumps(rows, indent=1, ensure_ascii=False))
            return 0 if rows else 1
        if not rows:
            print(f"no stored answers for role {a.query!r}")
            return 1
        for row in rows:
            print(f"{row['generated_utc']}  {row['status'] or 'ok':<10} "
                  f"{json.dumps(row['payload'], ensure_ascii=False)[:200]}")
        return 0

    rows = history(root, a.history)
    if a.json:
        print(json.dumps(rows, indent=1, ensure_ascii=False))
        return 0 if rows else 1
    if not rows:
        print("no runs recorded yet — run agent_brief.py")
        return 1
    for row in rows:
        cells = " | ".join(f"{role}:{(v.get('headline') or '')[:28]}"
                           for role, v in (row.get("roles") or {}).items())
        print(f"{row['generated_utc']}  {(row.get('status') or ''):<9} {cells}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
