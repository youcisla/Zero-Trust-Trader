"""Back up the Market Watch symbols that are NOT ours, so a purge is reversible.

Writes `my_market_watch.json` next to the funnel. Only symbols absent from every
tier1/tier2/tier3 artifact are captured — those are the ones a future purge must
never touch.

    python backup_market_watch.py            # write the backup
    python backup_market_watch.py --restore  # put them back into Market Watch
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import MetaTrader5 as mt5

HERE = Path(__file__).resolve().parent
WORK = Path(__file__).resolve().parent
if WORK.exists():
    HERE = WORK
OUT = HERE / "my_market_watch.json"


def artifact_symbols() -> set[str]:
    """Every symbol our own scans have touched, read from the in-tree artifacts."""
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--restore", action="store_true")
    a = ap.parse_args()
    mt5.initialize()

    if a.restore:
        if not OUT.exists():
            print(f"no backup at {OUT}")
            return 1
        rows = json.loads(OUT.read_text())["symbols"]
        n = sum(1 for r in rows if mt5.symbol_select(r["name"], True))
        print(f"restored {n}/{len(rows)} symbols into Market Watch")
        return 0

    ours = artifact_symbols()
    rows = [{"name": s.name, "path": s.path, "description": s.description}
            for s in mt5.symbols_get()
            if s.visible and s.name not in ours]
    rows.sort(key=lambda r: r["name"])
    OUT.write_text(json.dumps(
        {"_note": "Market Watch symbols not owned by the MT5 funnel. Restore with "
                  "python backup_market_watch.py --restore",
         "saved_utc": __import__("datetime").datetime.now(
             __import__("datetime").timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
         "count": len(rows), "symbols": rows}, indent=2) + "\n")
    print(f"wrote {OUT.name}: {len(rows)} personal symbols")
    mt5.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
