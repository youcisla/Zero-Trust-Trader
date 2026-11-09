# Safety and execution audit

Date: 2026-11-09. Method: source and test inspection, official API contract checks, and isolated synthetic checks. No terminal connection, real order, policy mutation, scheduler mutation, or ledger inspection was performed.

## Findings

### SAFE-01 — High: the manual test bypasses the execution consent architecture

Evidence: `manual_e2e_test.py:59-65` asks the operator to enable AutoTrading; `:88-103` accepts `EXECUTE` and calls `mt5.order_send` directly. The opening request has no stop. `:144-145` opens then closes in sequence; `:165-168` guarantees only terminal shutdown. `AGENTS.md` sections 1 and 4 require `APPROVED` plus independent bridge consent and forbid order calls outside the supervisor absent an explicit override.

Trigger and impact: an operator follows this test's own instructions. The test exercises bridge preflight limits in-process, but never obtains a bridge consent token or idempotency key. If the opening succeeds and the subsequent close fails, the unprotected demo position remains open and AutoTrading can remain on. Demo checks reduce the account risk but do not restore the missing structural consent gate.

Recommendation: remove the direct-execution test from the supported workflow or route its manual, explicitly authorized execution through the actual bridge approval/idempotency path. Keep automated coverage fully mocked. Document any intentionally exceptional manual procedure precisely; do not present a policy-function replay as an end-to-end consent test.

Acceptance checks: mocked execution refuses without fresh bridge approval; every supported opening path requires an initial protective stop; post-fill failures produce a clear unresolved-position state. No live test was attempted.

### SAFE-02 — High: trial-count failures silently lower the significance input

Evidence: `run_funnel.py:312-321` catches every exception reading prior trials and the current scan's `total_tests`, substitutes zero, and `:322-327` sends the resulting count to Tier 3.

Trigger and impact: an unreadable/malformed ledger, import failure, or malformed Tier 2 artifact. The run continues with a smaller cumulative trial count instead of refusing. A positive current count combined with a failed prior count is sufficient to understate scan history. This contradicts the cumulative, fail-closed significance invariant; it does not itself place an order.

Recommendation: abort with a failed audit event when either count cannot be established. Validate nonnegative integer counts and Tier 2 artifact identity/freshness. Never change `LEGACY_TRIALS` or reduce thresholds as a repair.

Acceptance checks: inject ledger-read exceptions, corrupt artifacts, negative/string/fractional counts, and stale Tier 2 files; assert Tier 3 is never invoked and no proposal is reported.

### SAFE-03 — High: position retrieval errors are reported as healthy and flat

Evidence: `position_manager.py:206` uses `mt5.positions_get() or []`; `:246-256` then emits `ok: true` with an empty position list. The same conflation appears in the manual test at `manual_e2e_test.py:111`, `:138`, and `:147`.

The official [positions_get contract](https://www.mql5.com/en/docs/python_metatrader5/mt5positionsget_py) distinguishes an API error (`None`) from a returned position sequence.

Trigger and impact: the terminal connection passes preflight but position retrieval fails. The supervisor skips management for that pass and publishes a fresh, healthy-looking flat state. In the manual test a retrieval error can bypass the existing-position refusal or falsely confirm flatness.

Recommendation: handle `None` as a failed observation with `last_error()`, preserve the distinction between unknown and empty, and require successful reconciliation before claiming a completed close. Also handle an account observation disappearing between preflight and the second `account_info()` call (`position_manager.py:205`).

Acceptance checks: a fake terminal returns `None`, an empty tuple, and a nonempty tuple; only the empty tuple may produce a confirmed flat state. Test account retrieval failure independently.

Executed isolated check: a synthetic `MetaTrader5` module was installed in `sys.modules` before importing the supervisor, and initialization/preflight were mocked. With `positions_get` returning `None`, the real `run_once` returned `ok=True, positions=[]`; the assertion passed. No real terminal API was available to this process.

### SAFE-04 — High: a partial close retries the original full volume

Evidence: `position_manager.py:153-167` constructs one close request using `pos.volume`, tries IOC, and tries FOK for every response other than `TRADE_RETCODE_DONE`. The second request uses the original snapshot volume and price. No position/deal reconciliation occurs between requests.

The official [trade return-code table](https://www.mql5.com/en/docs/constants/errorswarnings/enum_trade_return_codes) defines separate codes for partial completion (`DONE_PARTIAL`), unsupported filling (`INVALID_FILL`), and a close volume exceeding the current position (`INVALID_CLOSE_VOLUME`).

Trigger and impact: an IOC close partially fills. The fallback requests the full original quantity after exposure has already shrunk, so it can be rejected and leave residual exposure. Other failures are also retried without checking whether changing fill policy is relevant. An actual reversal or duplicated broker fill was not demonstrated; broker-specific behavior remains unverified.

Recommendation: restrict fill-policy fallback to an explicitly unsupported filling mode. Reconcile actual remaining exposure and deal history after partial or uncertain outcomes before any further action, with a fresh quote and a bounded retry policy. Preserve reduce-only intent and explicit failure reporting.

Acceptance checks: fake `DONE_PARTIAL`, `INVALID_FILL`, timeout/connection failure, and a position disappearing concurrently. Assert there is no blind repeat of the original full-volume request and no false completed-close claim.

Executed isolated check: a fake `order_send` returned `10010` then `10038` to the real `_close` helper. The helper emitted two requests, both with synthetic original volume `0.01`, and returned failure. Assertions passed. This establishes the blind retry, not a broker fill or reversal.

### SAFE-05 — High: stale lock takeover has no ownership protection

Evidence: `run_funnel.py:125-139` and `position_manager.py:315-327` check staleness then unlink by pathname. `run_funnel.py:143-147` and `position_manager.py:420-421` also release by pathname without validating ownership. The supervisor heartbeat (`:373-377`) is refreshed only before a whole management pass; its lease is four configured intervals (`:357`). The funnel's lock has a fixed age limit and no heartbeat.

Trigger and impact: a valid process stalls beyond the lease, or two processes simultaneously inspect the same stale lock. A contender can remove another contender's replacement lock; a resumed/terminating old owner can refresh or remove the new owner's lock. Concurrent supervisors can issue conflicting stop/close requests, and overlapping funnel scans can corrupt trial accounting. `O_EXCL` protects initial creation, but it does not make stale replacement or release ownership-safe.

Recommendation: use an OS-held exclusive lock for the process lifetime where possible; otherwise implement ownership tokens, verified liveness, and a takeover protocol that cannot delete a different generation. A long pass must not make a still-running owner eligible for takeover.

Acceptance checks: coordinate two stale-lock contenders with barriers; delay a management pass beyond the lease; resume/exit the old owner after takeover; assert at most one process may act and a nonowner cannot release the new lock. Existing `tests/test_position_manager.py:259-277` covers concurrent creation of an absent lock only.

## Additional improvements

- Quantize submitted stops using symbol tick size/digits and broker stop/freeze distances, then recheck protective direction against a fresh position/quote. The current wire path rounds every symbol to five decimal places (`position_manager.py:237-244`), after the pure-function checks. Add integration-level mocked tests of the exact submitted request, including non-FX tick sizes and nonfinite input rejection.
- Keep failed Market Watch cleanup entries pending for retry. `marketdata.py:273-277` discards ownership even when deselection fails, so a transient failure becomes a permanent leak for that process. Ensure cleanup runs in `finally` on every scanner path.
- Make the demo preflight validate the API's explicit demo trade mode as well as the pinned login and server-name check. The current funnel checks only name and login (`run_funnel.py:61-69`); the manual test already demonstrates an explicit trade-mode check (`manual_e2e_test.py:52-55`). Treat this as defense in depth, not evidence that a real account passed.
- Replace `stops.py:36-38` read-modify-write with an atomic, concurrency-safe update; validate ticket and finite positive stop values. A crash can truncate this derived risk map, and concurrent writers can lose entries.
- Document that `ZTT_ALLOW_APPLY` is an operational interlock, not an independent human consent mechanism: any process capable of setting its environment can set it. Preserve dry-run defaults and keep this scope distinct from entry approval.
- Expand tests beyond `next_stop`: broker-return semantics, request rounding, position/account observation failures, partial closes, and stale-lock replacement currently lack corresponding coverage in the inspected tests. Preserve the existing 3,000-position protective-direction sweep.

## Rationale and limitations

Priorities follow the project's documented invariants: entry consent, honest observations, cumulative significance, and exclusive reduce-only supervision. Findings above identify concrete source behavior and its trigger; recommendations are acceptance criteria, not implemented changes. Demo-only checks, a pinned login, dry-run defaults, fixed permutation counts, and explicit send-result checks are valuable existing controls.

Skipped: live terminal behavior, actual policy configuration, bridge implementation/token enforcement, scheduler registration, broker partial fills, and real order execution. These remain unverified; source inspection and mocks cannot certify the deployed trading system.
