#!/usr/bin/env python3
"""PreToolUse guard for the four AGENTS.md §4 rules a path rule cannot see.

`permissions.deny` in `.claude/settings.json` stops an Edit of the ledger. It
cannot stop `rm funnel_history.log`, and it cannot tell an edit that keeps the
frozen `LEGACY_TRIALS` from one that lowers it. This closes both gaps, and only
those: a pattern it does not know is allowed through, so a gap here is a missing
rule, never a blocked session.

Rules denied:
    LEGACY_TRIALS changed away from 2205          (§4.3, frozen history)
    PERMUTATIONS changed away from 1000           (§4.10, gate resolution)
    permutation alpha or t-gate alpha changed     (§4.2, significance)
    --apply added to a launcher                   (§8, dry run is the default)
    a protected runtime file deleted, moved,
    added to git, or wiped by `git clean`         (§4.4, §4.5)

Reads one JSON payload on stdin, writes one JSON decision on stdout, never
raises: an unparseable payload allows the call, because a broken guard must not
become a broken session.
"""
from __future__ import annotations

import json
import re
import sys

LEGACY_TRIALS = 2205
PERMUTATIONS = 1000
ALPHA = 0.05

# Runtime state AGENTS.md §4.4/§4.5 says is never deleted, rewritten or committed.
PROTECTED = (
    "funnel_history.log", "trades.csv", "liquid_universe.json", "stops.json",
    "engine_state", "audit.jsonl", "idempotency.db", "KILL_SWITCH", "config.toml",
)

DELETE_VERB = r"(?:rm|del|erase|unlink|mv|remove-item|move-item)\b"
LEADING = r"(?:^|[;&|\n]|\bthen\b)\s*(?:sudo\s+|&\s+)?"
CLEAN = re.compile(r"\bgit\s+clean\b", re.I)
GIT_ADD = re.compile(r"\bgit\s+add\b", re.I)
GIT_REVERT = re.compile(r"\bgit\s+(?:restore|checkout)\b", re.I)
DELETE = re.compile(LEADING + DELETE_VERB, re.I | re.M)

LEGACY_ASSIGN = re.compile(r"LEGACY_TRIALS\s*(?::\s*int\s*)?=\s*(\d+)")
PERM_ASSIGN = re.compile(r"^PERMUTATIONS\s*=\s*(\d+)", re.M)
ALPHA_T = re.compile(r"alpha:\s*float\s*=\s*([0-9.]+)")
ALPHA_P = re.compile(r"permutations\s*\+\s*1\)\s*<\s*([0-9.]+)")


def deny(reason: str) -> None:
    json.dump({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }}, sys.stdout)
    print()


def new_text(tool: str, data: dict) -> str:
    if tool == "Write":
        return str(data.get("content") or "")
    if tool == "Edit":
        return str(data.get("new_string") or "")
    parts = [str(e.get("new_string") or "") for e in data.get("edits") or []
             if isinstance(e, dict)]
    return "\n".join(parts)


def check_edit(path: str, text: str) -> str | None:
    name = path.replace("\\", "/").rsplit("/", 1)[-1].lower()

    if name == "trials_total.py":
        for value in LEGACY_ASSIGN.findall(text):
            if int(value) != LEGACY_TRIALS:
                return (f"LEGACY_TRIALS is frozen at {LEGACY_TRIALS} (AGENTS.md §4.3); "
                        f"this edit sets {value}. Lowering it makes every past result "
                        f"look more significant.")
    if name == "timemachine_mt5.py":
        for value in PERM_ASSIGN.findall(text):
            if int(value) != PERMUTATIONS:
                return (f"PERMUTATIONS is locked at {PERMUTATIONS} (AGENTS.md §4.10); "
                        f"this edit sets {value}. A gate whose resolution varies "
                        f"between runs is not a gate.")
        for value in ALPHA_T.findall(text):
            if abs(float(value) - ALPHA) > 1e-12:
                return (f"the t-gate alpha is {ALPHA} (AGENTS.md §4.2); this edit "
                        f"sets {value}.")
        for value in ALPHA_P.findall(text):
            if abs(float(value) - ALPHA) > 1e-12:
                return (f"the permutation threshold is p < {ALPHA} (AGENTS.md §4.2); "
                        f"this edit sets {value}.")
    if name.endswith((".cmd", ".bat")) and "--apply" in text:
        return ("--apply on a launcher is refused (AGENTS.md §8): the supervisor "
                "runs dry until a real position is open.")
    return None


def check_command(command: str) -> str | None:
    if CLEAN.search(command):
        return ("`git clean` deletes gitignored runtime state, including the "
                "ledger (AGENTS.md §4.4). Remove specific files instead.")
    hit = next((p for p in PROTECTED if p in command), None)
    if not hit:
        return None
    if DELETE.search(command):
        return f"{hit} is protected runtime state (AGENTS.md §4.4/§4.5); it is not deleted or moved."
    if GIT_ADD.search(command):
        return f"{hit} must never be committed (AGENTS.md §4.5); it carries the broker account number."
    if GIT_REVERT.search(command):
        return f"{hit} is append-only; a git restore/checkout would rewrite it (AGENTS.md §4.4)."
    return None


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (OSError, ValueError):
        return 0
    tool = str(event.get("tool_name") or "")
    data = event.get("tool_input") or {}
    if not isinstance(data, dict):
        return 0
    if tool in ("Edit", "Write", "MultiEdit"):
        reason = check_edit(str(data.get("file_path") or ""), new_text(tool, data))
    elif tool in ("Bash", "PowerShell"):
        reason = check_command(str(data.get("command") or ""))
    else:
        return 0
    if reason:
        deny(reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
