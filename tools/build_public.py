"""Build a sanitized publication artifact from tracked source, never runtime state."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.check_public import ROOT, private_identifier, scan_files, tracked_files

REQUIRED = {
    "AGENTS.md", "README.md", "LICENSE", "requirements.txt", "run_funnel.py",
    "position_manager.py", "marketdata.py", "trials_total.py", "tier2_patterns.py",
    "process_lock.py",
    "tier3_deep.py", "timemachine_mt5.py", "desk.py", "report.py", "stops.py", "audit.py",
    "tools/check_public.py", "tools/run_tests.py", "tools/build_public.py",
    "dashboard/Cargo.toml", "dashboard/Cargo.lock", "dashboard/src/main.rs",
    "dashboard/web/index.html", "dashboard/web/architecture.html",
    "dashboard/web/vendor/react.production.min.js", "tests/_mt5stub.py",
}


def build(root: Path, destination: Path) -> dict:
    names = tracked_files(root)
    missing = REQUIRED.difference(names)
    if missing:
        raise ValueError("required tracked sources missing: " + ", ".join(sorted(missing)))
    refusals = scan_files(root, names,
                          private_identifier(root, os.environ.get("ZTT_PRIVATE_IDENTIFIER", "")))
    if refusals:
        raise ValueError("privacy refusal: " + ", ".join(f"{name} [{rule}]" for name, rule in refusals))
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("destination must be empty; existing publication content is never deleted")
    destination.mkdir(parents=True, exist_ok=True)
    hashes = {}
    for name in names:
        source = root / name
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        raw = source.read_bytes()
        if name.endswith(".py"):
            tree = ast.parse(raw, filename=name)
            for node in ast.walk(tree):
                modules = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                           else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                for module in modules:
                    if not module:
                        continue
                    parts = module.split(".")
                    # `from tools.check_public import ROOT` used to be reduced to
                    # "tools" and looked up as tools.py, so an untracked helper
                    # inside a tracked package passed validation.
                    candidates = (Path(*parts).with_suffix(".py"), Path(*parts) / "__init__.py",
                                  Path(parts[0]).with_suffix(".py"))
                    for cand in candidates:
                        if (root / cand).is_file() and cand.as_posix() not in names:
                            raise ValueError(
                                f"local import missing from tracked source: {name} -> {cand.as_posix()}")
        target.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, check=True).stdout.strip()
    manifest = {"source_revision": revision, "files_sha256": hashes}
    (destination / "PUBLIC-MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, help="new or empty destination; never overwritten")
    parser.add_argument("--check", action="store_true", help="build and test only in a temporary directory")
    args = parser.parse_args(argv)
    if args.check == bool(args.out):
        parser.error("choose exactly one of --check or --out")
    try:
        if args.check:
            with tempfile.TemporaryDirectory(prefix="ztt-public-check-") as directory:
                destination = Path(directory)
                manifest = build(ROOT, destination)
                result = subprocess.run([sys.executable, "tools/run_tests.py"], cwd=destination,
                                        capture_output=True, text=True, timeout=300)
                if result.returncode:
                    print("publication regression suite failed; no artifact promoted", file=sys.stderr)
                    return 1
        else:
            destination = args.out.resolve()
            if destination == ROOT or ROOT.is_relative_to(destination):
                raise ValueError("destination cannot contain the execution tree")
            manifest = build(ROOT, destination)
        print(f"publication source validated: {len(manifest['files_sha256'])} files; no runtime state copied")
        return 0
    except (OSError, subprocess.SubprocessError) as error:
        print(f"publication refused: source read/check failed ({type(error).__name__})", file=sys.stderr)
        return 2
    except (ValueError, SyntaxError) as error:
        print(f"publication refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
