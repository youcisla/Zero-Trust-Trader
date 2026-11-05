"""Build the intraday universe: the N genuinely most liquid tradable symbols.

A 15-minute cadence cannot afford Tier 1 (it takes ~35 minutes), so the deep
universe scan runs separately and this reduces its output to a cache the
intraday scan reuses. The output has the SAME schema as a tier1 artifact, so
`tier2_patterns.py --tier1 liquid_universe.json` works with no code change.

WHY VOLUME AND NOT SPREAD
    Ranking by spread/ATR was tried first and produced MAERSKB.DK, GMAB.DK,
    COLOB.DK — spreads tight relative to their own tiny ATR, but not liquid.
    Spread measures *cost*, not *turnover*: a quiet instrument can look cheap
    and still be impossible to trade intraday.

    So ranking is mean tick volume (the broker feed's actual traded ticks), with
    spread used only as a veto: liquid but too expensive is still rejected.

    ponytail: tick volume proxies turnover, not true currency volume. Swap in
    symbol_info().volume or a real volume feed if the broker supplies one.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

import marketdata as md

HERE = Path(__file__).resolve().parent
OUT = HERE / "liquid_universe.json"

# Benchmarks kept whenever available, so a pure volume ranking cannot quietly
# drop gold, oil or the index contracts the intraday edge is most likely on.
BENCHMARKS = (
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
    "EURJPY", "GBPJPY", "EURGBP", "AUDJPY", "CADJPY",
    "XAUUSD", "XAGUSD",
    "BTCUSD", "ETHUSD",
    "US30", "NAS100", "SP500", "GER40", "UK100", "JP225", "US2000",
    "USOIL", "UKOIL", "XTIUSD", "XBRUSD", "NATGAS",
)


def newest_universe() -> Path:
    stamped = sorted(HERE.glob("tier1_universe_*.json"))
    return stamped[-1] if stamped else HERE / "tier1_universe.json"


def measure(rows: list[dict], tf: str, lookback: int, max_spread_pct: float) -> list[dict]:
    """Attach mean tick volume to each row; drop anything too expensive to trade."""
    out = []
    for r in rows:
        if r["spread_pct_atr"] > max_spread_pct:
            continue
        if md.fetch(r["symbol"], tf, lookback) is None:
            continue
        rates = md.mt5.copy_rates_from_pos(r["symbol"], md.TIMEFRAMES[tf], 0, lookback)
        if rates is None or len(rates) == 0:
            continue
        vol = float(np.mean([float(x["tick_volume"]) for x in rates]))
        if vol <= 0:
            continue
        out.append({**r, "mean_tick_volume": round(vol, 1)})
    return out


def order(measured: list[dict]) -> list[dict]:
    """Benchmarks first, then strictly by turnover."""
    by_name = {r["symbol"]: r for r in measured}
    head = [by_name[n] for n in BENCHMARKS if n in by_name]
    seen = {r["symbol"] for r in head}
    tail = sorted((r for r in measured if r["symbol"] not in seen),
                  key=lambda r: r["mean_tick_volume"], reverse=True)
    return head + tail


def dedupe(rows: list[dict], tf: str, corr_max: float, pool: int,
           bars_n: int = 3000) -> tuple[list[dict], int]:
    """Collapse near-identical instruments, keeping the most liquid of each.

    Without this the list is nine gold crosses (XAUCNH, XAUSGD, XAUEUR, XAUJPY,
    XAUTHB...), which are one position in nine quote currencies — the same
    failure the Tier 2 dedupe exists to prevent, caught one stage earlier where
    it can still free up slots.
    """
    cand = rows[:pool]
    bars = {}
    for r in cand:
        b = md.fetch(r["symbol"], tf, bars_n)
        if b is not None:
            bars[r["symbol"]] = b
    kept: list[dict] = []
    for r in cand:
        if r["symbol"] not in bars:
            continue
        clash = False
        for k in kept:
            rho = md.corr_returns(bars[r["symbol"]], bars[k["symbol"]])
            if rho is not None and abs(rho) > corr_max:
                clash = True
                break
        if not clash:
            kept.append(r)
    return kept, len(cand) - len(kept)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier1", default=None)
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--lookback", type=int, default=500)
    ap.add_argument("--max-spread-pct", type=float, default=5.0,
                    help="veto anything whose spread exceeds this %% of D1 ATR")
    ap.add_argument("--corr-max", type=float, default=0.7)
    ap.add_argument("--pool", type=int, default=150,
                    help="candidates considered before correlation dedupe")
    a = ap.parse_args()

    src_path = Path(a.tier1) if a.tier1 else newest_universe()
    src = json.loads(src_path.read_text())
    rows = src["symbols"]
    print(f"source: {src_path.name} ({len(rows)} symbols) — measuring {a.tf} turnover...")

    md.connect()
    measured = measure(rows, a.tf, a.lookback, a.max_spread_pct)
    ordered = order(measured)
    kept, dropped = dedupe(ordered, a.tf, a.corr_max, a.pool)
    picked = kept[: a.top]
    md.release_auto_selected()

    payload = {
        "generated_utc": md.now_utc(),
        "source": src_path.name,
        "criteria": (f"top {a.top} by mean {a.tf} tick volume, correlation-deduped at "
                     f"|rho|>{a.corr_max}, benchmarks force-included, "
                     f"spread veto {a.max_spread_pct}% of ATR"),
        "count": len(picked),
        "measured": len(measured),
        "deduped_away": dropped,
        "by_class": {},
        "symbols": picked,
    }
    for r in picked:
        payload["by_class"][r["class"]] = payload["by_class"].get(r["class"], 0) + 1
    OUT.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"measured {len(measured)} | dedupe dropped {dropped} | "
          f"selected {len(picked)} -> {OUT.name}  {payload['by_class']}")
    for n, r in enumerate(picked, 1):
        print(f"  {n:>2} {r['symbol']:14} {r['class']:12} vol {r['mean_tick_volume']:>10.0f} "
              f"spread {r['spread_pct_atr']:>5.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
