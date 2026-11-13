---
description: Read-only audit of this repository against AGENTS.md. Use when asked to audit, review for defects, or check that the gates still match what the docs claim. Writes one findings file and changes nothing else.
allowed-tools: Read, Grep, Glob, Bash, Write
---

# Audit

Read-only except for one file: `docs/audit-<YYYY-MM-DD>-cli.md`, findings only.
No edits, no commits, no pushes, no broker calls, no order placement.

## Before anything

Read `AGENTS.md` in full, then `CLAUDE.md`. `AGENTS.md` §11 governs: when you
cannot verify something, say so and stop.

## Gates first, and record the real numbers

```
python -m unittest discover -s tests
python tools/check_docs.py
python tools/check_public.py
```

```
cd dashboard && cargo fmt --check && cargo clippy --offline --all-targets -- -D warnings && cargo test --offline --release
```

```
node --test dashboard/tests/ui.test.cjs
```

A repository whose gates disagree with its README is the finding that makes
every other number suspect, so run these before reading any other code.

## Then audit, in this order

1. Money path — `position_manager.py`, `run_funnel.py`: any path that moves a
   stop the wrong way, acts on a position it cannot identify, or treats an
   unreadable state file as flat.
2. Tests that cannot fail — assertions on mocks, skips on a missing binary,
   `except: pass`, anything swallowing an order error.
3. Gates that leak — Bonferroni and permutation wiring, `trials_total`
   accounting, any threshold read from a file runtime state could rewrite.
4. Numbers in docs against artifacts — `tier3_results.json`,
   `funnel_history.log`, `liquid_universe.json`. Never against memory.
5. Dashboard — can anything write, does missing data render as 0 instead of
   unknown, does the scan button claim a scan that did not run.

Stop at eight real findings.

## What counts as a finding

`file:line`, the command you ran, and its actual output. A finding without a
reproduction command is not a finding — drop it, do not soften it into a
suggestion. Mark each claim verified, false, partially true, or not verifiable,
and put what you could not run under "Not verified" with the reason. Rank by
probability the claim is real times its impact, state what would prove you
wrong, and re-trace every claim yourself before writing it down.

## Output

`docs/audit-<YYYY-MM-DD>-cli.md`: a table of `id | area | severity | P(real) |
evidence | why it matters | the fix in one line`, at most three lines of
reproduction per HIGH finding, then `## Contradictions` and `## Not verified`.
Cap at 1,500 words. If the tree is clean, say it is clean in one paragraph and
stop — inventing a finding to look useful is the failure this repository exists
to prevent.
