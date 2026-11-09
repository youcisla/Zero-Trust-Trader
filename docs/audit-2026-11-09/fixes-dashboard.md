# Dashboard remediation record

These changes address D01-D07 in the 9 November 2026 audit. All routes remain
read-only, all JavaScript stays vendored, the engine has no dependency on this
process, and the viewport grid/wire measurement is preserved.

| Finding | Implemented change | Verification |
| --- | --- | --- |
| D01 | Reject unexpected/duplicate Host and Origin values before routing; require a local Origin for WebSocket upgrades; remove raw account identity and unused fields from engine output | Header-policy unit cases and isolated fixture HTTP refusals before snapshot exposure |
| D02 | Require matching stage/ledger scan IDs, a completed latest ledger row and artifact age under three hours; legacy/mismatched data is explicitly historical/unknown | Coherence fixtures for matching, missing, mismatched, newer-failed and stale artifacts; UI refuses candidates when transport is stale |
| D03 | Seek into a bounded final 4 MB log window, discard its partial first line and expose truncation/size headers; clipboard text labels excerpts | Synthetic large-log window selection, UTF-8 content and HTTP metadata checks |
| D04 | Replace unconditional “supervised/trailing” assertions with dry-run, apply-enabled, refusal, flat and stale observation labels | Shared display-helper fixtures cover each state |
| D05 | Browser uses elapsed local time and a five-second valid-message watchdog; bad snapshot shapes are visible | Helper deadline fixtures; full inline JavaScript parsing |
| D06 | Single and copy-all log paths share an HTTP-status checked reader | Synthetic 404 refuses success, truncation labels retained |
| D07 | Navigation anchors retain independent aria labels and titles when CSS hides their spans; narrow topbar wraps | Persistent-label checks; browser/assistive-technology verification remains separate |

The smallest adjacent improvements were included: ordinary JSON reads have a
2 MB cap; reports have an 8 MB cap; normal tails use a bounded reader even if a
producer is growing the file. Snapshot construction/report scanning leave the
async reactor, and one-shot snapshot requests reuse the cached snapshot.
Invalid ports and server failures now exit nonzero instead of silently falling
back or reporting success. Sensitive responses carry no-store and anti-framing
headers. No extra Rust or frontend dependency was added.

The current API and failure/null semantics are documented in
[dashboard/API.md](../../dashboard/API.md). The shared producer contract is the
`scan_id` propagated by orchestration through both stage artifacts and the
terminal ledger row. Historical documents and ledger rows are retained; no
history is rewritten to make them appear current.

The initial Rust test attempt identified a borrowed test-path lifetime error in
the new blocking HTTP fixture helper; owning that path fixed compilation. An
initial formatting invocation used the repository root without a manifest and
was rerun from `dashboard/`. Neither failure involved a production service.
Final test/build results are recorded by the root remediation verification.

Skipped: production dashboard access, real terminal/bridge behavior, browser
exploitation, assistive technology, generated-viewer internals and load profiles.
Risk: source/fixture checks do not certify browser rendering or local-process
authentication; browser and performance validation must use synthetic state.
