"""Forward to the workspace harness guard, when this tree sits inside one.

The Router/Worker gates live one level up, at ``<workspace>/scripts/hooks/guard.py``,
because they police several repositories at once. This repository is also
cloned on its own, and there a missing guard must not turn into a blocked
edit: no workspace means no Router to police, so the hook stands down.
"""

import runpy
import sys
from pathlib import Path

TARGET = Path(__file__).resolve().parents[3] / "scripts" / "hooks" / "guard.py"

if not TARGET.exists():
    sys.exit(0)

sys.argv = [str(TARGET), *sys.argv[1:]]
runpy.run_path(str(TARGET), run_name="__main__")
