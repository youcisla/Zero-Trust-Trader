---
description: Five-second readiness check for the trading tree. Use before a scan, after a config change, or when the dashboard shows an amber age badge or an engine refusal. Runs the funnel's own pre-flight and the document gate, reports the refusals, and fixes nothing.
allowed-tools: Bash, Read
---

# Pre-flight

Read-only. Never scan, never take the lock, never touch the terminal beyond the
one-symbol bar fetch the pre-flight itself performs, and never fix what you find
unless asked in the same turn.

## Run, in this order

```
python run_funnel.py --preflight
```

That answers, in about 0.04 s: the kill switch, the trial chain, the terminal,
the broker offset and one closed bar. It takes no lock and writes nothing. Exit
code 78 means a human froze the system — report that and stop.

```
python tools/check_docs.py
python tools/check_public.py
```

## If `ModuleNotFoundError: numpy` appears

The interpreter on `PATH` has no dependencies installed. That is an environment
fault, not a finding: say which command failed and stop. `requirements-dev.txt`
installs what the tree needs.

## Report

One line per refusal, with the exact text the command printed. Then the single
line that matters: `PREFLIGHT OK` or `PREFLIGHT REFUSED`.

A refusal is a correct outcome — `STAND_DOWN` is the system working. Do not
"make it pass". `AGENTS.md` §11 applies: when you are unsure, do nothing and say
what you could not verify.
