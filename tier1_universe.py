"""TIER 1 — universe scanner.

Every symbol MT5 offers -> filtered for liquidity, cost and data depth, grouped
by asset class, with a per-class exclusion tally so the funnel is auditable.

Two things this build gets wrong, both found by testing rather than assuming:

  * `symbol_info_tick()` returns None and `symbol_info().time` is 0 for symbols
    not selected into Market Watch, and `symbol_info().spread` is 0 even after
    selection. So: select first, and read the spread off the live tick.
  * this MT5 python build has no `symbol_info_session_trade`, and quote
    freshness over-reports "closed" (47/94 FX and 50/53 crypto at 13:26 UTC on a
    Thursday). Liveness is therefore measured as the age of the last CLOSED bar,
    which is exactly what "currently triggering" depends on anyway.

Filters, cheapest first (a 3-bar probe rejects a closed share before we pull
3,000 bars of history for it):

  1. tradable     trade_mode == FULL
  2. market open  last closed H1 bar ended within --max-idle minutes
  3. data depth   >= --min-days of H1 bars actually returned
  4. cost         spread <= --max-spread-pct of the 14-day DAILY ATR

Market Watch additions made by the scan are undone at the end.

    python tier1_universe.py
    python tier1_universe.py --skip-shares
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5

import marketdata as md

HERE = Path(__file__).resolve().parent

GROUPS = ("FX", "Crypto", "Indices", "Commodities", "Shares")
TRADE_MODE_FULL = 4


def newest_universe(directory: Path = HERE) -> Path | None:
    """Newest tier1 artifact, timestamped files first, legacy single file last."""
    stamped = sorted(directory.glob("tier1_universe_*.json"))
    if stamped:
        return stamped[-1]
    legacy = directory / "tier1_universe.json"
    return legacy if legacy.exists() else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-days", type=int, default=100)
    ap.add_argument("--max-spread-pct", type=float, default=15.0,
                    help="spread as a percent of 14-day daily ATR")
    ap.add_argument("--max-idle", type=int, default=150,
                    help="minutes since the last closed H1 bar ended")
    ap.add_argument("--h1-bars", type=int, default=3000)
    ap.add_argument("--skip-shares", action="store_true")
    ap.add_argument("--out", default=None,
                    help="default: tier1_universe_<UTC stamp>.json — a unique file "
                         "per run, so concurrent scans can never overwrite each other")
    a = ap.parse_args()

    # ponytail: unique filename per run. A shared tier1_universe.json let a stale
    # background scan clobber a corrected one; the fix is no shared mutable path.
    out_path = Path(a.out) if a.out else HERE / (
        f"tier1_universe_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json")

    md.connect()
    offset = md.broker_offset()
    print(f"broker offset {offset / 3600:+.1f}h | {md.now_utc()} UTC | max_idle {a.max_idle}min")

    by_class = defaultdict(list)
    for s in mt5.symbols_get() or []:
        cls = md.asset_class(getattr(s, "path", ""))
        if cls in GROUPS and not (a.skip_shares and cls == "Shares"):
            by_class[cls].append(s.name)

    excl = {c: Counter() for c in GROUPS}
    kept: list[dict] = []
    toggled: list[str] = []

    for cls in GROUPS:
        names = sorted(by_class.get(cls, []))
        if not names:
            continue
        print(f"\n[{cls}] {len(names)} symbols")
        for name in names:
            info = mt5.symbol_info(name)
            if info is None:
                excl[cls]["no_info"] += 1
                continue
            if info.trade_mode != TRADE_MODE_FULL:
                excl[cls]["not_tradable"] += 1
                continue
            if not info.visible:
                mt5.symbol_select(name, True)
                toggled.append(name)

            # cheap liveness probe: 30 bars (fetch() refuses fewer), not 3,000
            probe = md.fetch(name, "H1", 30, offset)
            if probe is None:
                excl[cls]["no_h1"] += 1
                continue
            age = md.bar_age_min(probe, offset)
            if age > a.max_idle:
                excl[cls]["market_closed"] += 1
                continue

            h1 = md.fetch(name, "H1", a.h1_bars, offset)
            if h1 is None:
                excl[cls]["no_h1"] += 1
                continue
            days = md.span_days(h1)
            if days < a.min_days:
                excl[cls]["short_history"] += 1
                continue

            d1 = md.fetch(name, "D1", 60, offset)
            if d1 is None:
                excl[cls]["no_d1"] += 1
                continue
            atr_d = md.atr(d1["h"], d1["l"], d1["c"], 14)
            atr_now = atr_d[-1]
            if not (atr_now and atr_now > 0):
                excl[cls]["no_atr"] += 1
                continue

            tick = mt5.symbol_info_tick(name)
            spread = float(tick.ask - tick.bid) if tick else 0.0
            if spread <= 0:
                excl[cls]["no_spread"] += 1
                continue
            pct = 100.0 * spread / atr_now
            if pct > a.max_spread_pct:
                excl[cls]["too_wide"] += 1
                continue

            info = mt5.symbol_info(name)
            kept.append({
                "symbol": name, "class": cls,
                "spread": round(spread, 6), "atr_d1": round(float(atr_now), 6),
                "spread_pct_atr": round(pct, 3),
                "h1_days": round(days, 1), "h1_bars": int(len(h1["t"])),
                "bar_age_min": round(age, 1),
                "volume_min": float(info.volume_min),
                "volume_step": float(info.volume_step),
                "contract_size": float(info.trade_contract_size),
                "point": float(info.point), "digits": int(info.digits),
            })
        print(f"  kept {sum(1 for k in kept if k['class'] == cls)} | {dict(excl[cls])}")

    for name in toggled:
        mt5.symbol_select(name, False)

    kept.sort(key=lambda r: (r["class"], r["spread_pct_atr"]))
    total = sum(len(v) for v in by_class.values())
    payload = {
        "generated_utc": md.now_utc(),
        "broker_offset_hours": offset / 3600,
        "params": {"min_days": a.min_days, "max_spread_pct": a.max_spread_pct,
                   "max_idle_min": a.max_idle, "skip_shares": a.skip_shares},
        "universe_total": total,
        "kept": len(kept),
        "by_class": {c: sum(1 for k in kept if k["class"] == c) for c in GROUPS},
        "exclusions": {c: dict(excl[c]) for c in GROUPS if excl[c]},
        "symbols": kept,
        "market_watch_added_then_removed": len(toggled),
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"\n=== RESULT === universe {total}, kept {len(kept)}")
    print("by class:", payload["by_class"])
    for c, v in payload["exclusions"].items():
        print(f"  {c} exclusions: {v}")
    print(f"wrote {out_path.name}; Market Watch restored ({len(toggled)} toggled)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
