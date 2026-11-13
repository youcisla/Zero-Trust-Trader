"""How much does one 0.01 lot actually risk, across the 50-symbol universe?

The funnel sizes every candidate at the broker minimum (0.01 lots) regardless of
the instrument. That is fine while it never trades, and wrong the moment it
does: a 1xATR stop on 0.01 lots of EURUSD and on 0.01 lots of BTCUSD are not the
same trade. This measures the spread, because "risk 1R" is only meaningful if R
is roughly constant.

Read-only: symbol specs and M15 bars, no orders.

Run from the repo root:  python tools/vol_dispersion.py [--account 200]
Why it matters: docs/SIZE-AND-RUIN.md
"""

from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

import MetaTrader5 as mt5

import marketdata as md

ROOT = Path(os.environ.get("ZTT_ROOT", ".")).resolve()  # run from the repo root
UNIVERSE = ROOT / "liquid_universe.json"
ACCOUNT = 200.0


def main(argv: list[str]) -> int:
    account = ACCOUNT
    if "--account" in argv:
        account = float(argv[argv.index("--account") + 1])
    md.connect()
    syms = [
        s["symbol"] if isinstance(s, dict) else s
        for s in json.loads(UNIVERSE.read_text(encoding="utf-8"))["symbols"]
    ]
    rows = []
    added: list[str] = []
    for sym in syms:
        inf = mt5.symbol_info(sym)
        was_visible = bool(inf and getattr(inf, "visible", False))
        if not mt5.symbol_select(sym, True):
            continue
        if not was_visible:
            added.append(sym)  # ours to release; the user's stays
        si = mt5.symbol_info(sym)
        bars = md.fetch(sym, "M15", 300)
        if not (si and bars):
            continue
        atr = float(md.atr(bars["h"], bars["l"], bars["c"], 14)[-1])
        pv = si.trade_tick_value * (si.point / si.trade_tick_size) * si.volume_min
        if pv <= 0 or atr <= 0:
            continue
        risk = pv * (atr / si.point)  # money lost if a 1xATR stop is hit
        rows.append((sym, atr, pv, risk, risk / account * 100))
    md.release_auto_selected()
    for sym in added:
        mt5.symbol_select(sym, False)

    rows.sort(key=lambda r: r[3])
    risks = [r[3] for r in rows]
    print(
        f"universe {len(rows)} symbols · account {account:.0f} EUR · risk of a 1xATR stop "
        f"on 0.01 lots\n"
    )
    print(
        f"{'symbol':<11}{'ATR14 M15':>11}{'EUR/point':>11}{'risk 1R':>10}{'% of acct':>10}"
    )
    for sym, atr, pv, risk, pct in (
        rows[:4] + rows[len(rows) // 2 - 2 : len(rows) // 2 + 2] + rows[-4:]
    ):
        print(f"{sym:<11}{atr:>11.5f}{pv:>11.4f}{risk:>9.2f}€{pct:>9.2f}%")
    print(
        f"\nmedian 1R  {statistics.median(risks):>7.2f}€  "
        f"({statistics.median(risks) / account * 100:.2f}% of the account)"
    )
    print(
        f"min / max  {min(risks):>7.2f}€ / {max(risks):.2f}€  "
        f"-> ratio {max(risks) / min(risks):.1f}x"
    )
    over = [r for r in rows if r[4] > 2.0]
    print(
        f"\nsymbols where one 1xATR stop risks more than 2% of a {account:.0f} EUR account: "
        f"{len(over)}/{len(rows)}"
    )
    for r in sorted(over, key=lambda x: -x[4])[:6]:
        print(f"  {r[0]:<11} 1R = {r[3]:>7.2f}€  ({r[4]:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
