"""Pre-screen the whole liquid universe, one family at a time, on our own data.

This is the answer to "backtest every symbol" that does not need a second data
vendor: the terminal already serves history for all 120 symbols, and the same
`timemachine_mt5.analyse()` the funnel uses can be pointed at each of them.

What this is **not**: a gate. The corrected bar and the permutation null are the
product (AGENTS.md §1, §3), and a sweep that skipped the null must never claim a
candidate passed. So this tool runs with `permutations=0`, ranks on the test
statistic only, and prints the margin against the real bar with the label
`pre-screen`. A row that clears the bar here still has to survive a full scan.

Read-only: it fetches bars, runs the backtest, writes one JSON artifact and
prints a table. It never imports the order path and never touches the bridge.

    python tools/batch_backtest.py --limit 40 --family donchian
    python tools/batch_backtest.py --all --out sweep_history.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import marketdata as md  # noqa: E402
import timemachine_mt5 as tm  # noqa: E402
import trials_total  # noqa: E402


def summarise(doc: dict, family: str, symbol: str, klass: str | None) -> dict | None:
    """One analyse() result -> one sweep row. Returns None when nothing fired."""
    if not doc:
        return None
    h = doc.get("history") or {}
    s = doc.get("significance") or {}
    if not h or not s:
        return None
    gate = s.get("gate") or "z"
    stat = s.get("t_stat_R") if gate == "t" else s.get("z")
    return {
        "symbol": symbol,
        "class": klass,
        "family": family,
        "n_decided": h.get("n_decided"),
        "win_rate": h.get("win_rate"),
        "avg_R": h.get("avg_R"),
        "max_drawdown_R": h.get("max_drawdown_R"),
        "gate": gate,
        "stat": stat,
        "mean_net_R": s.get("mean_net_R"),
    }


def corrected_bar(legacy: int, logged: int) -> float:
    """The bar is set by *every* hypothesis ever tested, so the frozen legacy
    count is part of it. `trials_total.total()` returns the two apart; adding
    only the second one understates the bar by two thousand trials."""
    return tm.bonferroni_z(legacy + logged)


def rank(rows: list[dict], bar: float | None) -> list[dict]:
    """Highest statistic first; margin is distance to the corrected bar.

    A row with no statistic sorts last — never as zero, which would make an
    unmeasured symbol look like a measured dead one.
    """
    for r in rows:
        stat = r.get("stat")
        r["margin"] = None if (stat is None or bar is None) else round(stat - bar, 3)
        r["clears_bar"] = bool(bar is not None and stat is not None and stat > bar)
    return sorted(
        rows,
        key=lambda r: (r.get("stat") is not None, r.get("stat") if r.get("stat") is not None else float("-inf")),
        reverse=True,
    )


def load_symbols(root: Path, limit: int | None, explicit: list[str] | None) -> list[tuple[str, str | None]]:
    if explicit:
        return [(s.strip(), None) for s in explicit if s.strip()]
    path = root / "liquid_universe.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for row in doc.get("symbols") or []:
        sym = row.get("symbol")
        if not sym:
            continue
        out.append((sym, row.get("class")))
    return out[:limit] if limit else out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="pre-screen the universe (read-only)")
    ap.add_argument("--symbols", default="", help="comma-separated, instead of the universe")
    ap.add_argument("--limit", type=int, default=60, help="how many symbols (0 = all)")
    ap.add_argument("--tf", default="M15")
    ap.add_argument("--family", default="donchian,rsi,bollinger")
    ap.add_argument("--bars", type=int, default=5000)
    ap.add_argument("--replays", type=int, default=2000,
                    help="Monte-Carlo paths; the sweep is a screen, not a report")
    ap.add_argument("--out", default="sweep_history.json")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    families = [f.strip() for f in a.family.split(",") if f.strip()]
    explicit = [s for s in a.symbols.split(",") if s.strip()] if a.symbols else None
    universe = load_symbols(ROOT, a.limit or None, explicit)
    if not universe:
        print("no symbols: liquid_universe.json is absent or empty, and --symbols was not given")
        return 2

    # The bar is set by every hypothesis ever tested, so it comes from the same
    # function the funnel uses — never from a guess about the file's line count.
    try:
        legacy, cumulative = trials_total.total(ROOT / "funnel_history.log")
    except (OSError, ValueError) as e:
        print(f"cannot read the trial count ({e}); the bar will be unset")
        cumulative = None
    bar = corrected_bar(legacy, cumulative) if cumulative else None

    rows: list[dict] = []
    failed = 0
    for sym, klass in universe:
        for fam in families:
            try:
                doc = tm.analyse(
                    sym, a.tf, fam,
                    bars_n=a.bars, replays=a.replays,
                    n_tests=cumulative or 1,
                    permutations=0,        # a screen, not a gate: no null, no claim
                    use_t=True,
                )
            except Exception as e:  # a dead symbol must not stop the sweep
                failed += 1
                if not a.quiet:
                    print(f"{sym}/{fam}: {type(e).__name__}: {e}")
                continue
            row = summarise(doc, fam, sym, klass)
            if row:
                rows.append(row)
                if not a.quiet:
                    print(f"{sym:14} {fam:10} n={row['n_decided'] or 0:>4} "
                          f"win={(row['win_rate'] or 0):6.1%} stat={row['stat']:+.2f} "
                          f"avgR={(row['avg_R'] or 0):+.3f}")

    md.release_auto_selected()
    ranked = rank(rows, bar)

    payload = {
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "kind": "pre-screen",
        "note": ("ranks the test statistic only; the gate needs 1000 permutations and "
                 "the full two-gate scan. Nothing here is a verdict."),
        "tf": a.tf,
        "families": families,
        "symbols_tested": len({r["symbol"] for r in rows}),
        "rows": ranked,
        "trials_cumulative": cumulative,
        "bar": bar,
        "clears_bar": sum(1 for r in ranked if r.get("clears_bar")),
        "failed": failed,
    }
    out = ROOT / a.out
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print()
    print(f"pre-screen · {payload['symbols_tested']} symbols · {len(ranked)} series · "
          f"bar {bar:.2f} (cumulative {cumulative})" if bar else
          f"pre-screen · {len(ranked)} series · no cumulative trial count, so no bar")
    print(f"{'symbol':14} {'family':10} {'n':>5} {'win':>7} {'stat':>7} {'margin':>8}  {'':4}")
    for r in ranked[:15]:
        margin = "—" if r["margin"] is None else f"{r['margin']:+.2f}"
        print(f"{r['symbol']:14} {r['family']:10} {r['n_decided'] or 0:>5} "
              f"{(r['win_rate'] or 0):7.1%} {(r['stat'] if r['stat'] is not None else float('nan')):+7.2f} "
              f"{margin:>8}  {'PASS' if r['clears_bar'] else ''}")
    print(f"\nwrote {out} · {payload['clears_bar']} row(s) above the bar — each still needs a full scan")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
