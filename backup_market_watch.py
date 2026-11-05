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

from purge_market_watch import artifact_symbols

HERE = Path(__file__).resolve().parent
WORK = Path(__file__).resolve().parent
if WORK.exists():
    HERE = WORK
OUT = HERE / "my_market_watch.json"


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
