# Closure map — audit of 9 November 2026

Baseline audited: `8e08e21`. Remediation landed in `ae308e9` (70 files, +5,572).
This document answers the question the audit could not: **which findings are
actually closed, and what is the evidence?**

Method, in descending order of strength:

| Mark | Meaning |
| --- | --- |
| **probe** | the audit's own synthetic reproduction was re-run against this tree and no longer reproduces (`docs/audit-2026-11-09/audit_probes.py`, 9 cases, network disabled) |
| **test** | an automated test in the suite asserts the fixed behaviour |
| **code** | the defect was re-read in the current source and is not present |

Probe result on this tree: **0 of 9 cases reproduce.** Every finding below was
individually re-checked; none is closed on the strength of the fixes alone.

## Register

| ID | P | Finding | Fix | Evidence | Verified |
| --- | --- | --- | --- | --- | --- |
| SAFE-01 | 1 | Manual test opened an order outside consent | terminal contact removed entirely; runs the stub | `manual_e2e_test.py` header + `_mt5stub.install()` | code |
| SAFE-02 / S2 | 1 | Failed count reads **lowered** cumulative trials | `scan_id` dedupe, `UNKNOWN` raises unless a signed `TRIALS_RECONCILED` row resolves it, unparseable counts raise | `trials_total.py` `total()`, `required_trials()` | code |
| SAFE-03 | 1 | `positions_get()` error reported as flat | `None` → `{"ok": false, "error": ...}`; never coerced to `[]` | `position_manager.run_once` | code |
| SAFE-04 | 1 | Partial close retried the full original volume | re-read before send, `min(pos.volume, fresh.volume)`, FOK volume check, no blind retry | `position_manager._close` | code |
| SAFE-05 | 1 | Stale lock takeover had no ownership protection | OS file locks (`msvcrt`/`flock`); crash releases, age cannot evict a live process | `process_lock.py` | code |
| S1 | 1 | Gates could approve a negative mean return | `profitable = finite_returns and mean_r > 0`; both `passes_*` require it | `timemachine_mt5.significance` | code |
| S3 | 1 | Horizon exit priced one bar past the loop | `j = min(j, i + horizon, n - 1)` | `timemachine_mt5._resolve_live` | code |
| S4 | 1 | Session exits used a different clock | one contract: `md.bar_close_utc(bars, j)` | `timemachine_mt5._resolve_live` | code |
| S5 | 1 | Missing spread became free execution | `md.tick_spread()` validates bid/ask/freshness, returns `None`; callers refuse | `marketdata.tick_spread`, `position_manager._atr` | code |
| S7 | 1 | Unknown correlation accepted as independent | `rho is None or not isfinite(rho) or abs(rho) > corr_max` → dropped | `tier2_patterns.dedupe` | code |
| OPS-01 | 1 | Privacy guard embedded the identifier it protects | identifier comes from `ZTT_PRIVATE_IDENTIFIER`; guard scans every tracked file | `tools/check_public.py`, `ci.yml` | code |
| OPS-02 | 1 | Ignore rules lagged the artifact names | `.env*`, `idempotency.db*`, `_publish/`, `engine_state.*`, local launchers | `.gitignore` | code |
| D01 | 1 | WebSocket exposed state to any origin | host/origin enforced before serving | `http_routes_enforce_host_and_origin_before_exposing_fixture_state` | test |
| DATA-01 | 1 | API error overwrote the trade ledger | refuses, exits 2, existing history byte-identical | probe `export_api_error_preserves_ledger` | probe |
| S6 | 2 | Short stop fill charged spread twice | one spread per trade: `hi + spread` for MAE, stop books `cur`, mark at `close + spread` | `timemachine_mt5._resolve_live` | code |
| S8 | 2 | Monte Carlo graded a different exit policy | labelled `fixed_barrier_without_live_costs` with its limitations inline | `timemachine_mt5` scenario block | code |
| OPS-03 | 2 | Toolchain not reproducible | `dashboard/Cargo.lock` + `--locked` CI, `rust-toolchain.toml`, `requirements-dev.txt` | CI jobs | code |
| OPS-04 | 2 | Publication builder local-only | `tools/build_public.py` with `--check`, run in CI from tracked source | CI step | code |
| OPS-05 | 2 | Setup instructions did not produce a runnable scan | `.env.example` rewritten per component | `.env.example` | code |
| OPS-06 | 2 | Diagnostics printed the full policy | identifier/server redacted, key and policy presence only | `status.py` | code |
| D02 | 2 | Mixed artifacts could look current | fresh matching artifacts + latest completed row required | `current_scan_requires_fresh_matching_artifacts_and_the_latest_completed_row` | test |
| D03 | 2 | Log export kept the **first** 4 MB and dropped newer lines | bounded tail keeping newest complete lines, truncation labelled | `log_excerpt_is_bounded_and_keeps_the_newest_complete_lines` | test |
| D04 | 2 | Dry-run positions described as supervised | "observed only · dry run", explicit "unverified" state | `dashboard/web/index.html` | code |
| D05 | 2 | Stalled socket froze freshness | watchdog interval independent of incoming messages | `dashboard/web/index.html` | code |
| DESK-01 | 2 | Citation guard accepted invented metrics | tokenised decimal comparison, role required | probe `citation_substring`, `citation_sign`, `citation_small_metric` | probe |
| DESK-02 | 2 | Empty advisory accepted as success | role schema; `missing or incorrect advisory role` | probe `empty_note` | probe |
| DESK-03 | 2 | Failed briefs exited 0 | exit 2 for rejected/error/partial | probes `desk_exit_*` | probe |
| DATA-02 | 2 | Partial position exported as closed | volume-aware; refuses and exits 2 | probe `export_partial_position` | probe |
| D06 | 3 | Copy-all treated HTTP errors as success | `if (!response.ok) throw`; failure state and `aria-live` | `fetchLog`, copy button | code |
| D07 | 3 | Compact nav lost accessible names | persistent `aria-label`s; node fixture test | `dashboard/tests/ui.test.cjs` | test |

## What this does not certify

The audit's own scope limits still hold, and closing a finding does not extend
them:

- **Bridge consent** was not re-certified. SAFE-01 removed the in-tree order
  path, but the external bridge's token and idempotency behaviour was not
  exercised.
- **Broker fill behaviour** is reasoned about, not observed. `_close` refuses
  what it cannot verify; it has never been run against a real partial fill.
- **Deployed machine configuration** (Task Scheduler specs, the live
  `config.toml`) was not inspected.

## Open items not in the register

Found while closing, still open, deliberately not fixed here:

1. **`--horizon` defaults to 32 while `HORIZON_DEFAULT["M15"]` is 96.** A caller
   that omits `horizon` backtests a 24-hour hold; the pipeline measures an
   8-hour one. Documented in `AGENTS.md` §10.
2. **The live exit path has no take-profit.** `_resolve_live` walks stop,
   trailing, break-even, session close and horizon; `target` counts 0 in every
   record and `rr` is inert there. Correct today, and the reason a 24% win rate
   is not a defect. `tools/exit_policy_ab.py` measures the alternative.
3. **Cost per 1R is the dominant term** at every timeframe (3.3% at H4, 61% at
   M1). That is a strategy-selection constraint, not a defect: `docs/SIZE-AND-RUIN.md`.
