"""One-time Market Watch purge, scoped to symbols our own scans leaked.

Safety rule: a symbol is deselected ONLY if it appears in one of our artifacts
AND is currently visible. Anything not in our artifacts is left untouched, and
the script prints exactly how many visible symbols it refused to touch so the
blast radius is auditable rather than assumed.
"""

from __future__ import annotations

import json

import MetaTrader5 as mt5

from pathlib import Path

HERE = Path(__file__).resolve().parent


def artifact_symbols() -> set[str]:
    names: set[str] = set()
    for f in HERE.glob("tier1_universe*.json"):
        try:
            for row in json.loads(f.read_text()).get("symbols", []):
                names.add(row["symbol"])
        except Exception as e:
            print(f"  ! {f.name}: {e}")
    for fname in ("tier2_candidates.json", "tier3_results.json"):
        p = HERE / fname
        if not p.exists():
            continue
        try:
            doc = json.loads(p.read_text())
        except Exception as e:
            print(f"  ! {fname}: {e}")
            continue
        for key in ("top", "all_hits", "deduped"):
            for row in doc.get(key, []) or []:
                if isinstance(row, dict) and row.get("symbol"):
                    names.add(row["symbol"])
        for row in doc.get("results", []) or []:
            if isinstance(row, dict) and row.get("symbol"):
                names.add(row["symbol"])
    return names


def main() -> int:
    mt5.initialize()
    ours = artifact_symbols()
    visible = {s.name for s in mt5.symbols_get() if s.visible}
    target = sorted(visible & ours)
    keep = sorted(visible - ours)

    print(f"artifact symbols:      {len(ours)}")
    print(f"visible before:        {len(visible)}")
    print(f"will deselect (ours):  {len(target)}")
    print(f"left untouched:        {len(keep)}  <- not in any artifact, yours")

    released = 0
    for name in target:
        if mt5.symbol_select(name, False):
            released += 1

    after = {s.name for s in mt5.symbols_get() if s.visible}
    print(f"\ndeselected:            {released}")
    print(f"visible after:         {len(after)}")
    print(f"untouched still there: {len(after & set(keep))}/{len(keep)}")

    # fetch-then-release must still hold after the purge
    hidden = [s.name for s in mt5.symbols_get() if not s.visible]
    if hidden:
        import marketdata as md
        probe = hidden[0]
        md.fetch(probe, "H1", 60)
        vis_mid = bool(mt5.symbol_info(probe).visible)
        n = md.release_auto_selected()
        print(f"\nprobe {probe}: visible after fetch={vis_mid}, "
              f"released={n}, visible after release={mt5.symbol_info(probe).visible}")
        assert vis_mid and not mt5.symbol_info(probe).visible, "release mechanism broken"
        assert len({s.name for s in mt5.symbols_get() if s.visible}) == len(after), \
            "purge count drifted during probe"
    print("OK: purge scoped, release mechanism holds")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
