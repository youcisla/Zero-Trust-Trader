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
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
DIAGRAM = HERE / "docs" / "architecture.archify.json"
README = HERE / "README.md"
LIQUID = HERE / "liquid_universe.json"
MEMOIRE = HERE.parent / "MemoireIPSSI" / "TradeBot_Memoire" / "05_memoire_long.md"

FAMILIES = 3  # donchian, rsi, bollinger — the 13:00-17:00 UTC matrix
failures: list[str] = []
notes: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)


def note(msg: str) -> None:
    notes.append(msg)


def load(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def newest_tier1() -> Path | None:
    files = sorted(HERE.glob("tier1_universe_*.json"), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def main() -> int:
    diagram = load(DIAGRAM)
    if diagram is None:
        fail(f"{DIAGRAM.name} is missing or not JSON — the architecture map is part of the tree")
        return report()

    components = {c.get("id"): c for c in diagram.get("components") or []}

    # 1. Every file the map claims to describe must still exist. A rename that
    #    leaves the map pointing at the old path makes the map fiction.
    for cid, comp in components.items():
        for src in comp.get("sources") or []:
            path = src.get("path")
            if path and not (HERE / path).exists() and not (HERE.parent / path).exists():
                fail(f"architecture map: component '{cid}' cites '{path}', which does not exist")

    # 2. Numbers that come from the artifacts, when the artifacts are here.
    liquid = load(LIQUID)
    t1 = newest_tier1()
    tier1_doc = load(t1) if t1 else None

    if liquid and tier1_doc:
        universe = liquid.get("count")
        broker = tier1_doc.get("universe_total")
        shares = (liquid.get("by_class") or {}).get("Shares")

        tag = (components.get("tier1") or {}).get("tag") or ""
        want = f"{broker:,} → {universe}"
        if want not in tag:
            fail(f"architecture map: tier1 tag is '{tag}', the artifacts say '{want}'")

        ltag = (components.get("liquid") or {}).get("tag") or ""
        if shares is not None and f"{shares} shares" not in ltag:
            fail(f"architecture map: liquid tag is '{ltag}', the universe has {shares} shares")

        t2 = (components.get("tier2") or {}).get("tag") or ""
        lo, hi = universe, universe * FAMILIES
        if f"{lo}–{hi}" not in t2 and f"{lo}-{hi}" not in t2:
            fail(f"architecture map: tier2 tag is '{t2}', the matrix is {lo}–{hi} tests/scan")
    else:
        note("no liquid_universe.json / tier1 artifact on this tree — number checks skipped")

    # 3. A tool that is not in the README is a tool nobody knows exists.
    readme = README.read_text(encoding="utf-8") if README.exists() else ""
    for tool in sorted((HERE / "tools").glob("*.py")):
        if tool.name not in readme:
            fail(f"README does not mention tools/{tool.name}")

    # 4. The mémoire carries the same structural facts, in French, and lives
    #    outside the repo: advisory, because CI cannot see or fix it.
    if liquid and MEMOIRE.exists():
        words = {
            248: "deux cent quarante-huit",
        }.get(liquid.get("count"))
        body = MEMOIRE.read_text(encoding="utf-8")
        if words and words not in body and str(liquid.get("count")) not in body:
            note(f"mémoire does not mention {liquid.get('count')} symbols (neither digits nor '{words}')")
    elif not MEMOIRE.exists():
        note("mémoire not checked out beside the repo — its numbers were not verified")

    return report()


def report() -> int:
    width = max((len(f) for f in failures), default=0)
    for f in failures:
        print(f"FAIL  {f.ljust(width)}" if width > 30 else f"FAIL  {f}")
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
