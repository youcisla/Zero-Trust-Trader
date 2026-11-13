---
paths:
  - "run_funnel.py"
  - "position_manager.py"
  - "tier3_deep.py"
  - "policy_check.py"
  - "timemachine_mt5.py"
  - "trials_total.py"
---

# Money path — read `AGENTS.md` before you edit

This file is a pointer, not a second copy. **`AGENTS.md` is the authority and
wins on every disagreement.** It is too long to sit in context all session, so
this rule loads when you touch one of the six files above.

Read, in this order: §1 (the invariant), §3 (the eight fail-closed gates), §4
(the ten things an agent never does), and then the section that owns the file —
§7 for a strategy, §8 for the supervisor.

The four prohibitions most often violated by accident:

- Do not lower a significance threshold, relax `permutation p < 0.05`, or
  change `bonferroni_z` to make a candidate pass. A candidate that fails, fails.
- Do not edit `LEGACY_TRIALS`. It is frozen history.
- Do not delete or rewrite `funnel_history.log`, `trades.csv`, or a run that did
  not work out. Losers stay in the ledger.
- Do not add `--apply` to a launcher or a scheduled task. The supervisor runs
  dry until a real position is open.

Two of these are also enforced mechanically, and a refusal from either is a
result rather than an obstacle: `permissions.deny` in `.claude/settings.json`,
and the `PreToolUse` guard at `.claude/hooks/gate_guard.py`. If a guard blocks
you, report it; do not look for a route around it.

`position_manager.py` may only reduce risk. Any change must keep `next_stop()`
returning `None` rather than a stop that moves away from price, and
`tests/test_position_manager.py` is the test that says so — if it fails, the
change is wrong, not the test.
