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

`alerts[]` carries `{id, severity, message, since_ts}`. `severity` is one of
**`error`**, **`warn`**, **`info`** — nothing else. `error` is red, `warn` amber,
`info` neutral; a severity outside that set renders as `info`, which is why the
desk-failure alert says `error` rather than a fourth word the client would have
to learn. Both sides are pinned: `health.rs` asserts the word it emits and the
node suite asserts the three it maps.
| `/ws` | Current snapshot, then the snapshot on change (checked at 1 Hz), resent at least every 4 s so a quiet tree cannot be mistaken for a dead feed; protocol ping every 15 s; age advances in the browser. The client calls the transport stale after 8 s, so one late beat is not a reconnect. At most 16 sockets at once | Invalid origin is 403; a 17th client is 503 `too many dashboard clients`; standard upgrade validation still applies |
| `/api/logs/funnel` | Recent complete lines from the final bounded 4 MB window | 404 missing, 503 unreadable |
| `/api/logs/actions` | Same contract for supervisor actions | 404 missing, 503 unreadable |
| `/api/logs/audit` | Same contract for the resolved audit log | 404 missing, 503 unreadable |
| `/api/report/latest` | Latest report HTML by modification time, at most 8 MB | 404 absent, 503 unreadable or oversized |
| `/vendor/react.production.min.js` | Embedded JavaScript | No runtime CDN |
| `/vendor/react-dom.production.min.js` | Embedded JavaScript | No runtime CDN |
| `/vendor/htm.umd.js` | Embedded JavaScript | No runtime CDN |
| `/vendor/echarts.min.js` | Embedded chart library, sha256 in `web/vendor/PROVENANCE.json` | No runtime CDN |
| `POST /api/scan` | **The only write in this process.** Spawns `run_funnel.py --window-utc 05:00-17:00 --t-gate` with no other argument | 202 with `pid`; **409** while a scan is alive; **429** within 5 s of a successful spawn; **503** if the script is missing; **500** if the interpreter cannot be spawned. A 409 does **not** consume the 5 s debounce; a failed spawn does not either. The funnel's own lock, window and demo pre-flight still decide whether anything runs |

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
After eight seconds without a valid heartbeat it shows stale transport even if
the socket remains open (the server resends every 4 s, so one late beat is not a
reconnect); displayed engine age continues increasing locally.
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

## Snapshot v2 (additive)

Every field that existed before is unchanged, including its null semantics. v2
adds five keys. A missing artifact is `null` or `present:false` — never `0`.

```jsonc
{
  "universe": {
    "present": true,                 // false when liquid_universe.json is absent
    "count": 120,                    // null when absent
    "generated_utc": "2026-11-10T09:41:00Z",
    "by_class": {"FX": 19, "SHARES": 83, ...},
    "symbols": [{"symbol": "EURUSD", "class": "FX", "spread_pct_atr": 1.81, "mean_tick_volume": 1314.5}]
  },
  "ledger": {
    "present": true,
    "rows": [                        // newest first, cap 400
      {"ts": "2026-11-10T10:29:41Z", "universe": 120, "tested": 119, "total_tests": 238,
       "triggering": 15, "deduped": 1, "shortlist": 4, "passed_both": 0,
       "cumulative": 11080, "scan_id": "3844a179...", "permutations": 1000,
       "verdict": "STAND_DOWN", "winners": [], "signed": true, "detail": null}
    ],
    "window_bytes": 524288,          // the parser reads a bounded tail
    "truncated": true,
    "total_bytes": 90112,
    "malformed": 0
  },
  "series": {                        // in memory only; nothing is written to disk
    "since_ts": 1762776360,          // null on a cold start
    "equity":  {"2h": [[1762776360, 199.92]], "48h": [], "14d": []},
    "balance": {"2h": [...], "48h": [], "14d": []},
    "events":  [{"ts": 1762776360, "kind": "verdict", "label": "STAND_DOWN", "scan_id": "..."}]
  },
  "alerts": [
    {"id": "engine_stale", "severity": "warn", "message": "engine state 12h 38m stale", "since_ts": 1762776360}
  ],
  "health": {
    "files": {"engine_state.json": {"exists": true, "age_s": 3},
              "liquid_universe.json": {"exists": true, "age_s": 3700}},
    "maintenance": {"active": false, "age_min": null, "note": null}
  }
}
```

Notes that matter for a client:

- `series` is a cold-start-honest capture: `since_ts` says when observation began,
  and a range that nobody observed is an empty array, not a flat line.
- `alerts` is derived server-side so two clients cannot disagree about whether the
  engine is stale. `since_ts` is when the alert was *computed*, not when the
  condition started; the message carries the age.
- `signed` reports only the presence of a `sig=` segment. This process holds no
  key and cannot verify the chain; `status.py` does that.

## Environment

`ZTT_ROOT` points the server at the engine's tree (set by `start_dashboard.bat`;
the raw `.exe` would inherit `System32`). Neither is a setting the UI can change.

The interpreter that runs a scan is resolved in this order: `ZTT_PYTHON`, then the
project's own `.venv` (`.venv/Scripts/python.exe` on Windows, `.venv/bin/python`
elsewhere), then `py -3` / `python3` as a last guess. The launcher's guess has no
`MetaTrader5` on this machine, which is why the venv comes first: a scan started
through the guess fails closed on import and reports `FAILED` for the wrong
reason.
