"""One file stops every automated path.

`KILL_SWITCH` at the tree root, or `ZTT_KILL_SWITCH=1` in the environment, freezes
both the scan and the supervisor. It is checked at the start of every unit of work,
not once at start-up, because the whole point is to stop the NEXT cycle.

Who may create it: a human, with `touch KILL_SWITCH` (or the environment). The
dashboard never writes it - a monitor that can arm or disarm the system is a
control surface, and this repository has exactly one write already.

Exit code 78 is the supervisor's answer (EX_CONFIG): a human has stopped it, so
restarting it is the wrong response.
"""

from __future__ import annotations

import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = HERE / "KILL_SWITCH"
EXIT_KILLED = 78


def active(root: Path | None = None, environ: dict | None = None) -> str | None:
    """Why automation is frozen, or None. Never guesses: absence is not consent."""
    env = os.environ if environ is None else environ
    if str(env.get("ZTT_KILL_SWITCH", "")).strip() == "1":
        return "ZTT_KILL_SWITCH=1 in the environment"
    marker = (root / "KILL_SWITCH") if root else MARKER
    if marker.exists():
        return f"{marker.name} exists at {marker.parent}"
    return None
