# The research lane

Two external readers are sanctioned for research, and for nothing else:
`agent-reach` (platform content — Reddit, GitHub, X, YouTube transcripts,
Xueqiu quotes) and `last30days` (a 30-day sweep across platforms, with a
competitor mode). A human runs them, and their output lands in
`docs/PRIOR-ART.md` or `docs/ALTERNATIVES.md` — the same two files every other
external read has landed in, not a third destination.

## What each tool is for

| Tool | Answers | Output lands |
| --- | --- | --- |
| `agent-reach` | what people actually say on a platform: threads, issues, videos, quotes | `docs/PRIOR-ART.md`, or `docs/ALTERNATIVES.md` when the question is "what else exists" |
| `last30days` | what changed in the last 30 days across platforms, with coverage and freshness reported | the same two files; the header (items, sources, how many are fresh) is quoted, not summarised away |

Use them for prior art, alternatives and hypothesis scouting — the reading that
comes before a family or a tool exists. Section 5 of `docs/PRIOR-ART.md` is a
worked example: a `last30days` sweep returned 24 items across 3 active sources,
Reddit was rate-limited, and the thin result was the finding.

## The hard line

- **Never inside a scan.** A scan must not depend on a network call or a model's
  mood (`AGENTS.md` §9b); the 15-minute cadence in `run_funnel.py` stays local.
- **Never feeds a gate.** External chatter cannot establish an edge; only the
  cumulative trial gate can (`AGENTS.md` §1, §3).
- **Never touches execution.** There is no code path from a research read to an
  order — execution is a human typing `APPROVED` plus the bridge token
  (`AGENTS.md` §1).

The fence is enforced, not advised. `tests/test_research_lane_offline.py` scans
the seven modules that can place, size or gate an order — `run_funnel.py`,
`tier1_universe.py`, `tier2_patterns.py`, `tier3_deep.py`,
`position_manager.py`, `desk.py`, `agent_brief.py` — and fails if either tool's
name appears in any of them, including inside a `subprocess` or `os.system`
argument. `desk.py`'s scout deliberately does not call either tool: it already
escalated 34 of 45 runs on noisy input, and a scheduled 15-minute job must not
depend on a login-bearing CLI.

```bash
grep -REn 'agent-reach|last30days' run_funnel.py tier1_universe.py tier2_patterns.py tier3_deep.py position_manager.py desk.py agent_brief.py   # prints nothing
python -m unittest tests.test_research_lane_offline -v
```
