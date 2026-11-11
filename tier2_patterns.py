"""TIER 2 — vectorised strategy-matrix scan over the Tier 1 universe.

Three families, all evaluated on the last closed bar of the scan timeframe
(M15 by default; the funnel passes --tf).

  A  donchian   Donchian-20 breakout, gated by EMA50 vs EMA200 trend
  B  rsi        RSI-14 crossing INTO <35 (long) / >65 (short)
  C  bollinger  close crossing OUT of the 2.0sd band — faded, not followed

A chopping market kills a breakout strategy outright, which is why one family
is not enough: the two mean-reversion families are designed to fire exactly
where the breakout does not. MACD was dropped as a family because a line/signal
cross is trend-following and duplicates Donchian's exposure rather than
diversifying it.

Strength units differ by nature (donchian = channel extension in ATR; rsi/macd
= trigger-bar impulse in ATR), so each family is normalised by its own best of
the day before the families are merged. `score` is therefore "how extreme is
this trigger for its own family today" — a selector heuristic, NOT a claim that
an RSI trigger is better than a Donchian one. Tier 3 is the judge.

Correlation dedupe runs HERE, before Tier 3, so five Monte-Carlo runs buy five
distinct bets instead of paying twice for one underlying (AMD.US-24 vs
AMD.US-PERP measure +0.997 and are the same position twice).

    python tier2_patterns.py
    python tier2_patterns.py --top 5 --corr-max 0.7
"""

from __future__ import annotations

import argparse
import math
import json
import re
import uuid
from pathlib import Path

import pandas as pd

import marketdata as md
import process_lock
from timemachine_mt5 import source_provenance

HERE = Path(__file__).resolve().parent
OUT = HERE / "tier2_candidates.json"
STRATEGIES = ("donchian", "rsi", "bollinger")


def newest_universe() -> Path:
    """Newest Tier 1 artifact: timestamped runs first, legacy single file last."""
    stamped = sorted(HERE.glob("tier1_universe_*.json"))
    return stamped[-1] if stamped else HERE / "tier1_universe.json"


def _sig(strategy, side, strength, close, atr_v, extra=None):
    out = {"strategy": strategy, "side": side, "strength": round(float(strength), 3),
           "close": round(float(close), 6), "atr14": round(float(atr_v), 6)}
    if extra:
        out.update({k: round(float(v), 6) for k, v in extra.items()})
    return out


def scan_symbol(bars: dict, don_n: int, fast: int, slow: int) -> list[dict]:
    """Every family that is triggering on the last closed bar (0-3 signals)."""
    c_all = bars["c"]
    df = pd.DataFrame({"h": bars["h"], "l": bars["l"], "c": c_all})
    n = len(df)
    if n < max(don_n, slow) + 40:
        return []
    a = md.atr(bars["h"], bars["l"], c_all, 14)
    i = n - 1
    if math.isnan(a[i]) or a[i] <= 0:
        return []
    atr_v, c = float(a[i]), float(c_all[i])
    out: list[dict] = []

    # A — Donchian breakout + trend filter
    upper = df["h"].rolling(don_n).max().shift(1)
    lower = df["l"].rolling(don_n).min().shift(1)
    ef = df["c"].ewm(span=fast, adjust=False).mean()
    es = df["c"].ewm(span=slow, adjust=False).mean()
    if not any(pd.isna(x) for x in (upper.iloc[i], lower.iloc[i], ef.iloc[i], es.iloc[i])):
        u, lo = float(upper.iloc[i]), float(lower.iloc[i])
        up_trend = bool(ef.iloc[i] > es.iloc[i])
        extra = {"donchian_upper": u, "donchian_lower": lo,
                 "ema_fast": float(ef.iloc[i]), "ema_slow": float(es.iloc[i])}
        if c > u and up_trend:
            out.append(_sig("donchian", "long", (c - u) / atr_v, c, atr_v, extra))
        elif c < lo and not up_trend:
            out.append(_sig("donchian", "short", (lo - c) / atr_v, c, atr_v, extra))

    # B — RSI mean reversion: crossing INTO the zone, same rule as scan_rsi()
    r = md.rsi(c_all, 14)
    if i >= 1 and not (math.isnan(r[i]) or math.isnan(r[i - 1])):
        impulse = abs(c - float(c_all[i - 1])) / atr_v
        if r[i - 1] >= 35.0 and r[i] < 35.0:
            out.append(_sig("rsi", "long", impulse, c, atr_v, {"rsi": float(r[i])}))
        elif r[i - 1] <= 65.0 and r[i] > 65.0:
            out.append(_sig("rsi", "short", impulse, c, atr_v, {"rsi": float(r[i])}))

    # C — Bollinger fade: a close outside the band is faded, not followed
    _m, bu, bl = md.bollinger(c_all, 20, 2.0)
    if i >= 1 and not any(math.isnan(v) for v in (bu[i], bl[i], bu[i - 1], bl[i - 1])):
        impulse = abs(c - float(c_all[i - 1])) / atr_v
        extra = {"bb_upper": float(bu[i]), "bb_lower": float(bl[i])}
        if c < float(bl[i]) and float(c_all[i - 1]) >= float(bl[i - 1]):
            out.append(_sig("bollinger", "long", impulse, c, atr_v, extra))
        elif c > float(bu[i]) and float(c_all[i - 1]) <= float(bu[i - 1]):
            out.append(_sig("bollinger", "short", impulse, c, atr_v, extra))
    return out


def normalise(hits: list[dict], families: tuple[str, ...] = STRATEGIES) -> None:
    """Scale each family's strength to 0-1 by its own best of the day (in place)."""
    for fam in families:
        vals = [h["strength"] for h in hits if h["strategy"] == fam]
        top = max(vals) if vals else 0.0
        for h in hits:
            if h["strategy"] == fam:
                h["score"] = round(h["strength"] / top, 4) if top > 0 else 0.0


def dedupe(hits: list[dict], corr_max: float, tf: str, bars_n: int = 3000,
           pool: int = 30) -> tuple[list[dict], list[dict]]:
    """Greedy by score: drop any hit too correlated with a stronger kept hit.

    Only the plausible winners are fetched (`pool`), otherwise deduping ~120
    hits would mean thousands of correlation fetches.
    """
    cand = hits[:pool]
    syms = list(dict.fromkeys(h["symbol"] for h in cand))
    bars = {s: md.fetch(s, tf, bars_n) for s in syms}
    kept, dropped = [], []
    for h in cand:
        if bars[h["symbol"]] is None:
            dropped.append({"symbol": h["symbol"], "strategy": h["strategy"],
                            "kept_instead": None, "correlation": None,
                            "reason": "correlation history unavailable", "score": h["score"]})
            continue
        clash = None
        for k in kept:
            rho = 1.0 if k["symbol"] == h["symbol"] else md.corr_returns(bars[k["symbol"]], bars[h["symbol"]])
            if rho is None or not math.isfinite(rho) or abs(rho) > corr_max:
                clash = (k, rho)
                break
        if clash:
            dropped.append({"symbol": h["symbol"], "strategy": h["strategy"],
                            "kept_instead": f"{clash[0]['symbol']}/{clash[0]['strategy']}",
                            "correlation": round(clash[1], 3) if clash[1] is not None and math.isfinite(clash[1]) else None,
                            "reason": "correlated or unverifiable", "score": h["score"]})
        else:
            kept.append(h)
    return kept, dropped


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tier1", default=None, help="default: newest tier1_universe_*.json")
    ap.add_argument("--scan-id", default=None)
    ap.add_argument("--tf", default="H1")
    ap.add_argument("--donchian", type=int, default=20)
    ap.add_argument("--ema-fast", type=int, default=50)
    ap.add_argument("--ema-slow", type=int, default=200)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--corr-max", type=float, default=0.7)
    ap.add_argument("--dedupe-pool", type=int, default=30,
                    help="how many top hits get correlation-checked")
    ap.add_argument("--dedupe-bars", type=int, default=3000,
                    help="bars used for the correlation estimate")
    ap.add_argument("--no-dedupe", action="store_true")
    ap.add_argument("--strategies", default=",".join(STRATEGIES),
                    help="comma-separated subset of the matrix (session-dependent)")
    ap.add_argument("--bars", type=int, default=4000)
    ap.add_argument("--max-age-min", type=float, default=45.0,
                    help="skip a symbol whose last closed bar is older than this "
                         "(a 15-min cadence would otherwise fire on stale weekend bars)")
    a = ap.parse_args()
    scan_id = a.scan_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", scan_id):
        ap.error("invalid --scan-id")

    fams = tuple(x.strip() for x in a.strategies.split(",") if x.strip())
    unknown = [f for f in fams if f not in STRATEGIES]
    if not fams or unknown:
        ap.error(f"--strategies: {unknown or 'empty'} (choose from {list(STRATEGIES)})")

    src_path = Path(a.tier1) if a.tier1 else newest_universe()
    src = json.loads(src_path.read_text())
    symbols = src["symbols"]
    print(f"Tier 1 universe: {len(symbols)} symbols  ({src['generated_utc']})  <- {src_path.name}")

    md.connect()
    offset = md.broker_offset()

    hits, tested, per_family = [], 0, {f: 0 for f in fams}
    stale = 0
    for row in symbols:
        name = row["symbol"]
        bars = md.fetch(name, a.tf, a.bars, offset)
        if bars is None:
            continue
        # Liveness: on a 15-minute cadence a closed market's last bar is hours
        # old, and scanning it would report a "trigger" that is actually
        # yesterday's bar. Skip rather than emit a stale signal.
        if md.bar_age_min(bars, offset) > a.max_age_min:
            stale += 1
            continue
        tested += 1
        for s in scan_symbol(bars, a.donchian, a.ema_fast, a.ema_slow):
            if s["strategy"] not in fams:
                continue
            per_family[s["strategy"]] += 1
            hits.append({"symbol": name, "class": row["class"],
                         "spread_pct_atr": row["spread_pct_atr"], **s})

    normalise(hits, fams)
    hits.sort(key=lambda r: r["score"], reverse=True)

    if a.no_dedupe:
        top, dropped = hits[: a.top], []
    else:
        kept, dropped = dedupe(hits, a.corr_max, a.tf, a.dedupe_bars, a.dedupe_pool)
        top = kept[: a.top]

    total_tests = tested * len(fams)
    payload = {
        **source_provenance(),
        "generated_utc": md.now_utc(),
        "scan_id": scan_id,
        "timeframe": a.tf,
        "strategies": list(fams),
        "trigger": "Donchian-20+EMA trend | RSI-14 zone cross | Bollinger-20 band cross",
        "symbols_in": len(symbols),
        "symbols_tested": tested,
        "strategies_tested": len(fams),
        "total_tests": total_tests,
        "triggering": len(hits),
        "per_family": per_family,
        "corr_max": a.corr_max,
        "dedupe_applied": not a.no_dedupe,
        "top": top,
        # Without this, a reader cannot tell a capped shortlist from a lost one:
        # nine triggering less one dedupe left eight, the file listed six, and
        # the desk reported that as a contradiction. It is --top.
        "top_n": a.top,
        "deduped": dropped,
        "all_hits": hits[:50],
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n")

    print(f"tested {tested} x {len(fams)} strategies = {total_tests} tests "
          f"| triggering {len(hits)}  {per_family}"
          + (f" | {stale} stale/closed skipped" if stale else ""))
    if dropped:
        print(f"correlation dedupe: dropped {len(dropped)} "
              f"(abs(corr) > {a.corr_max}) before Tier 3")
    print(f"\n{'#':>2} {'symbol':14} {'class':11} {'strategy':9} {'side':6} "
          f"{'strength':>9} {'score':>6}")
    for n, r in enumerate(top, 1):
        print(f"{n:>2} {r['symbol']:14} {r['class']:11} {r['strategy']:9} {r['side']:6} "
              f"{r['strength']:>9.3f} {r['score']:>6.2f}")
    print(f"\nwrote {OUT.name}")
    print(f"NOTE: declare {total_tests} tests ({tested} symbols x {len(fams)} "
          f"strategies) for the DSR/Bonferroni accounting.")
    print(f"market watch released: {md.release_auto_selected()} symbols")
    return 0


def main() -> int:
    if (HERE / ".ztt-maintenance").exists():
        print("maintenance marker present; refusing research")
        return 1
    lock = HERE / "funnel.lock"
    owned = not process_lock.parent_holds(lock)
    if owned and not process_lock.acquire(lock, 120 * 60):
        print("another research process holds funnel.lock")
        return 1
    try:
        return _main()
    finally:
        try:
            md.release_auto_selected()
        finally:
            if owned:
                process_lock.release(lock)


if __name__ == "__main__":
    raise SystemExit(main())
