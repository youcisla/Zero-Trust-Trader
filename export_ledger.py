"""Export closed MT5 trades to a botlab-oss honest_backtest ledger CSV.

Output columns match botlab's price form:
    closed,symbol,side,entry,exit,stop,hold_hours

Reads MT5's native deal history directly. The bridge's own get_history tool is
NOT used on purpose: in this build its error path fails its output model
("1 validation error for get_historyOutput ... Input should be a valid list"),
so it cannot distinguish "no trades" from "bad request". history_deals_get has
neither problem.

Broker server time is shifted from UTC. The offset is self-calibrated from the
live tick versus the system clock and rounded to the nearest 30 min, rather
than trusting the reported broker_tz_offset_minutes.

`stop` is not carried on deal records (it lived on the order we sent), so it is
resolved from stops.json next to this script:
    {"98552327": "1.12090"}
Trades with no recorded stop are emitted with an empty stop and warned about on
stderr — botlab can compute R only when stop is present.

Usage:
    python export_ledger.py [--days 30] [--symbol EURUSD] [--out trades.csv]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import MetaTrader5 as mt5

import stops

HERE = Path(__file__).resolve().parent

DEAL_ENTRY_IN = 0
DEAL_ENTRY_OUT = 1
DEAL_TYPE_BUY = 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--symbol", default="EURUSD")
    ap.add_argument("--out", default=str(HERE / "trades.csv"))
    args = ap.parse_args()

    stop_map = stops.load()

    if not mt5.initialize():
        print(f"mt5.initialize failed: {mt5.last_error()}", file=sys.stderr)
        return 2

    try:
        mt5.symbol_select(args.symbol, True)
        tick = mt5.symbol_info_tick(args.symbol)
        now = datetime.now(timezone.utc)
        # self-calibrated broker offset, rounded to 30 min
        offset = round(((tick.time if tick else time.time()) - time.time()) / 1800) * 1800
        deals = mt5.history_deals_get(now - timedelta(days=args.days), now + timedelta(days=1)) or []
    finally:
        mt5.shutdown()

    legs: dict[int, list] = defaultdict(list)
    for d in deals:
        if d.symbol != args.symbol:
            continue
        legs[d.position_id].append(d)

    rows = []
    for pos_id, ds in legs.items():
        ins = [d for d in ds if d.entry == DEAL_ENTRY_IN]
        outs = [d for d in ds if d.entry == DEAL_ENTRY_OUT]
        if not ins or not outs:
            continue
        o = ins[0]
        c = outs[-1]
        closed = datetime.fromtimestamp(c.time - offset, timezone.utc)
        rows.append({
            "closed": closed.strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": args.symbol,
            "side": "long" if o.type == DEAL_TYPE_BUY else "short",
            "entry": f"{o.price:.5f}",
            "exit": f"{c.price:.5f}",
            "stop": stop_map.get(str(pos_id), ""),
            "hold_hours": f"{(c.time - o.time) / 3600:.4f}",
        })

    rows.sort(key=lambda r: r["closed"])
    out = Path(args.out)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["closed", "symbol", "side", "entry", "exit", "stop", "hold_hours"],
        )
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} trade(s) -> {out}  (broker offset {offset / 3600:+.1f}h)")
    missing = [r for r in rows if not r["stop"]]
    if missing:
        print(
            f"WARNING: {len(missing)} trade(s) missing a stop in stops.json; "
            f"botlab cannot compute R for those.",
            file=sys.stderr,
        )
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())
