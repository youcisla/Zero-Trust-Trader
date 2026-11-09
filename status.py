"""Full system diagnostic.

    python status.py

Read-only. Places nothing, toggles nothing, starts nothing.

Covers: terminal and account, the policy in force, Windows scheduled tasks
(next and previous run), the last 10 funnel runs, and the live liquid universe.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent
TASKS = ("HermesMT5Funnel", "HermesMT5Rebuild")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def terminal_block() -> None:
    section("MT5 terminal")
    initialized = False
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            print("  terminal not reachable")
            return
        initialized = True
        t, a = mt5.terminal_info(), mt5.account_info()
        if t is None or a is None:
            print("  terminal/account observation unavailable")
            return
        pos = mt5.positions_get()
        orders = mt5.orders_get()
        print(f"  AutoTrading : {'ON  <-- UNSAFE' if t.trade_allowed else 'off'}")
        demo = "DEMO" if "demo" in (a.server or "").lower() else "LIVE??"
        print(f"  account     : identifier/server redacted ({demo})")
        print(f"  balance     : {a.balance:.2f} {a.currency}   equity {a.equity:.2f}")
        print(f"  positions   : {len(pos) if pos is not None else 'UNKNOWN (retrieval failed)'}"
              f"   orders {len(orders) if orders is not None else 'UNKNOWN (retrieval failed)'}")
        for p in pos or ():
            side = "LONG" if p.type == 0 else "SHORT"
            print(f"    {p.symbol} {side} {p.volume} "
                  f"entry {p.price_open} now {p.price_current} "
                  f"sl {p.sl or 'none'} pnl {p.profit:+.2f}")
        symbols = mt5.symbols_get()
        print(f"  market watch: {sum(1 for s in symbols if s.visible) if symbols is not None else 'UNKNOWN'} symbols")
    except Exception as e:
        print(f"  unavailable ({type(e).__name__})")
    finally:
        if initialized:
            mt5.shutdown()


def policy_summary(path: Path) -> list[str]:
    """Only typed operational controls are safe to share in diagnostics."""
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    policy = data.get("policy", {})
    if not isinstance(policy, dict):
        return ["policy section invalid"]
    lines = []
    for name in ("auto_approve_notional", "max_notional_per_trade", "max_orders_per_minute"):
        value = policy.get(name)
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            number = Decimal("NaN")
        if isinstance(value, bool) or not number.is_finite():
            lines.append(f"{name}=unavailable (not a finite numeric control)")
        else:
            lines.append(f"{name}={number}")
    return lines


def config_block() -> None:
    section("configuration in force")
    print("  run window    : 05:00-17:00 UTC, session matrix narrows families early")
    pin = os.environ.get("MT5_EXPECTED_LOGIN")
    hint = "set (redacted)" if pin else ("not set in THIS shell (the launchers pin it; "
                   "a scan run without it refuses by design)")
    print(f"  expected login: {hint}")
    # This tree is the installation: its config.toml is the policy in force.
    # The per-user %APPDATA% copy is the pre-migration location, read only as a
    # fallback so a stale one cannot mask the policy the scan actually uses.
    cfg = HERE / "config.toml"
    if not cfg.exists():
        cfg = Path(os.environ.get("APPDATA", "")) / "mt5-mcp" / "config.toml"
    if cfg.exists():
        print("  policy        : present (location and private fields redacted)")
        try:
            for line in policy_summary(cfg):
                print(f"    {line}")
        except (OSError, ValueError):
            print("    policy unreadable or invalid; no control values verified")
    else:
        print("  policy        : config.toml not found (consent gate not configured)")


def tasks_block() -> None:
    section("Windows scheduled tasks")
    if sys.platform != "win32":
        print("  skipped (not Windows)")
        return
    for name in TASKS:
        try:
            out = subprocess.run(
                ["schtasks", "/query", "/tn", name, "/fo", "LIST", "/v"],
                capture_output=True, text=True, timeout=20, check=True,
            ).stdout
        except Exception as e:
            print(f"  {name}: query failed ({type(e).__name__})")
            continue
        if "cannot find" in out.lower() or not out.strip():
            print(f"  {name}: NOT REGISTERED")
            continue
        wanted = {"next run time", "last run time", "status", "last result"}
        fields = []
        for ln in out.splitlines():
            if ":" not in ln:
                continue
            k, v = ln.split(":", 1)
            if k.strip().lower() in wanted:
                fields.append(f"{k.strip()}={v.strip()}")
        print(f"  {name}: " + "  ".join(fields))


def history_block() -> None:
    section("last 10 funnel runs")
    log = HERE / "funnel_history.log"
    if not log.exists():
        print("  no funnel_history.log yet")
        return
    lines = [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()][-10:]
    if not lines:
        print("  log is empty")
    for ln in lines:
        print(f"  {ln[:160]}")


def trials_block() -> None:
    section("trials accounting")
    try:
        import timemachine_mt5 as tm

        import trials_total
        legacy, logged = trials_total.total()
        total = legacy + logged
        print(f"  total {total}  (legacy {legacy} + logged {logged})")
        print(f"  z bar at this count: {tm.bonferroni_z(total):.2f}")
        print(f"  -> botlab --trials {total}")
        # Tamper-evidence: a chain break means the bar may have been lowered.
        state, detail = trials_total.verify()
        if state == "broken":
            print(f"  !! LEDGER TAMPERED — {detail}")
        elif state == "ok":
            print(f"  chain: ok — {detail}")
        else:
            print(f"  chain: {state} — {detail}")
    except Exception as e:
        print(f"  unavailable: {e}")


def universe_block() -> None:
    section("liquid universe (top 50)")
    p = HERE / "liquid_universe.json"
    if not p.exists():
        print("  liquid_universe.json missing - run: python run_funnel.py --rebuild")
        return
    doc = json.loads(p.read_text())
    raw = doc if isinstance(doc, list) else doc.get("symbols", [])
    names = [s if isinstance(s, str) else s.get("symbol", "?") for s in raw]
    stamp = doc.get("generated_utc", "?") if isinstance(doc, dict) else "?"
    print(f"  {len(names)} symbols, generated {stamp}")
    for i in range(0, len(names), 10):
        print("   " + " ".join(f"{n:<10}" for n in names[i:i + 10]).rstrip())


def verdict_block() -> None:
    section("newest verdict")
    t3 = HERE / "tier3_results.json"
    if not t3.exists():
        print("  no results yet")
        return
    doc = json.loads(t3.read_text())
    state = "STAND_DOWN" if doc.get("stood_down") else \
        "WINNER " + ",".join(doc.get("survivors", []))
    print(f"  {doc.get('generated_utc')}  {state}  "
          f"({len(doc.get('results', []))} tested, {doc.get('trials_declared')} trials, "
          f"z bar {doc.get('z_crit_corrected')})")


def main() -> int:
    terminal_block()
    config_block()
    tasks_block()
    history_block()
    trials_block()
    universe_block()
    verdict_block()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
