"""Read deal history into a new immutable, explicitly limited CSV snapshot.

The canonical trades.csv is never rewritten or maintained by this utility.
The price-only botlab format cannot faithfully represent partial/multiple fills,
reversals or nonzero fees/swap; those shapes are refused rather than flattened.
No order function is available here. Account observations must match the demo pin.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import tempfile
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import MetaTrader5 as mt5

import stops

HERE = Path(__file__).resolve().parent
FIELDS = ["closed", "symbol", "side", "entry", "exit", "stop", "hold_hours"]
DEAL_ENTRY_IN, DEAL_ENTRY_OUT, DEAL_TYPE_BUY = 0, 1, 0


class ExportError(ValueError):
    """History cannot be represented truthfully by this export contract."""


def _number(value, name: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ExportError(f"invalid {name}") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise ExportError(f"invalid {name}")
    return result


def _price(value) -> str:
    return format(_number(value, "price", positive=True), "f")


def closed_rows(deals, symbol: str, stop_map: dict, offset: int) -> list[dict]:
    """Represent only fully closed, single-entry/single-exit, zero-cost positions.

    Missing entry history and unsupported shapes fail the complete export. An
    entry with no exits remains open and contributes no completed row.
    """
    legs = defaultdict(list)
    for deal in deals:
        if deal.symbol == symbol:
            legs[deal.position_id].append(deal)
    rows = []
    for position_id, records in legs.items():
        if any(d.entry not in (DEAL_ENTRY_IN, DEAL_ENTRY_OUT) for d in records):
            raise ExportError("reversal/close-by history requires volume-aware reconstruction")
        entries = [d for d in records if d.entry == DEAL_ENTRY_IN]
        exits = [d for d in records if d.entry == DEAL_ENTRY_OUT]
        if not exits:
            continue
        if len(entries) != 1 or len(exits) != 1:
            raise ExportError("missing entry or multiple fills cannot be represented by the price-only format")
        opened, closed = entries[0], exits[0]
        volume_in = _number(opened.volume, "entry volume", positive=True)
        volume_out = _number(closed.volume, "exit volume", positive=True)
        if volume_in != volume_out:
            raise ExportError("partial/residual position cannot be represented as a closed trade")
        if opened.type not in (0, 1) or closed.type != 1 - opened.type:
            raise ExportError("inconsistent entry/exit directions")
        for record in records:
            for field in ("commission", "swap", "fee"):
                if _number(getattr(record, field, 0), field) != 0:
                    raise ExportError("nonzero fees/swap require a cost-aware export format")
        start = float(_number(opened.time, "entry time", positive=True))
        end = float(_number(closed.time, "exit time", positive=True))
        if not math.isfinite(start) or not math.isfinite(end) or end < start:
            raise ExportError("invalid chronological deal history")
        stop = stop_map.get(str(position_id), "")
        if stop:
            stop = _price(stop)
        rows.append({
            "closed": datetime.fromtimestamp(end - offset, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "symbol": symbol,
            "side": "long" if opened.type == DEAL_TYPE_BUY else "short",
            "entry": _price(opened.price), "exit": _price(closed.price),
            "stop": stop, "hold_hours": f"{(end - start) / 3600:.4f}",
        })
    return sorted(rows, key=lambda row: row["closed"])


def write_export(output: Path, rows: list[dict]) -> None:
    """Exclusive creation prevents concurrent or repeated exports from overwriting."""
    if output.resolve() == (HERE / "trades.csv").resolve():
        raise ExportError("canonical trades.csv is append-only; choose a new snapshot path")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".trade-export-", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard link publishes the complete file atomically and refuses an
        # existing name on both Windows and POSIX. Never use overwriting replace.
        os.link(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--out", default=None, help="new immutable snapshot path; existing files are refused")
    args = parser.parse_args(argv)
    if not 1 <= args.days <= 3650:
        parser.error("--days must be between 1 and 3650")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(args.out) if args.out else HERE / "exports" / f"trades-{stamp}-{uuid.uuid4().hex[:12]}.csv"
    if output.exists() or output.resolve() == (HERE / "trades.csv").resolve():
        print("export refused: choose a new snapshot; existing/canonical ledger is preserved", file=sys.stderr)
        return 2
    selected = False
    try:
        expected = os.environ.get("MT5_EXPECTED_LOGIN", "")
        if not expected.isdecimal() or int(expected) <= 0:
            raise ExportError("MT5_EXPECTED_LOGIN must pin the demo account")
        if not mt5.initialize():
            raise ExportError("terminal initialization failed")
        try:
            account, terminal = mt5.account_info(), mt5.terminal_info()
            if not account or not terminal or not terminal.connected:
                raise ExportError("account/terminal observation unavailable")
            if account.login != int(expected) or "demo" not in account.server.lower():
                raise ExportError("demo account identity does not match the pin")
            info = mt5.symbol_info(args.symbol)
            if info is None:
                raise ExportError("symbol unavailable")
            selected = not info.visible
            if not mt5.symbol_select(args.symbol, True):
                raise ExportError("symbol selection failed")
            tick = mt5.symbol_info_tick(args.symbol)
            if tick is None or not getattr(tick, "time", 0):
                raise ExportError("timestamp calibration unavailable")
            offset = round((tick.time - time.time()) / 1800) * 1800
            now = datetime.now(timezone.utc)
            deals = mt5.history_deals_get(now - timedelta(days=args.days), now + timedelta(days=1))
            if deals is None:
                raise ExportError("deal history unavailable; no export was written")
            rows = closed_rows(deals, args.symbol, stops.load(), offset)
        finally:
            if selected:
                if not mt5.symbol_select(args.symbol, False):
                    print("warning: symbol selection cleanup failed", file=sys.stderr)
            mt5.shutdown()
        if not rows:
            print("no fully closed representable trades; no export written")
            return 1
        write_export(output, rows)
        print(f"wrote {len(rows)} trade(s) to a new immutable snapshot: {output.name}")
        if any(not row["stop"] for row in rows):
            print("warning: missing initial stops; these rows cannot establish R", file=sys.stderr)
        return 0
    except (ExportError, OSError, ValueError, AttributeError, TypeError) as exc:
        print(f"export refused: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
