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
from pathlib import Path

STOPS = Path(__file__).resolve().parent / "stops.json"


def load(path: Path = STOPS) -> dict[str, str]:
    return json.loads(path.read_text()) if path.exists() else {}


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
    ticket = str(position.get("ticket"))
    data = load(path)
    data[ticket] = str(sl)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return data[ticket]


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
