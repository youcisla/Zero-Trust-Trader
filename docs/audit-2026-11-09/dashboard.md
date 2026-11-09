# Dashboard and API audit — 2026-11-09

Scope: `dashboard/src/main.rs`, `dashboard/Cargo.toml`, `dashboard/web/index.html`, the generated architecture viewer and its JSON source, vendored JavaScript, and dashboard launcher. This is a source audit; production services, terminal, private state, and browser were not accessed. Priority means remediation order, not permission to change execution controls. References use repository-relative paths and source line numbers at audit time.

## Findings

### D01 — P1: WebSocket accepts arbitrary browser origins

**Confidence:** confirmed source-level exposure path; browser exploitation unverified.

**Evidence:** `dashboard/src/main.rs:298` directly upgrades the socket without validating Origin or Host. `:302` sends the current snapshot and subsequent updates. The full engine document is exposed by `build` (`:169` onward), including account and position fields supplied by `position_manager.py:249`. Binding to `127.0.0.1` (`main.rs:373`) limits network reachability but does not establish the origin of a browser making a loopback connection.

**Trigger/impact:** a hostile web page in a browser that permits its connection to the loopback WebSocket can receive account/position/log data. Browser mixed-content and local-network policies affect whether the connection is allowed; no end-to-end exploit was attempted. This is a confidentiality concern, not an order-execution path.

**Improvement:** validate exact expected local Host and Origin before upgrading; document whether non-browser clients are supported. Apply the Host rule to HTTP routes as defense against unwanted host routing. Return a minimal display DTO rather than the entire engine account object. Keep every endpoint read-only.

**Acceptance:** isolated server/fixture tests allow the intended local origin, reject a foreign origin/Host, and confirm rejected clients receive no snapshot. Add a browser fixture check on supported browsers.

### D02 — P2: Stale artifacts can produce a current-looking candidate

**Confidence:** high, source-confirmed.

**Evidence:** `dashboard/web/index.html:386` combines Tier 2, Tier 3 and the most recent parseable ledger row without checking a common run identity or timestamp. `:845` prefers any existing Tier 3 verdict; `:914` can display EDGE CANDIDATE. `main.rs:166` reads the fixed stage artifact names with no freshness metadata. The orchestration review confirms prior artifacts survive early/failing runs; successful Tier 3 validation does not prevent the dashboard from consuming older artifacts after other failures.

**Trigger/impact:** an old Tier 3 result remains while a newer run fails preflight/Tier 2 or writes only one stage. The monitor can combine different scans or continue showing an earlier proposal. The engine-age badge measures supervisor state, not scan-result freshness.

**Improvement:** propagate a scan ID and timestamps through both stage artifacts and the terminal ledger row. Display historical results with age, and show incomplete/failed/mismatched stages as unknown. Do not delete or rewrite ledger history.

**Acceptance:** fixtures with mismatched IDs, a newer failed run, missing stages, malformed JSON and stale timestamps cannot produce an unlabeled current candidate. A coherent completed run still renders normally.

### D03 — P2: Log export is unbounded in memory and silently clips recent history

**Confidence:** high, source-confirmed.

**Evidence:** `dashboard/src/main.rs:264` reads the entire append-only file into memory, then `:269` keeps only the first 4,000,000 bytes and drops newer content. `index.html:309` and `:321` describe the action as copying the full log.

**Trigger/impact:** larger logs require memory proportional to the whole file, multiplied by concurrent requests. A successful response silently omits the newest material once the file exceeds the response cap. `spawn_blocking` protects the async reactor, but does not bound allocation.

**Improvement:** either stream a full download with explicit resource limits or seek/read a bounded final window and label it as an excerpt. Include truncation metadata and test the Unicode boundary. Keep normal snapshot tails bounded.

**Acceptance:** a large temporary fixture proves the selected read path consumes bounded memory, returns the intended newest/full content and visibly reports truncation. Concurrent requests have a finite budget.

### D04 — P2: Open positions are described as supervised even in dry run

**Confidence:** high, source-confirmed.

**Evidence:** `index.html:929` renders `supervised · trailing` for any nonempty position list, ignoring the explicit `engine.applied` flag published at `position_manager.py:256`. The same label remains when the engine-age badge is stale.

**Trigger/impact:** a dry-run supervisor observes a position and the operator receives an assurance of active protection that the process has not enabled.

**Improvement:** show observation/dry run, apply enabled, stale or refusing as distinct factual states using `applied`, `ok` and freshness. Report unsuccessful stop actions explicitly. Do not enable apply mode as part of this display fix.

**Acceptance:** fixtures with positions and `applied=false`, `applied=true`, stale state and `ok=false` receive accurate labels.

### D05 — P2: Open socket without messages freezes freshness indicators

**Confidence:** high about missing control; operational stall not reproduced.

**Evidence:** `index.html:817` marks the connection live on open, `:820` only updates age from incoming messages, and `:818` detects closure. There is no independent last-message timeout. `main.rs:179` builds heartbeat snapshots using synchronous file reads.

**Trigger/impact:** if the watch loop stalls or dies while sockets remain open, the green live indicator and previously low engine age can remain frozen.

**Improvement:** track last received heartbeat in the browser using elapsed local time and show a stale transport state after a bounded interval, even when the socket remains open. Detect invalid snapshot shapes rather than silently ignoring every parse error. Record watcher failures.

**Acceptance:** a fixture socket that stays open but stops sending becomes visibly stale without depending on a close frame.

### D06 — P3: Copy-all treats missing logs as a successful export

**Confidence:** high, source-confirmed.

**Evidence:** `index.html:830` fetches all three logs but, unlike the individual copy path at `:311`, does not check `response.ok`. It then copies error text and sets state to done (`:834`).

**Improvement/acceptance:** handle each response status, label intentional partial exports, and report failure if a required log is unavailable. A 404/500 fixture must not produce an unconditional success indicator.

### D07 — P3: Compact sidebar removes accessible link names

**Confidence:** high, source-confirmed; assistive-technology test pending.

**Evidence:** below 900px, CSS hides `.navitem span` and `.sidelink span` (`index.html:94`). Navigation anchors (`:867`) have no independent accessible label; their remaining SVG is `aria-hidden` (`:870`). Architecture/report links have the same shape (`:879`).

**Improvement/acceptance:** add persistent accessible labels or visually hidden text and tooltips to compact navigation. Verify every link has a useful accessible name at narrow widths and at 200% zoom.

## Controls worth preserving

- All registered routes use GET (`main.rs:361`); socket messages are ignored (`:320`). No dashboard order action or engine callback was found.
- Listener is IPv4 loopback-only (`:373`); route parameters for log files use an explicit allowlist (`:250`), rather than arbitrary file paths.
- Normal log tails seek to a 16 KiB window (`:53`) and distinguish missing from empty files. JSON parse failures become unknown instead of crashing the monitor.
- React, React DOM and HTM are embedded local assets; the architecture page is embedded at build time. No runtime CDN dependency was found in the dashboard entry point.
- The UI includes focus-visible styles, native buttons, pressed state, ResizeObserver-based wires, a reconnect notice and a render error boundary.
- Rust includes tests for bounded-tail selection, missing versus empty files and Unicode clipping (`main.rs:398`). Build/clippy/test execution is recorded by the main audit, not claimed here.

## Improvements across the remaining aspects

| Area | Minimal next improvement | Evidence of completion |
| --- | --- | --- |
| API contract | Document all GET routes, snapshot nullability, age semantics, log truncation, Origin/Host policy and websocket heartbeat; current project endpoint table omits logs/report routes | Contract/fixture tests cover missing, malformed and stale producer files |
| Browser regression | Synthetic fixtures for connected/disconnected/stalled, failed engine, dry run, missing artifacts and historical proposal | Automated smoke test uses temporary state, never private production files |
| Responsive UX | Verify topbar wrapping, grids and long symbols at 320/375/768px and 200% zoom | No document-wide horizontal overflow; tables may scroll within their own containers |
| Accessibility | Audit keyboard order, link names, contrast, announced status changes and reduced motion | Keyboard + accessibility-tree checks at desktop/mobile widths |
| Performance | Reuse cached snapshot for one-shot reads, bound JSON/report file sizes, and move blocking report/directory reads off the request reactor if measured delay warrants it | Synthetic large-file and concurrent-client latency measurements |
| Lifecycle | Report unexpected server exit (`main.rs:385` currently discards the serve error); validate invalid CLI ports explicitly | Occupied-port and server-error tests produce useful nonzero failures |
| Dependency/provenance | Record vendor versions, hashes, upstream URLs and required license notices; manage Rust lockfile/update review | Repeatable build and documented vendor refresh/check process |
| Generated architecture | Preserve JSON as source; add a regeneration receipt/check that does not publish machine paths; the current generated viewer is roughly 771 KB | Deterministic source-to-artifact review and offline loading check |
| Maintainability | Keep one process and local assets; extract only pure parsers/state helpers needed for fixture tests before splitting the UI | Small testable changes without a new framework or build pipeline |

Not checked: live/browser behavior, screen-reader/contrast measurements, runtime memory/latency, dependency advisories, full generated-viewer internals or production configuration. Source review cannot certify runtime safety; none of these suggestions authorize trading or changing execution gates.
