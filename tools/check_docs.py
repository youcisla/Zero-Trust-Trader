#!/usr/bin/env python3
"""The documents that describe this tree must not disagree with it.

`AGENTS.md` §2b says a change is not finished while the README, the architecture
map or the mémoire still describe the tree as it was. Prose cannot enforce that.
These assertions can, and CI runs the ones that do not need runtime state.

The architecture map is the one that rots quietly: it is regenerated from a JSON
source by the Archify skill, its numbers are typed by hand into tags, and nothing
reads it but a human. It said `1,723 -> 120` for a day after the universe became
248. That is the failure this file exists to prevent.

Exit 0 when everything agrees, 1 when something does not. Numbers that come from
runtime artifacts (the universe, the tier 1 return) are checked only when those
artifacts are present: a fresh clone does not have them, and a missing file is
not a mismatch.

    python tools/check_docs.py            # the tree this file lives in
    python tools/check_docs.py --root DIR # a fixture, for the self-test
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
DIAGRAM = Path("docs") / "architecture.archify.json"
README = Path("README.md")
LIQUID = Path("liquid_universe.json")
MEMOIRE = Path("../MemoireIPSSI/TradeBot_Memoire/05_memoire_long.md")


def load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def newest_tier1(root: Path) -> Path | None:
    """Same rule as `make_liquid.newest_universe`: newest by name, and non-empty.

    An empty tier1 artifact must never shadow a full one, so a stamped file with
    `symbols: []` is skipped rather than selected.
    """
    files = sorted(root.glob("tier1_universe_*.json"), key=lambda p: p.name, reverse=True)
    for path in files:
        doc = load(path)
        if doc and doc.get("symbols"):
            return path
    return None


def family_count(root: Path) -> int:
    """How many families the matrix scans, read from the source.

    Not imported: `tier2_patterns` pulls `marketdata`, which pulls MetaTrader5,
    which does not exist off Windows. A count that CI cannot compute is a count
    that drifts.
    """
    try:
        tree = ast.parse((root / "tier2_patterns.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return 3
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "STRATEGIES" for t in node.targets):
            try:
                return len(ast.literal_eval(node.value))
            except ValueError:
                break
    return 3


def check(root: Path) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    notes: list[str] = []

    diagram = load(root / DIAGRAM)
    if diagram is None:
        failures.append(f"{DIAGRAM.name} is missing or not JSON — the map is part of the tree")
        return failures, notes
    components = {c.get("id"): c for c in diagram.get("components") or []}

    # 1. Every file the map claims to describe must still exist.
    for cid, comp in components.items():
        for src in comp.get("sources") or []:
            path = src.get("path")
            if path and not (root / path).exists() and not (root.parent / path).exists():
                failures.append(f"architecture map: component '{cid}' cites '{path}', which does not exist")

    # 2. Numbers that come from the artifacts, when the artifacts are here.
    liquid = load(root / LIQUID)
    t1 = newest_tier1(root)
    tier1_doc = load(t1) if t1 else None

    if liquid and tier1_doc:
        universe = liquid.get("count")
        broker = tier1_doc.get("universe_total")
        shares = (liquid.get("by_class") or {}).get("Shares")

        tag = (components.get("tier1") or {}).get("tag") or ""
        want = f"{broker:,} → {universe}"
        if want not in tag:
            failures.append(f"architecture map: tier1 tag is '{tag}', the artifacts say '{want}'")

        ltag = (components.get("liquid") or {}).get("tag") or ""
        if shares is not None and f"{shares} shares" not in ltag:
            failures.append(f"architecture map: liquid tag is '{ltag}', the universe has {shares} shares")

        t2 = (components.get("tier2") or {}).get("tag") or ""
        lo, hi = universe, universe * family_count(root)
        if f"{lo}–{hi}" not in t2 and f"{lo}-{hi}" not in t2:
            failures.append(f"architecture map: tier2 tag is '{t2}', the matrix is {lo}–{hi} tests/scan")
    else:
        notes.append("no liquid_universe.json / tier1 artifact on this tree — number checks skipped")

    # 3. A tool that is not in the README is a tool nobody knows exists.
    readme_path = root / README
    readme = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    for tool in sorted((root / "tools").glob("*.py")):
        if tool.name not in readme:
            failures.append(f"README does not mention tools/{tool.name}")

    # 4. The mémoire carries the same structural facts, in French, and lives
    #    outside the repo: advisory, because CI cannot see or fix it.
    memo = (root / MEMOIRE).resolve()
    if liquid and memo.exists():
        # The digits, never a word map: a `{248: "deux cent quarante-huit"}` lookup
        # silently skipped this check for every other count, which is how it stopped
        # firing the day the universe became 180.
        body = memo.read_text(encoding="utf-8")
        count = liquid.get("count")
        if count and str(count) not in body:
            notes.append(f"mémoire does not mention {count} symbols")
    elif not memo.exists():
        notes.append("mémoire not checked out beside the repo — its numbers were not verified")

    return failures, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, default=HERE)
    args = ap.parse_args(argv)
    failures, notes = check(args.root.resolve())
    for f in failures:
        print(f"FAIL  {f}")
    for n in notes:
        print(f"note  {n}")
    if failures:
        print(f"\n{len(failures)} document(s) disagree with the tree — "
              f"see AGENTS.md §2b for what to regenerate")
        return 1
    print("documents agree with the tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
