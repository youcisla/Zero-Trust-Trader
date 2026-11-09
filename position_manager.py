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
import atexit
import json
import math
import os
import time

import audit
import process_lock
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
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

    The environment variable is an operational interlock, not independent
    consent: a process able to set its environment can set this variable.
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
    if (side not in (-1, 1) or not all(math.isfinite(x) for x in
            (entry, sl, price, atr, spread, cfg.be_r, cfg.trail_k,
             cfg.initial_k, cfg.min_step_atr)) or
            entry <= 0 or price <= 0 or sl < 0 or spread < 0 or
            cfg.be_r < 0 or cfg.trail_k <= 0 or cfg.initial_k <= 0 or cfg.min_step_atr < 0):
        return None, "invalid risk inputs"
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

    try:
        bars = md.fetch(symbol, tf="M15", bars=200)
        inf = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
    finally:
        md.release_auto_selected()
    if inf is None or bars is None or len(bars["c"]) < 20:
        return None
    a = md.atr(bars["h"], bars["l"], bars["c"], 14)[-1]
    if not math.isfinite(a) or tick is None:
        return None
    spread = md.tick_spread(tick, bars.get("offset", 0))
    if spread is None:
        return None
    return float(a), spread


def _close(mt5, pos, inf) -> tuple[bool, str]:
    """Close observed exposure; never blindly retry an uncertain/partial fill."""
    import run_funnel as rf
    import marketdata as md

    if pos.type not in (0, 1) or not math.isfinite(pos.volume) or pos.volume <= 0:
        return False, "invalid position snapshot"

    for filling in (mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_FOK):
        problems = rf.preflight_check(mt5.terminal_info(), mt5.account_info())
        if problems:
            return False, "close refused: " + "; ".join(problems)
        current = mt5.positions_get(ticket=pos.ticket)
        if current is None:
            return False, "close observation failed"
        if not current:
            return True, f"flat confirmed {pos.ticket}"
        fresh = current[0]
        if (fresh.type != pos.type or fresh.symbol != pos.symbol or
                not math.isfinite(fresh.volume) or fresh.volume <= 0):
            return False, "position changed; close refused"
        if filling == mt5.ORDER_FILLING_FOK and fresh.volume != pos.volume:
            return False, "position volume changed; reconcile on the next pass"
        tick = mt5.symbol_info_tick(pos.symbol)
        if md.tick_spread(tick, md.broker_offset()) is None:
            return False, "unusable close quote"
        price = tick.bid if fresh.type == 0 else tick.ask
        if not math.isfinite(price) or price <= 0:
            return False, "invalid close quote"
        r = mt5.order_send({
            "action": mt5.TRADE_ACTION_DEAL, "symbol": fresh.symbol,
            "volume": min(pos.volume, fresh.volume),
            "type": mt5.ORDER_TYPE_SELL if fresh.type == 0 else mt5.ORDER_TYPE_BUY,
            "position": fresh.ticket, "price": price, "deviation": 20,
            "magic": MAGIC, "comment": "session-close", "type_filling": filling,
        })
        rc = getattr(r, "retcode", None)
        if rc == getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030) and filling == mt5.ORDER_FILLING_IOC:
            continue
        remaining = mt5.positions_get(ticket=pos.ticket)
        if remaining is None:
            return False, f"close rc={rc}; remaining exposure unknown"
        if not remaining:
            return True, f"flat confirmed {pos.ticket}"
        return False, f"close rc={rc}; residual exposure remains, no blind retry"
    return False, "close filling mode unsupported"


def wire_stop(side: int, old: float, candidate: float, price: float, inf) -> float | None:
    """Quantize protectively, then check the exact stop sent to the broker."""
    step = getattr(inf, "trade_tick_size", 0) or getattr(inf, "point", 0)
    point = getattr(inf, "point", 0)
    levels = (getattr(inf, "trade_stops_level", 0), getattr(inf, "trade_freeze_level", 0))
    if (side not in (-1, 1) or not all(math.isfinite(x) for x in
            (old, candidate, price, step, point, *levels)) or
            step <= 0 or point <= 0 or old < 0 or candidate <= 0 or price <= 0):
        return None
    rounding = ROUND_CEILING if side > 0 else ROUND_FLOOR
    tick = Decimal(str(step))
    new = float((Decimal(str(candidate)) / tick).to_integral_value(rounding=rounding) * tick)
    distance = max(*levels, 0) * point
    if ((side > 0 and (new >= price or (old and new <= old) or price - new < distance)) or
            (side < 0 and (new <= price or (old and new >= old) or new - price < distance))):
        return None
    return new


_MT5_READY = False


def ensure_mt5(mt5) -> None:
    """Hold ONE MetaTrader5 session for the life of the process.

    run_once used to connect and disconnect every tick. At --interval 5 that is
    17,280 connect cycles a day, racing the funnel's own connect every 15
    minutes, and it is what produced the -10005 IPC timeouts. Reconnect only
    when the terminal is actually gone.
    """
    global _MT5_READY
    if _MT5_READY and mt5.terminal_info() is not None:
        return
    if not mt5.initialize():
        _MT5_READY = False
        raise RuntimeError(f"initialize failed: {mt5.last_error()}")
    _MT5_READY = True


def run_once(cfg: Cfg, apply: bool, close_at: str | None) -> dict:
    """One supervision pass. Returns the state document the dashboard reads."""
    if (HERE / ".ztt-maintenance").exists():
        return {"ok": False, "error": "maintenance in progress"}
    import MetaTrader5 as mt5

    import run_funnel as rf

    try:
        ensure_mt5(mt5)
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        problems = rf.preflight_check(mt5.terminal_info(), mt5.account_info())
        if problems:
            return {"ok": False, "error": "; ".join(problems)}

        acct = mt5.account_info()
        if acct is None:
            return {"ok": False, "error": "account observation failed"}
        positions = mt5.positions_get()
        if positions is None:
            return {"ok": False, "error": f"positions_get failed: {mt5.last_error()}"}
        now = datetime.now(timezone.utc)
        due = bool(close_at) and close_due(now, close_at, now.weekday())
        actions: list[str] = []
        errors: list[str] = []
        open_docs = []

        for p in positions:
            if p.type not in (0, 1) or not math.isfinite(p.volume) or p.volume <= 0:
                errors.append("unusable position type or volume")
                continue
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
                    if not ok:
                        errors.append(msg)
                else:
                    actions.append(f"would close {p.ticket} ({p.symbol}) session-close")
                continue

            env = _atr(p.symbol)
            if env is None:
                errors.append(f"{p.symbol}: risk inputs unavailable")
                continue
            atr, spread = env
            new, reason = next_stop(side, p.price_open, p.sl, p.price_current,
                                    atr, spread, cfg)
            if new is None:
                continue
            if apply:
                problems = rf.preflight_check(mt5.terminal_info(), mt5.account_info())
                fresh = mt5.positions_get(ticket=p.ticket)
                tick = mt5.symbol_info_tick(p.symbol)
                inf = mt5.symbol_info(p.symbol)
                if problems or fresh is None or not fresh or tick is None or inf is None:
                    errors.append(f"{p.symbol}: fresh observation refused stop update")
                    continue
                import marketdata as md
                if md.tick_spread(tick, md.broker_offset()) is None:
                    errors.append(f"{p.symbol}: unusable quote before stop update")
                    continue
                current = fresh[0]
                if current.type != p.type or current.symbol != p.symbol:
                    errors.append(f"{p.symbol}: position changed before stop update")
                    continue
                if not math.isfinite(current.tp) or current.tp < 0:
                    errors.append(f"{p.symbol}: unusable take-profit observation")
                    continue
                price = tick.bid if side > 0 else tick.ask
                new, reason = next_stop(side, current.price_open, current.sl, price,
                                        atr, tick.ask - tick.bid, cfg)
                new = wire_stop(side, current.sl, new, price, inf) if new is not None else None
                if new is None:
                    continue
                r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP,
                                    "symbol": p.symbol, "position": p.ticket,
                                    "sl": new, "tp": current.tp})
                ok = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
                actions.append(f"{'SL ' if ok else 'SL-FAIL '}{p.symbol} {p.sl} -> "
                               f"{new} ({reason})")
                if not ok:
                    errors.append(f"{p.symbol}: stop update failed")
            else:
                actions.append(f"would set {p.symbol} sl {p.sl} -> {round(new, 5)} ({reason})")

        return {
            "ok": not errors,
            "error": "; ".join(errors) if errors else None,
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
        # The terminal session is held open between ticks on purpose: closing
        # and reopening it every 5 seconds is what caused the -10005 IPC
        # timeouts. Shutdown is registered once, at process exit.
        pass


def write_state(doc: dict, keep: int = 2) -> bool:
    """Publish state for the dashboard. Returns whether it landed.

    Written as a NEW generation, never over the file a reader has open:

        .engine_state.<pid>.tmp  ->  engine_state.<epoch_ms>.json

    os.replace onto an existing name fails with WinError 5 whenever anything
    holds the destination - the dashboard polls, and the antivirus scans what
    was just created. Retrying around that only made the collision rarer; it
    never made it impossible. Renaming onto a name that does not exist yet
    cannot be refused, because no process can hold a handle on a file that was
    never created. The reader resolves the newest generation instead.

    Old generations are pruned best-effort: a reader mid-read of the previous
    one must not turn cleanup into an error.
    """
    tmp = HERE / f".engine_state.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    # Nanoseconds, not milliseconds: two writes inside the same tick must not
    # race for the same name, and a rename onto an existing file is exactly the
    # failure this design exists to avoid.
    target = HERE / f"engine_state.{time.time_ns()}.json"
    try:
        os.rename(tmp, target)          # create, never replace
    except OSError:
        tmp.unlink(missing_ok=True)
        return False

    stale = sorted(HERE.glob("engine_state.*.json"),
                   key=lambda p: p.stat().st_mtime_ns)[:-keep]
    for old in stale:
        try:
            old.unlink()
        except OSError:
            pass
    return True


def acquire_lock(max_age_s: float, path: Path | None = None) -> bool:
    """Hold process-lifetime OS ownership, including during slow broker calls."""
    return process_lock.acquire(path or LOCK, max_age_s)


def release_lock(path: Path | None = None) -> None:
    process_lock.release(path or LOCK)


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

    if (a.interval <= 0 or not math.isfinite(a.be_r) or a.be_r < 0 or
            not math.isfinite(a.trail_k) or a.trail_k <= 0):
        ap.error("interval/trail-k must be positive and be-r nonnegative")
    if a.session_close:
        try:
            datetime.strptime(a.session_close, "%H:%M")
        except ValueError:
            ap.error("--session-close must be HH:MM in UTC")

    if (HERE / ".ztt-maintenance").exists():
        print("REFUSED: maintenance in progress; no terminal connection or actions")
        return 2
    cfg = Cfg(be_r=a.be_r, trail_k=a.trail_k)

    a.apply, why = apply_allowed(a.apply)
    if not a.apply:
        print("DRY RUN -- nothing will be sent.")
        if why:
            print(f"REFUSED --apply: {why}.")
            print("Set ZTT_ALLOW_APPLY=1 in your own shell to enable it.")
        print("Use --apply (with that variable) to act.\n")

    if not acquire_lock(a.interval * 4):
        print(f"another supervisor is running (lock {LOCK.name})")
        return 0
    log_line(f"supervisor up | interval={a.interval}s | apply={a.apply} | "
             f"session-close={a.session_close}")

    last_error = None
    state_stalled = False
    try:
        import MetaTrader5 as _mt5

        atexit.register(_mt5.shutdown)
    except ImportError:
        pass
    try:
        while True:
            # OS ownership survives slow passes without an expiring heartbeat.
            if (HERE / ".ztt-maintenance").exists():
                return 2
            doc = run_once(cfg, a.apply, a.session_close)
            try:
                published = write_state(doc)
            except OSError as e:
                # Monitoring is allowed to fail; supervision is not.
                published = False
                if not state_stalled:
                    log_line(f"STATE WRITE FAILED {e}")
            if not published:
                if not state_stalled:
                    state_stalled = True
                    log_line("STATE WRITE deferred - engine_state.json is held open; "
                             "retrying next tick")
            elif state_stalled:
                state_stalled = False
                log_line("STATE WRITE recovered")
            if not doc.get("ok"):
                err = str(doc.get("error"))
                # One line per state change, not one per tick. A failing
                # pre-flight used to repeat every 5s and bury the log: 13
                # identical rows in a minute hid everything around them.
                if err != last_error:
                    last_error = err
                    log_line(f"FAILED {err}")
                    print(f"! {err}")
                    audit.write("refused", stage="supervisor", problems=[err])
            else:
                if last_error is not None:
                    last_error = None
                    log_line("RECOVERED pre-flight passed again")
                    print("recovered")
                for act in doc["actions"]:
                    print(act)
                    log_line(act)
                    audit.write("action", detail=act)
                if not doc["actions"]:
                    print(f"{doc['generated_utc']}  {len(doc['positions'])} open, "
                          f"equity {doc['account']['equity']} {doc['account']['currency']}  idle")
            if a.once:
                return 0 if doc.get("ok") else 1
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
