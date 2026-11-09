# Project audit — 9 November 2026

Baseline: `8e08e21310b66ff67b88e115e19a047c68e5b5c8`. The working tree was clean at the start. This audit adds documentation and a synthetic diagnostic script; it does not implement the recommended production fixes.

## Assessment

The project has a clear safety model, useful regression tests, conservative defaults, and a small read-only dashboard. Several documented guarantees are not enforced consistently across all paths. The most consequential gaps are the manual order bypass, permissive cumulative-trial failures, acceptance of negative mean returns, error-to-empty position handling, and destructive ledger export. Privacy and stale-state reporting also need attention.

**The existing test suite passes while these defects remain.** A green suite establishes the covered examples, not the absence of unsafe paths or the validity of a market edge. The installed bridge's consent mechanism, broker behavior and deployed machine configuration were not certified.

The register contains **30 distinct findings: 14 P1, 14 P2 and 2 P3**. One cumulative-trial issue is discussed in both the safety and statistical reviews and counted only once. Priorities express engineering impact and remediation order; they do not imply that production exploitation or financial loss occurred.

| Priority | Meaning |
| --- | --- |
| P1 | A concrete safety, data-integrity, privacy, or core research-correctness issue; resolve before relying on the affected path |
| P2 | A substantive reliability, modelling, operational, or usability defect |
| P3 | A smaller but actionable usability/accessibility defect |

## Documents and evidence

| Document | Contents |
| --- | --- |
| [Safety review](safety.md) | Consent, supervisor, broker errors, retries, locks and protective-stop improvements |
| [Statistical review](statistics.md) | Gates, accounting, exits, clocks, costs, correlations, Monte Carlo and research methodology |
| [Dashboard review](dashboard.md) | HTTP/WebSocket exposure, freshness, resource bounds, UI correctness and accessibility |
| [Operations review](operations.md) | Privacy, dependencies, CI, packaging, setup, diagnostics, recovery and governance |
| This report | Deduplicated priorities, advisory/export findings, improvement plan and decision rationale |
| [Verification record](verification.md) | Commands, results, reproducible checks and unverified boundaries |
| [Coverage inventory](scope.csv) | Every baseline tracked file, review area, method and limitations |
| [Synthetic probes](audit_probes.py) | Network-disabled reproductions using stubs and temporary files |
| [Probe results](probe_results.json) | Recorded observations; `defect_reproduced: true` means a defect was observed |

Source references are repository-relative and refer to the audited baseline. Detailed findings identify the trigger, evidence, impact, proposed change and acceptance checks. Relative links deliberately keep these documents portable and avoid publishing installation paths.

## Finding register

| ID | Priority | Finding | Evidence and proposed remedy |
| --- | --- | --- | --- |
| SAFE-01 | P1 | Manual test opens an unprotected order outside bridge consent | [Safety](safety.md): remove the supported direct-entry route or require the real approval/token/idempotency path; keep automated tests mocked |
| SAFE-02 / S2 | P1 | Failed count reads lower cumulative trials; manual Tier 3 undercounts | [Safety](safety.md), [statistics](statistics.md): refuse unverifiable counts and derive an authoritative cumulative total |
| SAFE-03 | P1 | Position retrieval errors appear healthy and flat | [Safety](safety.md): preserve unknown versus empty and reconcile before declaring flatness |
| SAFE-04 | P1 | Partial closes retry the original full volume | [Safety](safety.md): restrict fill fallback and reconcile residual exposure before any retry |
| SAFE-05 | P1 | Stale lock replacement/release can remove another owner's lock | [Safety](safety.md): use process-lifetime OS locking or an ownership-safe protocol |
| S1 | P1 | Default significance and permutation gates can accept negative mean R | [Statistics](statistics.md): unconditionally refuse nonpositive net mean and review statistic choice without weakening gates |
| S3 | P1 | Horizon expiry takes a price from one additional unchecked bar | [Statistics](statistics.md): settle on the final processed permitted bar with explicit timestamp semantics |
| S4 | P1 | Historical session exits conflict with the code's broker/UTC convention | [Statistics](statistics.md): define and test one time contract; actual terminal convention remains unmeasured |
| S5 | P1 | Missing metadata spread becomes a free backtest | [Statistics](statistics.md): share a validated bid/ask cost helper and refuse unverifiable costs |
| S7 | P1 | Missing/nonfinite correlations pass deduplication | [Statistics](statistics.md): quarantine unverifiable additional candidates |
| OPS-01 | P1 | The tracked privacy guard embeds the private identifier and exempts itself | [Operations](operations.md): use a private CI variable and scan every tracked source with sanitized diagnostics |
| OPS-02 | P1 | Portable ignore rules miss current private artifact classes | [Operations](operations.md): track explicit generation, temporary, database-sidecar and launcher exclusions |
| D01 | P1 | WebSocket has no Origin/Host check before exposing snapshots | [Dashboard](dashboard.md): enforce local origin/host policy and minimize exposed data; browser exploitability is unverified |
| DATA-01 | P1 | Failed history retrieval overwrites the trade ledger | Detailed below; refuse the API error and preserve all existing records |
| S6 | P2 | Short stop fill adds spread twice; simulated/live inputs differ | [Statistics](statistics.md): specify bid/ask semantics and test exact fill/request parity |
| S8 | P2 | Monte Carlo describes a different exit policy | [Statistics](statistics.md): align the policy or clearly label the separate scenario |
| OPS-03 | P2 | Dependencies, toolchain and scheduled interpreter are not reproducible | [Operations](operations.md): approved lock/constraints, tracked Cargo.lock and a pinned local interpreter |
| OPS-04 | P2 | Publication builder is local-only and stale | [Operations](operations.md): tracked sanitized inputs and a clean-artifact completeness test |
| OPS-05 | P2 | Setup instructions do not configure the required scanner account pin | [Operations](operations.md): document component-specific environment loading and verify clean-shell setup with stubs |
| OPS-06 | P2 | Status diagnostics print all policy values | [Operations](operations.md): output an allowlist with redaction and synthetic disclosure tests |
| D02 | P2 | Dashboard can mix artifacts from different scans | [Dashboard](dashboard.md): coherent run IDs, timestamps and explicit historical/unknown states |
| D03 | P2 | Log export allocates the whole file, keeps only its first 4 MB and drops newer content | [Dashboard](dashboard.md): bounded tail or controlled streaming with honest truncation metadata |
| D04 | P2 | Dry-run positions are described as actively supervised/trailing | [Dashboard](dashboard.md): render observed, applied, refusing and stale states accurately |
| D05 | P2 | A stalled open socket can freeze apparently live freshness indicators | [Dashboard](dashboard.md): browser heartbeat watchdog independent of incoming state |
| DESK-01 | P2 | Citation matching accepts invented metrics | Detailed below; parse signed tokens and require field-level evidence |
| DESK-02 | P2 | Empty/malformed advisory objects are accepted as successful notes | Detailed below; role-specific schema and application-owned status |
| DESK-03 | P2 | Rejected/failed briefs exit successfully | Detailed below; define nonzero failure/partial exit semantics |
| DATA-02 | P2 | Export labels partial positions closed and ignores fill aggregation | Detailed below; volume-aware reconstruction or explicit unsupported-case refusal |
| D06 | P3 | Copy-all treats HTTP failure bodies as successful log exports | [Dashboard](dashboard.md): check each status and identify partial exports |
| D07 | P3 | Compact navigation loses accessible names | [Dashboard](dashboard.md): persistent accessible labels and narrow-layout keyboard checks |

## Advisory desk findings

### DESK-01 — Citation matching does not establish metric provenance

Evidence: `desk.py:165-188` tests rendered numbers by substring membership in the entire source. It also exempts every single-digit number from zero through five, regardless of whether it is an ordinal or a metric.

Observed with synthetic inputs: a source containing `14.531` permits a note claiming `4.531`; `-5.523` permits a positive `5.523`; a source with no figures permits a claimed `3%` win rate. All three notes receive `status: ok`. The unrestricted whole-source match can also attach a genuine number to the wrong symbol or field. That semantic misattribution is an additional limitation, not a separate demonstrated market event.

Impact: advisory output can violate the central citation guarantee and mislead the person reviewing a proposal. The desk remains disconnected from execution, which limits direct impact.

Recommendation: compare complete signed numeric tokens, with deliberate finite rounding rules. Permit small ordinals only in explicitly non-metric contexts. Prefer structured citations to an artifact ID, symbol and field, and render the cited value deterministically. Test sign, substring, exponent/percentage formats, rounding and a number borrowed from the wrong field. Prompt wording alone cannot enforce this.

### DESK-02 — A JSON object is treated as a valid note without a role schema

Evidence: `desk.py:133-148` checks only that the response is a dictionary. `_guard` at `:192-198` accepts `{}` and retains a provider-supplied status. `run` at `:382-395` recognizes only a few status values when computing overall success.

Observed: `{}` becomes `{"status": "ok"}`. An object with an unrecognized `status` remains accepted by the guard. Required role identity, meaningful content, the scout's symbol vocabulary, skeptic verdicts and output-length rules have no structural validation here.

Recommendation: validate an explicit schema for each role before citation checks; reject missing/unknown fields or inappropriate types and limit lengths. Compute status in application code. Treat feed and model text as untrusted data; preserve the absence of terminal/tool authority. Acceptance checks should include empty objects, arrays, wrong-role objects, invented status strings, oversized strings, malformed response envelopes and injected instructions.

### DESK-03 — Scheduler success is disconnected from note success

Evidence: `agent_brief.py:73` returns a failure code only for `no key`. The `rejected`, `error` and `partial` states still return zero. Existing failure tests in `tests/test_desk.py:290-313` reproduce local status-selection expressions rather than exercising every CLI return path.

Observed: patching the model orchestration to each of those three states and calling the real CLI function returns exit code zero each time.

Recommendation: define and test CLI exit semantics for successful, unavailable, rejected, partial and failed runs. Unsuccessful briefs should be visible to a scheduler; if partial output is deliberately acceptable, document a distinct exit/status policy and ensure monitoring consumes it. Preserve written failure evidence. Additional desk improvements: per-role model/usage provenance rather than only the last response, validated HTTPS production endpoint policy with a separate test injection path, bounded response bodies and total run time, and freshness/run identity in the fact sheet. Do not add an execution role.

## Trade export findings

### DATA-01 — An API error destroys the existing export history

Evidence: `export_ledger.py:66` converts `history_deals_get() == None` to an empty list. `:97-103` then opens the requested output, by default `trades.csv`, with mode `w` and replaces it with a header. There is no successful-history or existing-record preservation check before truncation. The same destructive replacement occurs for a successful query that contains only a shorter lookback or different symbol.

Observed: a temporary ledger containing an existing row is replaced after a fake API returns `None`. The CLI exits 1 after the data has already been lost. Only a temporary fixture was changed.

Recommendation: distinguish API error from a successful empty history before opening any output. Keep the canonical trade ledger append-only, preserve existing trades/losses, and reconcile using stable broker deal IDs in private runtime state. Write separate explicitly named, immutable range exports when a filtered view is required. Atomic replacement protects a derived snapshot from crashes but is not permission to discard canonical history. Acceptance checks: API failure, empty history, reduced lookback, symbol changes, repeat exports, interrupted writes and account mismatch cannot erase or silently mix canonical records.

### DATA-02 — Entry/exit reconstruction ignores quantities and residual exposure

Evidence: `export_ledger.py:77-93` takes the first entry and last exit for each position, without examining volume, commissions, swaps, multiple fills or whether the position is fully closed. The row shape is then described as a closed trade.

Observed: synthetic entry volume 0.02 and exit volume 0.01 produce one completed export row and exit code zero, although 0.01 remains open. Multiple entries/exits also require volume-aware prices rather than the first and last prices alone.

Recommendation: implement a documented deal-reconstruction contract with quantities, weighted fills, partial closes and reversals, or refuse unsupported shapes. Preserve costs separately where the downstream format cannot represent them. Deal history windows may contain an exit without its original entry; query/reconcile sufficiently or label incomplete history. Add synthetic multi-fill, partial, netting/reversal and out-of-window-entry fixtures before interpreting exported R. No broker/account-mode behavior was tested live.

## Improvement plan across every aspect

| Aspect | Recommended improvement | Completion evidence |
| --- | --- | --- |
| Safety architecture | Inventory every mutation-capable path; enforce entry consent independently; keep supervisor reduce-only | Static call-site inventory plus mocked refusal, approval expiry, idempotency and reconciliation tests |
| Statistical correctness | Correct return statistic, costs, clocks, exit horizon and unknown correlations; keep all existing bars at least as strict | Negative-mean, missing-input and parity fixtures cannot become survivors |
| Research validity | Predeclare held-out/walk-forward protocol, chronological eligibility and dependence-aware null calibration | Documented protocol, synthetic no-edge calibration and counted new hypotheses; no cherry-picked historical losses |
| Market data | Central validation for finite ordered OHLC, closed-bar identity, tick age, bid/ask, symbol contracts and timestamps | Degenerate, stale, missing, duplicate and nonfinite fixtures fail explicitly |
| State integrity | Authoritative cumulative accounting, ownership-safe locks, atomic stop-map updates and coherent artifact IDs | Crash/race/fault-injection fixtures preserve history and distinguish unknown from empty |
| Security/privacy | Portable ignore/deny rules; minimized/redacted outputs; origin validation; no private identifiers in scanners | Clean-clone artifact-name tests, sanitized scanner fixtures and isolated HTTP/WS access tests |
| Advisory AI | Role schemas, field provenance, failure exits and bounded external calls; preserve advisory isolation | Malicious/malformed/uncited inputs produce rejected or failed notes with no execution authority |
| Dashboard/UI | Honest dry-run and freshness states, cross-scan isolation, bounded log exports, keyboard and narrow-screen support | Fixture-based browser tests at narrow widths, zoom and stalled transport |
| API design | Document GET routes, nullable/unknown data, generation choice, heartbeat, logs/report routes and errors | A local contract/fixture suite; every endpoint remains read-only |
| Performance | Measure large-file/concurrent-client behavior and deep-test phase timings before optimizing | Repeatable synthetic memory/latency benchmarks; shrink universe if needed, never permutation count |
| Tests/CI | Add Windows stubbed CI; prioritize negative-path and stale-lock tests; make lint configuration repository-owned | Same tests on supported platforms; fault injection kills the unsafe behavior being fixed |
| Dependencies | Approved Python environment, Cargo.lock, toolchain/vendor provenance and scheduled interpreter identity | Clean reproducible build and separate dependency update validation |
| Release/deployment | Repair or retire private builder; validate sanitized artifact completeness; control edits in the active execution tree | Clean-source packaging test and a documented same-tree maintenance/promotion/rollback procedure |
| Operations/recovery | Explicit freshness/failure response, encrypted local backup and ledger/key recovery drills | Temporary restore exercise cannot reduce cumulative history or silently lose failures |
| Maintainability | Extract pure state/validation helpers as needed; remove stale assumptions, avoid introducing a larger framework | Smaller reviewable fixes with behavioral tests and no extra execution coupling |
| Documentation/governance | Synchronize actual contracts and instructions; extend ownership coverage and verify protections | Clean-shell onboarding, consistent docs, and recorded repository-setting checks |
| Licensing/provenance | Inventory vendored/generated assets and upstream notices | Reviewed source/version/hash/license manifest; legal sufficiency is outside this audit |

## Recommended delivery order

1. **Close entry, integrity and privacy gaps first.** Address SAFE-01, SAFE-02/S2, DATA-01, SAFE-03/04/05 and OPS-01/02 in small independent changes. Do not run the direct manual trading test as validation. Check whether the identifier was ever published before considering any separately authorized history operation.
2. **Repair the research contract.** Address S1/S3/S4/S5/S7, then S6/S8 and the stated methodological limits. Establish costs, clocks, entry eligibility and exit rules together before rerunning market research. Preserve every prior trial and count all new measured hypotheses.
3. **Make observations and advisories dependable.** Address D01-D05 and DESK-01/02/03, then export reconstruction and stop persistence. Test every failure state using synthetic artifacts and mocked terminals.
4. **Make the result repeatable and maintainable.** Repair environment locking, CI coverage, packaging, onboarding and recovery, then accessibility, smaller UI fixes and targeted performance work.

Suggested owners are the safety maintainer for mutation/locks, quantitative maintainer for research semantics, dashboard maintainer for browser/API behavior, and operations maintainer for CI/privacy/release/recovery. These are responsibility categories, not assignments to real people. Accept each change against its stated checks, then update this register with the fixing revision and verification evidence. No dates or implementation effort are asserted without planning the fixes.

## Decision and rationale record

This records reviewable evidence and decisions, assumptions, alternatives and uncertainties. It is not a private internal reasoning transcript.

| Decision | Evidence/rationale | Limit or tradeoff |
| --- | --- | --- |
| Audit the whole checked-out project rather than a diff | The request covers every aspect; the initial tree was clean | Baseline and per-file scope are recorded; ignored installation files are not treated as publishable source |
| Split review into four independent domains, then deduplicate | Safety, statistics, dashboard and operations have distinct failure modes | Shared cumulative-trial finding is counted once; cross-domain conclusions are integrated here |
| Use project invariants as acceptance requirements | Consent, demo restriction, cumulative correction, fixed permutations and read-only UI are explicit contracts | No strategy/gate relaxation or speculative auto-execution is proposed |
| Run tests with MT5 replaced before imports | The project explicitly supports a terminal stub; existing tests can exercise pure behavior safely | Does not verify the actual installed terminal/bridge or real broker fills |
| Add narrowly scoped synthetic reproductions | Green tests missed concrete branches; examples isolate the documented fault | Reproducing a logical failure is not evidence that a historical market run reached it |
| Review API contract locally and avoid production services | The dashboard is a monitor of private trading artifacts | Browser exploitation and actual UI accessibility/performance remain unverified |
| Preserve current production code and policy | The user asked for an audit and recommendations; this directory is also the execution tree | Findings remain open until separately implemented and verified |
| Keep evidence sanitized | Account identifiers and machine paths must not be copied into a publishable report | Private state contents, configuration and published history are outside the evidence set |
| Report successful checks and counterexamples together | Passing regression/build checks and discovered defects answer different questions | No overall numerical safety score or profitability claim is justified |

The code-review-and-quality skill supplied review axes. A diff-specific review skill was inspected but its fixed-point/spec workflow did not fit this whole-project request. Postman's API-engineer/testing guidance supplied contract and evidence boundaries. Its version check encountered an unreadable managed Private Cloud manifest; the final `postman status` command separately failed because this installation does not recognize `status`. No Postman collection run, workspace, registry setting, cloud resource or account configuration was changed.

## Boundaries and residual uncertainty

No trading terminal, live dashboard, production ledger, private policy contents, scheduler or registry configuration was exercised. There were no real orders, AutoTrading changes, thresholds changes, frozen-trial edits, strategy additions, remote publications, commits or pushes. Rust build/cache artifacts and isolated audit-tool caches were created locally; the release dashboard binary was rebuilt but not launched.

Source inspection covered every listed project area; this is not exhaustive formal verification or a financial, legal, penetration-testing or deployed-configuration certification. The external MCP bridge and its token/idempotency implementation are outside the tracked repository. Rust dependency advisories and full vendored/generated JavaScript internals were not independently scanned. Broker timestamp/fill behavior, Windows scheduling, browser security policy, accessibility and restore completeness need the separately described checks.

The highest-value next step is to implement and verify the P1 fixes while preserving the project's refusal-by-default behavior.
