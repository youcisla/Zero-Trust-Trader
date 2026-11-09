"""Single source of truth for the trade->stop map used by the ledger export.

`stops.json` exists because MT5 deal records do not carry the stop (it lived on
the order we sent). Every order path must call `record()` with the position MT5
actually opened, so the map can never drift from reality: the stop is read back
off the open position itself, never from the request we intended to send.

Keys are position tickets, which for a market order in hedging mode equal the
order ticket the bridge returns.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import process_lock

STOPS = Path(__file__).resolve().parent / "stops.json"


def load(path: Path = STOPS) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(data, dict):
        raise ValueError("stop map must be an object")
    for ticket, value in data.items():
        _validate(ticket, value)
    return {ticket: str(value) for ticket, value in data.items()}


def _validate(ticket, value) -> None:
    if not str(ticket).isdigit() or int(ticket) <= 0:
        raise ValueError("stop map requires a positive position ticket")
    try:
        stop = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError("stop must be finite and positive") from exc
    if not stop.is_finite() or stop <= 0:
        raise ValueError("stop must be finite and positive")


def record(position: dict, path: Path = STOPS) -> str | None:
    """Persist position['sl'] under its ticket. Returns the stop written, or None.

    Reads the stop back off the opened position rather than the order request,
    so a broker-side adjustment or a rejected SL is captured truthfully. A
    position with no stop (sl 0/None) is deliberately NOT recorded -- writing
    "0" would make the exporter claim a stop that does not exist.
    """
    sl = position.get("sl")
    if sl in (None, "", 0, "0", "0.0", 0.0):
        return None
    try:
        if Decimal(str(sl)) == 0:
            return None
    except InvalidOperation as exc:
        raise ValueError("stop must be finite and positive") from exc
    ticket = str(position.get("ticket"))
    _validate(ticket, sl)
    lock = path.with_suffix(path.suffix + ".lock")
    deadline = time.monotonic() + 5
    while not process_lock.acquire(lock, 0):
        if time.monotonic() >= deadline:
            raise RuntimeError("stop map is busy; no update written")
        time.sleep(0.01)
    temporary = None
    try:
        data = load(path)
        data[ticket] = str(sl)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".stops.", suffix=".tmp", delete=False) as fh:
            temporary = Path(fh.name)
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temporary, path)
        return data[ticket]
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        process_lock.release(lock)


if __name__ == "__main__":
    # Self-check: a recorded stop round-trips, and an absent stop is NOT recorded
    # (writing "0" would make the ledger claim a stop the broker never set).
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "stops.json"
        # The bridge sends `sl` as a STRING; that is the shape that must round-trip.
        assert record({"ticket": 111, "sl": "1.12090"}, p) == "1.12090"
        assert record({"ticket": 222, "sl": 0.0}, p) is None
        assert record({"ticket": 333, "sl": None}, p) is None
        assert record({"ticket": 444, "sl": "0"}, p) is None
        assert load(p) == {"111": "1.12090"}, load(p)
        assert record({"ticket": 111, "sl": "1.12100"}, p) == "1.12100"
        assert load(p)["111"] == "1.12100", "update must overwrite, not duplicate"
        # A float stop still records (formatting is Python's, not a crash).
        assert record({"ticket": 555, "sl": 1.1210}, p) == "1.121"
    print("stops.py self-check OK")
