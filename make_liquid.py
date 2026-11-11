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
import math
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


def count_symbols(path: Path) -> int:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    return len(d.get("symbols") or d.get("rows") or [])


def newest_universe() -> Path:
    """The newest tier1 artifact that actually holds symbols.

    A failed tier1 run leaves a timestamped file with `symbols: []`, and "newest
    wins" picked it: the scan then measured nothing and refused to write, while
    the good universe sat in tier1_universe.json. An empty artifact must never
    shadow a full one - the emptiness is a measurement that did not happen.
    """
    stamped = sorted((p for p in HERE.glob("tier1_universe_*.json")
                      if count_symbols(p) > 0), reverse=True)
    if stamped:
        return stamped[0]
    return HERE / "tier1_universe.json"


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
        if not math.isfinite(vol) or vol <= 0:
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
            if rho is None or not math.isfinite(rho) or abs(rho) > corr_max:
                clash = True
                break
        if not clash:
            kept.append(r)
    return kept, len(cand) - len(kept)


# ---------------------------------------------------------------------------
# The terminal is single-occupancy.
#
# Two processes reading bars at once is how `(-10005, 'IPC timeout')` happens, and
# how `symbol_select` churn leaves a symbol with spread 0 — which turns a backtest
# into a cost-free market with no error at all. Called from run_funnel.py the lock
# is already held and `--lock-held` says so; called by hand, take it here.
def _claim_terminal(held: bool, max_age_min: float = 40.0) -> bool:
    if held:
        return True
    import process_lock
    from pathlib import Path as _P
    lock = _P(__file__).with_name("funnel.lock")
    if process_lock.acquire(lock, max_age_min * 60):
        import atexit
        atexit.register(process_lock.release, lock)
        return True
    print("REFUSED: the funnel holds funnel.lock — run this through "
          "`run_funnel.py --rebuild`, or wait for the scan to finish")
    return False


def refuses_shrink(picked: int, before: int, force: bool) -> bool:
    """A universe may only be replaced by at least half of itself, unless forced.

    A `--top 1` probe once replaced 188 symbols with EURUSD and the next scan
    reported `universe=1` as though the market had narrowed to a currency pair.
    Shrinking is sometimes the right measurement, so the escape is explicit.
    """
    return bool(before) and picked * 2 < before and not force


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock-held", action="store_true",
                    help="the caller already holds funnel.lock (set by run_funnel --rebuild)")
    ap.add_argument("--tier1", default=None)
    ap.add_argument("--top", type=int, default=120,
                    help="0 = keep every symbol that survives dedupe")
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--lookback", type=int, default=500)
    ap.add_argument("--max-spread-pct", type=float, default=5.0,
                    help="veto anything whose spread exceeds this %% of D1 ATR")
    ap.add_argument("--corr-max", type=float, default=0.7)
    ap.add_argument("--force", action="store_true",
                    help="write a universe less than half the size of the one on disk")
    ap.add_argument("--pool", type=int, default=150,
                    help="candidates considered before correlation dedupe; 0 = all measured")
    a = ap.parse_args()
    if not _claim_terminal(a.lock_held):
        return 3

    src_path = Path(a.tier1) if a.tier1 else newest_universe()
    src = json.loads(src_path.read_text())
    rows = src["symbols"]
    print(f"source: {src_path.name} ({len(rows)} symbols) — measuring {a.tf} turnover...")

    md.connect()
    measured = measure(rows, a.tf, a.lookback, a.max_spread_pct)
    ordered = order(measured)
    # 0 means "no cap" on either side: the whole measured list is deduped, and every
    # survivor is kept. Both caps default to a number, so a caller that omits them
    # keeps the old behaviour; `--top 0` is an explicit request for everything.
    kept, dropped = dedupe(ordered, a.tf, a.corr_max, a.pool or len(ordered))
    picked = kept[: a.top] if a.top else kept
    md.release_auto_selected()

    payload = {
        "generated_utc": md.now_utc(),
        "source": src_path.name,
        "criteria": (f"{'every' if not a.top else 'top ' + str(a.top)} symbol by mean {a.tf} tick volume, correlation-deduped at "
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
    if not picked:
        # A rebuild outside market hours measures a dead terminal and returns
        # nothing. Writing that over a working universe is how the funnel spent
        # two days scanning zero symbols and reporting STAND_DOWN.
        print("no symbol survived the filter — keeping the existing universe")
        return 1
    # A universe that halves is a diagnosis, not a selection. A `--top 1` probe
    # overwrote 188 symbols with EURUSD and nothing complained: the next scan
    # tested one symbol and reported STAND_DOWN as if that were the market.
    # Shrinking is sometimes right, so the escape is explicit.
    try:
        before = int(json.loads(OUT.read_text())["count"])
    except (OSError, ValueError, KeyError, TypeError):
        before = 0
    if refuses_shrink(len(picked), before, a.force):
        print(f"REFUSED: {len(picked)} symbols would replace {before} on disk. "
              f"Pass --force if that is the measurement you want.")
        return 4
    OUT.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"measured {len(measured)} | dedupe dropped {dropped} | "
          f"selected {len(picked)} -> {OUT.name}  {payload['by_class']}")
    for n, r in enumerate(picked, 1):
        print(f"  {n:>2} {r['symbol']:14} {r['class']:12} vol {r['mean_tick_volume']:>10.0f} "
              f"spread {r['spread_pct_atr']:>5.2f}%")
    return 0


def main() -> int:
    try:
        return _main()
    finally:
        md.release_auto_selected()


if __name__ == "__main__":
    raise SystemExit(main())
