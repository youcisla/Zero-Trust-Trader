# Read-only dashboard API

The server listens on IPv4 loopback only. It reads producer files; it has no
terminal client, mutation endpoint or engine callback. Client WebSocket messages
are ignored. Closing this monitor cannot change engine operation.

## Local request policy

Every request must have exactly one Host header equal to `localhost:<port>` or
`127.0.0.1:<port>`, using the configured port (8787 by default). If Origin is
present, it must be exactly `http://localhost:<port>` or
`http://127.0.0.1:<port>`. WebSocket upgrades require that local Origin; absent,
null, foreign, wrong-port and duplicate authority headers are refused with 403
before any snapshot is sent. Ordinary same-origin GETs and native local clients
may omit Origin. No remote proxy or non-browser WebSocket client is supported.

Responses are not cacheable. The UI embeds local JavaScript assets, disables
framing and does not require an internet connection. Local-process access is not
a user-authentication boundary; the same user must protect the producer files.

## Routes

| GET route | Response | Failure behavior |
| --- | --- | --- |
| `/` | Embedded dashboard HTML | Subject to local Host/Origin policy |
| `/architecture` | Embedded generated architecture HTML | Built with the binary |
| `/api/snapshot` | Cached JSON snapshot | Missing or invalid producer data is null/unknown |
| `/ws` | Current snapshot, then approximately one update/second | Invalid origin is 403; standard upgrade validation still applies |
| `/api/logs/funnel` | Recent complete lines from the final bounded 4 MB window | 404 missing, 503 unreadable |
| `/api/logs/actions` | Same contract for supervisor actions | 404 missing, 503 unreadable |
| `/api/logs/audit` | Same contract for the resolved audit log | 404 missing, 503 unreadable |
| `/api/report/latest` | Latest report HTML by modification time, at most 8 MB | 404 absent, 503 unreadable or oversized |
| `/vendor/react.production.min.js` | Embedded JavaScript | No runtime CDN |
| `/vendor/react-dom.production.min.js` | Embedded JavaScript | No runtime CDN |
| `/vendor/htm.umd.js` | Embedded JavaScript | No runtime CDN |

Unknown log names return 404. Routes support only read methods; there are no
order, settings, file-write or command messages. Axum provides normal HEAD
behavior for GET routes.

Log responses remain UTF-8 text. Their headers are `X-Log-Truncated` (true/false),
`X-Log-Bytes-Total` (observed source bytes) and `X-Log-Window-Bytes` (4000000).
When truncated, the first partial line is discarded; the response contains
newest complete lines, not the beginning of the file. A single line longer than
the window may yield an empty excerpt with truncation true. The UI labels every
truncated clipboard export. Copy-all fails if any requested log returns an HTTP
error; it does not call a partial export complete.

## Snapshot contract

The top-level fields are:

- `ts`: snapshot production time in Unix seconds.
- `engine`: null, or a display-only object with `ok`, `applied`, `error`,
  `account` and `positions`. The account retains display equity, balance,
  currency, server and a masked label; the raw account login and arbitrary
  provider fields are omitted. Position fields are restricted to those used by
  the table.
- `engine_age_s`: age of the chosen engine-generation file, or null if unknown.
  The reader selects the newest generation, falling back to the legacy name.
- `equity`, `balance`, `currency`, `server`, `positions`: display summaries;
  null is unknown, while a confirmed empty positions array yields count zero.
- `tier2`, `tier3`, `agent`: parsed JSON artifacts, or null when absent,
  unreadable, malformed or larger than the 2 MB input limit. Artifacts can be
  historical; their presence does not establish current evidence.
- `funnel`, `actions`, `mcp`: at most 12 nonempty log rows read from a bounded
  final 16 KiB window. Null means unavailable; an empty array means an existing
  empty log.
- `scan`: `{current, status, scan_id, reason}`. `current` is true only when both
  stage artifacts and the latest ledger row have the same nonempty `scan_id`,
  the artifacts are no older than three hours, and that latest row ends in a
  completed `STAND_DOWN` or `PROPOSAL` verdict. Missing identity, newer failed
  rows, mismatches and old artifacts produce `historical_or_unknown`.

Consumers must check `scan.current` before displaying a current candidate.
Legacy artifacts without IDs remain readable as historical inputs. The monitor
does not verify the ledger HMAC, establish a market edge, or authorize execution.
The report route is explicitly a historical document view.

The browser also treats a snapshot as unavailable until a valid message arrives.
After five seconds without a valid heartbeat it shows stale transport even if
the socket remains open; displayed engine age continues increasing locally.
Position supervision labels distinguish refusal, stale/unknown observation,
confirmed flatness, dry run and apply-enabled observation. Apply enabled is not a
claim that every protective action succeeded, or that terminal AutoTrading is
enabled/disabled.

## Local verification

Run from `dashboard/`:

```text
cargo test --locked --offline
cargo clippy --locked --offline -- -D warnings
node --test tests/ui.test.cjs
```

Rust tests bind an ephemeral loopback server against temporary synthetic files;
they never read the production tree. Node's built-in test runner checks the
embedded state helpers and script syntax without a frontend build system.
Browser rendering, assistive technology, actual browser local-network policies
and load profiling require separate synthetic-fixture checks.
