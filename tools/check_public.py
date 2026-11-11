"""Check publishable source; report rules/filenames without matched secrets."""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_NAMES = (
    "engine_state*.json*", "engine_state*.tmp", "idempotency.db*", "*.log", "*.out",
    "trades.csv", "stops.json", "stops.json.*", "liquid_universe.json", "audit.jsonl",
    ".stops.*.tmp", "funnel.lock", "position_manager.lock",
    "agent_notes.json", "desk_journal.jsonl", "tier1_universe*.json",
    "tier2_candidates.json", "tier3_results.json", "config.toml", "*.local.cmd",
    ".ztt-maintenance",
)
PATTERNS = {
    "machine-path": re.compile(rb"[A-Za-z]:[/\\](?:Users|Documents and Settings)[/\\][^/\\\s<>]+", re.I),
    "unix-home": re.compile(rb"/(?:home|Users)/[A-Za-z0-9_.-]+/"),
    "credential-token": re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9_-]{20,})\b"),
    "private-key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def private_path(name: str) -> bool:
    path = Path(name)
    return (
        any(part in {".git", ".venv", "__pycache__", "target", "_publish", ".archify", "exports", "reports"} for part in path.parts)
        or (path.name.startswith(".env") and path.name != ".env.example")
        or any(fnmatch.fnmatchcase(path.name, pattern) for pattern in PRIVATE_NAMES)
    )


def tracked_files(root: Path) -> list[str]:
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
    return [name.decode("utf-8") for name in result.stdout.split(b"\0") if name]


def scan_files(root: Path, names: list[str], identifier: str = "") -> list[tuple[str, str]]:
    failures = []
    root = root.resolve()
    for name in names:
        path = root / name
        if private_path(name):
            failures.append((name, "private-runtime-path"))
            continue
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            failures.append((name, "unsafe-source-path"))
            continue
        raw = path.read_bytes()
        for rule, pattern in PATTERNS.items():
            if pattern.search(raw):
                failures.append((name, rule))
        if identifier and identifier.encode() in raw:
            failures.append((name, "private-identifier"))
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    identifier = os.environ.get("ZTT_PRIVATE_IDENTIFIER", "")
    # A local run can read the one identifier this tree actually protects. CI cannot
    # (no .env), which is why ZTT_PRIVATE_IDENTIFIER exists there - but the machine
    # that commits is the machine where the slip happens, and an eight-digit login
    # is exactly what a fixture copies by accident.
    local_env = args.root / ".env"
    if local_env.exists() and not identifier:
        for line in local_env.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*MT5_EXPECTED_LOGIN\s*=\s*(\d{6,})", line)
            if m:
                identifier = m.group(1)
                break
    expects = os.environ.get("ZTT_EXPECT_IDENTIFIER") == "1"
    # GitHub renders a secret as an empty string for a pull request from a fork, and
    # an unset secret looks identical. The workflow therefore states its expectation
    # explicitly (`ZTT_EXPECT_IDENTIFIER`), so this script can tell "this repository
    # has no identifier to check" from "the identifier it needs did not arrive".
    # Either way it says so out loud: the silent version is what the first audit
    # found, and a scan that quietly loses a rule is worse than one that refuses.
    if expects and not identifier:
        print("ZTT_PRIVATE_IDENTIFIER is not configured; refusing to run a weakened scan",
              file=sys.stderr)
        return 2
    if not identifier:
        print("note: no private identifier configured — only the generic patterns ran",
              file=sys.stderr)
    try:
        failures = scan_files(args.root, tracked_files(args.root), identifier)
    except (OSError, ValueError, subprocess.CalledProcessError):
        print("privacy scan failed to read the complete tracked inventory", file=sys.stderr)
        return 2
    for name, rule in failures:
        print(f"privacy refusal: {name} [{rule}]", file=sys.stderr)
    if not failures:
        print("tracked-source privacy check passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
