# Audit of 2026-11-12 — verification first, and what survived it

An external auditor (Gemini Argon) produced a 15,000-word plan against `be3f139`
**without a filesystem, shell or repository mount** — its own Phase 0.0 says so
and asks the executing agent to run the checks it could not. This is that pass.

It found one real gap. Nine of its ten "defects" were already implemented, in
several cases with a comment naming the exact failure it describes.

## The five baseline claims

| # | Claim | Verdict | Evidence |
| --- | --- | --- | --- |
| VM-01 | HEAD is `be3f139` and CI is green | **true** | `git rev-parse --short HEAD` → `be3f139`; run `be3f139 completed success` |
| VM-02 | 31 node tests pass | **true** | `node --test tests/ui.test.cjs` → `ℹ pass 31` |
| VM-03 | 42 Rust tests pass | **true** | `cargo test --release` → `42 passed` |
| VM-04 | 335 Python tests pass | **true** | `unittest discover -s tests` → `Ran 335 tests … OK` |
| VM-05 | ruff, clippy, fmt, check_docs, check_public green | **true** | all five exit 0 |

## The ten defects

| # | Claim | Verdict | Evidence |
| --- | --- | --- | --- |
| DEF-01 | Forming bars reach indicators (look-ahead) | **false** | `marketdata.py:121` — `keep = [r for r in rates if int(r["time"]) + secs <= server_now]`. The module docstring names the failure and the fix |
| DEF-02 | No OHLC / gap / spread validation | **partly true** | OHLC and non-finite checks exist at `marketdata.py:134`; a redundancy check that would flag a gap is not there |
| DEF-03 | `--dry-run` and `--apply` diverge | **false** | one `run_once(cfg, apply, close_at)` (`position_manager.py:246`); `apply` only gates the send (`:294`, `:312`), and `apply_allowed()` (`:58`) requires an environment variable on top of the flag |
| DEF-04 | Exit priority ambiguous, stops can widen | **false** | `next_stop()` (`:70`) is the single pure direction rule, property-swept over 3,000 random positions; `be_r=1.0`, `trail_k=2.0` are configuration, not code |
| DEF-05 | IOC/FOK and partial fills unhandled | **false** | `:167` tries IOC then FOK, retries on `TRADE_RETCODE_INVALID_FILL`, and compares `fresh.volume != pos.volume` before treating a close as complete |
| DEF-06 | Same-bar fills optimistic, costs unmodeled | **false** | `_resolve` (`timemachine_mt5.py:117`) — "Stop before target"; `_resolve_live` (`:145`) charges one full spread round trip on bid prices and reuses the live `next_stop` |
| DEF-07 | Break-even chokes winners | **untested claim** | `be_r=1.0` means the stop moves at +1R, not inside the noise band the auditor describes. Whether 1.0R is optimal is a measurement, not a defect; `tools/exit_policy_ab.py` is where it would be measured |
| DEF-08 | Correlated exposure uncapped | **partly true, deliberately** | correlation dedupe exists at selection time (`make_liquid.py:119`, `tier2_patterns`); there is no cap across *open* positions because this system opens at most one — a portfolio cap over a portfolio of one is decoration |
| DEF-09 | CLI/supervisor defaults disagree with docs | **false** | `apply_allowed()` refuses `--apply` without `ZTT_ALLOW_APPLY`; the supervisor is not scheduled at all (`AGENTS.md` §12) |
| DEF-10 | Desk UI has no authority badge, ignores schema | **partly true** | `protectionLabel` already distinguishes "apply enabled" from "observed only · dry run"; `schema_version` and `/api/meta` now exist. The state file carries `applied`, so the label was never lying |

## What was actually missing, and is now in the tree

**A kill switch.** The plan called for one in §0.3 and it did not exist — the only
way to stop the system was to unschedule it or kill the process.

| File | What it does |
| --- | --- |
| `kill_switch.py` | `active(root, environ)` → the reason automation is frozen, or `None`. `KILL_SWITCH` at the tree root, or `ZTT_KILL_SWITCH=1`. Exit code 78 is the answer, so a restart is not a fix |
| `run_funnel.py` | refuses before the terminal, the lock or the ledger is touched — a frozen scan must not spend 35 minutes rewriting the universe |
| `position_manager.py` | checked at the top of every pass, ahead of the maintenance marker; a killed pass publishes why and the loop exits 78 instead of spinning |
| `dashboard/src/main.rs` | the snapshot carries `kill_switch`, and a test asserts the monitor **never creates one** — a monitor with a switch is a control surface |
| `dashboard/web/index.html` | one red strip cell, "ALL AUTOMATION FROZEN", and nothing at all when the switch is off |
| `tests/test_kill_switch.py` | absence is not consent; the environment form; a killed pass publishes `positions: null` and never imports the terminal |

## What this audit did not attempt

The plan's Phase 1 (walk-forward experiments EXP-01…EXP-05, win-rate
rehabilitation) and Phase 3 (golden fixtures, structured logging). Those need
measured runs over the real bar history, not code edits, and their premises —
five of the seven hypotheses — depend on defects that the verification above
found to be already fixed. Running them against a correct baseline is a research
task with its own budget.

The win rate itself is not a defect to be patched. `AGENTS.md` §10 records why:
the live exit path has no take-profit, so a 24% win rate is the trail banking
small wins, not a broken target. `tools/exit_policy_ab.py` measures the
alternative.
