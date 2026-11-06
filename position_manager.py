"""Continuous position supervisor for all three sessions.

Scanning stays inside the 05:00-17:00 UTC window. This process is the other
half: once something is open, it watches it around the clock -- trails the stop,
locks in break-even, and flattens before the session closes.

What it may do to your account
------------------------------
    modify a stop   (protective direction ONLY)
    close a position

What it may never do
--------------------
    open a position, enlarge one, or move a stop away from price

That asymmetry is the whole point. A supervisor that can only reduce risk cannot
turn a bad signal into a blown account, so it is safe to leave running while the
funnel stands down for weeks. `next_stop()` is the single place the direction
rule lives, so every caller inherits it.

Default is DRY RUN. It prints what it would do; `--apply` is required to send
anything to the broker.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "engine_state.json"
LOG = HERE / "position_manager.log"
LOCK = HERE / "position_manager.lock"
MAGIC = 20261105


@dataclass
class Cfg:
    """Tunables. Defaults are deliberately conservative."""
    be_r: float = 1.0          # move to break-even once profit >= 1R
    trail_k: float = 2.0       # trail at 2 x ATR
    initial_k: float = 2.0     # seed a stop at 2 x ATR when one is missing
    min_step_atr: float = 0.10 # ignore micro-adjustments (broker spam / requotes)


def apply_allowed(requested: bool, environ=None) -> tuple[bool, str]:
    """--apply is a request, not a permission.

    A flag can be typed by anything that can build a command string: an agent, a
    scheduled task, a copy-paste from a README. The actual switch is an
    environment variable a human sets in their own session, so a process that
    inherits its parent's environment cannot arm itself by argument alone.
    """
    env = os.environ if environ is None else environ
    if requested and env.get("ZTT_ALLOW_APPLY") != "1":
        return False, "ZTT_ALLOW_APPLY=1 is not set in this session"
    return requested, ""


def next_stop(side: int, entry: float, sl: float, price: float, atr: float,
              spread: float, cfg: Cfg) -> tuple[float | None, str]:
    """Return (new_stop, reason). None means leave the position alone.

    side: +1 long, -1 short. Pure function, no broker state -- this is the only
    place the protective-direction rule is enforced, so it is the only place it
    can be tested.

    Rejects, in order: unusable ATR, a candidate that would move the stop away
    from price, a candidate on the wrong side of the current price, and any
    adjustment too small to be worth a modify request.
    """
    if atr <= 0:
        return None, "no atr"

    risk = abs(entry - sl) if sl else cfg.initial_k * atr
    if risk <= 0:
        risk = cfg.initial_k * atr

    if sl == 0:
        seed = price - side * cfg.initial_k * atr
        if (side > 0 and seed >= price) or (side < 0 and seed <= price):
            return None, "seed would cross price"
        return seed, "seed stop"

    trail = price - side * cfg.trail_k * atr
    candidates = [trail]
    moved = (price - entry) * side
    if moved >= cfg.be_r * risk:
        # +spread so "break-even" is actually flat after costs, not before them
        candidates.append(entry + side * spread)

    new = max(candidates) if side > 0 else min(candidates)

    # 1. protective direction only
    if (side > 0 and new <= sl) or (side < 0 and new >= sl):
        return None, "no improvement"
    # 2. must stay the safe side of the live price
    if (side > 0 and new >= price) or (side < 0 and new <= price):
        return None, "candidate crosses price"
    # 3. no churn
    if abs(new - sl) < cfg.min_step_atr * atr:
        return None, "below min step"

    return new, "break-even" if candidates[-1] == new and moved >= cfg.be_r * risk else "trail"


def close_due(now: datetime, close_at: str, weekday: int) -> bool:
    """True once the session-close time has passed (no overnight holds).

    `weekday` must be 0-based (datetime.weekday(): Mon=0 ... Sun=6). Passing
    isoweekday() here made Friday(5) fail the `weekday < 5` test, so positions
    were never flattened on the one day a weekend gap hurts most.

    Weekend closes are left to `weekday` so a Saturday tick does not try to
    flatten positions the broker has already settled.
    """
    hh, mm = (int(x) for x in close_at.split(":"))
    return weekday < 5 and (now.hour, now.minute) >= (hh, mm)


def _atr(symbol: str) -> tuple[float, float] | None:
    """(atr, spread) in price units, or None. Uses the leak-safe fetch path."""
    import marketdata as md
    import MetaTrader5 as mt5

    inf = mt5.symbol_info(symbol)
    bars = md.fetch(symbol, tf="M15", bars=200)
    md.release_auto_selected()          # never leak scan symbols into Market Watch
    if inf is None or bars is None or len(bars["c"]) < 20:
        return None
    a = md.atr(bars["h"], bars["l"], bars["c"], 14)[-1]
    if a != a:                           # NaN guard
        return None
    return float(a), float(inf.spread) * float(inf.point)


def _close(mt5, pos, inf) -> tuple[bool, str]:
    """Flatten one position. Opposite deal, no new exposure."""
    tick = mt5.symbol_info_tick(pos.symbol)
    if tick is None:
        return False, "no tick"
    is_long = pos.type == 0
    price = tick.bid if is_long else tick.ask
    base = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": pos.symbol,
        "volume": pos.volume,
        "type": mt5.ORDER_TYPE_SELL if is_long else mt5.ORDER_TYPE_BUY,
        "position": pos.ticket,
        "price": price,
        "deviation": 20,
        "magic": MAGIC,
        "comment": "session-close",
    }
    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK):
        r = mt5.order_send({**base, "type_filling": filling})
        if r is not None and r.retcode == mt5.TRADE_RETCODE_DONE:
            return True, f"closed {pos.ticket} @ {price}"
    return False, f"close failed rc={getattr(r, 'retcode', '?')} {getattr(r, 'comment', '')}"


def run_once(cfg: Cfg, apply: bool, close_at: str | None) -> dict:
    """One supervision pass. Returns the state document the dashboard reads."""
    import MetaTrader5 as mt5

    import run_funnel as rf

    if not mt5.initialize():
        return {"ok": False, "error": f"initialize failed: {mt5.last_error()}"}
    try:
        problems = rf.preflight_check(mt5.terminal_info(), mt5.account_info())
        if problems:
            return {"ok": False, "error": "; ".join(problems)}

        acct = mt5.account_info()
        positions = mt5.positions_get() or []
        now = datetime.now(timezone.utc)
        due = bool(close_at) and close_due(now, close_at, now.weekday())
        actions: list[str] = []
        open_docs = []

        for p in positions:
            side = 1 if p.type == 0 else -1
            open_docs.append({
                "ticket": p.ticket, "symbol": p.symbol, "side": side,
                "volume": p.volume, "entry": p.price_open, "sl": p.sl,
                "price": p.price_current, "profit": round(p.profit, 2),
            })

            if due:
                if apply:
                    ok, msg = _close(mt5, p, mt5.symbol_info(p.symbol))
                    actions.append(("CLOSE " if ok else "CLOSE-FAIL ") + msg)
                else:
                    actions.append(f"would close {p.ticket} ({p.symbol}) session-close")
                continue

            env = _atr(p.symbol)
            if env is None:
                continue
            atr, spread = env
            new, reason = next_stop(side, p.price_open, p.sl, p.price_current,
                                    atr, spread, cfg)
            if new is None:
                continue
            if apply:
                r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP,
                                    "symbol": p.symbol, "position": p.ticket,
                                    "sl": round(new, 5), "tp": p.tp})
                ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
                actions.append(f"{'SL ' if ok else 'SL-FAIL '}{p.symbol} {p.sl} -> "
                               f"{round(new, 5)} ({reason})")
            else:
                actions.append(f"would set {p.symbol} sl {p.sl} -> {round(new, 5)} ({reason})")

        return {
            "ok": True,
            "generated_utc": now.isoformat(timespec="seconds"),
            "account": {"login": acct.login, "server": acct.server,
                        "balance": round(acct.balance, 2),
                        "equity": round(acct.equity, 2), "currency": acct.currency,
                        "leverage": acct.leverage},
            "positions": open_docs,
            "actions": actions,
            "session_close_due": due,
            "applied": apply,
        }
    finally:
        mt5.shutdown()


def write_state(doc: dict, attempts: int = 6) -> None:
    """Publish state for the dashboard.

    The replace is retried: on Windows os.replace fails with WinError 5 while
    another process holds the destination open, and the dashboard polls this
    file once a second. Losing that race used to kill the supervisor outright,
    which is the wrong failure mode - a monitoring problem must never stop the
    thing being monitored.
    """
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    for i in range(attempts):
        try:
            tmp.replace(STATE)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(0.05)


def log_line(text: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(f"{stamp} | {text}\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Continuous position supervisor")
    ap.add_argument("--interval", type=int, default=30, help="seconds between passes")
    ap.add_argument("--apply", action="store_true", help="send to the broker (default: dry run)")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--session-close", default=None, metavar="HH:MM",
                    help="UTC time to flatten (e.g. 20:45); omit to disable")
    ap.add_argument("--be-r", type=float, default=1.0)
    ap.add_argument("--trail-k", type=float, default=2.0)
    a = ap.parse_args()
    cfg = Cfg(be_r=a.be_r, trail_k=a.trail_k)

    a.apply, why = apply_allowed(a.apply)
    if not a.apply:
        print("DRY RUN -- nothing will be sent.")
        if why:
            print(f"REFUSED --apply: {why}.")
            print("Set ZTT_ALLOW_APPLY=1 in your own shell to enable it.")
        print("Use --apply (with that variable) to act.\n")

    if LOCK.exists() and time.time() - LOCK.stat().st_mtime < a.interval * 4:
        print(f"another supervisor is running (lock {LOCK.name})")
        return 0
    LOCK.write_text(str(time.time()))

    try:
        while True:
            # Refresh the lock every tick. It used to be written once and then
            # judged stale after interval*4, so at --interval 5 a second
            # supervisor could start 20s into the run and both could move stops.
            LOCK.write_text(str(time.time()))
            doc = run_once(cfg, a.apply, a.session_close)
            try:
                write_state(doc)
            except OSError as e:
                # Monitoring is allowed to fail; supervision is not.
                log_line(f"STATE WRITE FAILED {e}")
            if not doc.get("ok"):
                log_line(f"FAILED {doc.get('error')}")
                print(f"! {doc.get('error')}")
            else:
                for act in doc["actions"]:
                    print(act)
                    log_line(act)
                if not doc["actions"]:
                    print(f"{doc['generated_utc']}  {len(doc['positions'])} open, "
                          f"equity {doc['account']['equity']} {doc['account']['currency']}  idle")
            if a.once:
                return 0 if doc.get("ok") else 1
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        LOCK.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
