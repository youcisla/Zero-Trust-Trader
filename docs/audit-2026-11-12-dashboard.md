# Dashboard audit 2026-11-12 — disposition

A second audit, 16 findings against `main @ 383c605`, all confined to `dashboard/`.
This is what was applied, what is deferred, and how each applied item is now
enforced by a machine rather than by memory.

## D0 — contract repairs

| Finding | What changed | Enforced by |
| --- | --- | --- |
| H1 — `/architecture` executed generator JS with full origin authority | served with `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src data:; img-src data:; sandbox allow-scripts` | verified live: the diagram still renders (toolbar, theme, export, 519 nodes) and the header matrix shows the policy |
| M1 — API.md drifted in three places | staleness 5 s → 8 s (4 s resend stated); POST failure column gained **409** and **500**, plus the two debounce semantics; the venv-first resolution documented; the alert enum defined | `ui.test.cjs`: "API.md states the numbers the code implements" greps the doc for 202/409/429/503/500, `.venv`, "eight seconds", "every 4 s" and checks the code still implements them |
| M2 — desk_error said `bad`, client painted it INFO | server emits `error`; API.md defines `error | warn | info` as the whole vocabulary | `health.rs` asserts the word; the node suite asserts the doc and that no client branch speaks `bad` |
| M6 — no icon for the 7th route; test hardcoded six | icon added; test now derives ROUTES and ICONS from the source and asserts every key has an icon and a render branch | `ui.test.cjs` route test — it computes what its name claims |
| L6 — `esc()` outside the tested block | moved inside, exported, five assertions including a script payload | `ui.test.cjs` |
| L4 — the report page was the one framable HTML | `frame-ancestors 'none'` added to its own policy | `cargo test` csp test asserts both headers |
| M4 — 2.10 MB re-served `no-store` per reload | `/vendor/*` immutable for a year; `/architecture` ETag + 304 | live: `curl -D` shows the directives; `If-None-Match` answers 304 |
| L1 — `fetch_add` before the upgrade, no `on_failed_upgrade` | the count decrements on a failed upgrade | `cargo test` drives 16 handshakes, the 17th gets 503, and the count returns to 0 on drop |
| L2 — `.venv` stat on the reactor | joins the existing `spawn_blocking` probe | code |
| L3 — a failed spawn consumed the debounce | the timestamp is stamped only after a successful spawn | code + the journal shows 503 rows |

## D1 — the regression wall

| Test | What it pins |
| --- | --- |
| `a_route_that_sets_its_own_csp_keeps_it` | through the real router: the report route keeps its sandbox, and `/` still gets the middleware default. The pre-fix code fails this by construction |
| `a_scan_that_runs_is_refused_twice_and_its_exit_code_is_recorded` | a stub that exits 7: 202, then 409, then `last_scan_run.exit == 7`, and both the 409 and the exit are in the journal |
| `the_seventeenth_socket_is_refused_and_a_closed_one_frees_its_slot` | 16 upgrades, the 17th 503, and every slot returned |
| node: severity / esc / ROUTES-ICONS / API.md | the four contract pins |

## D2 — operational honesty

| Finding | What changed |
| --- | --- |
| M3 — the one privileged action left no record | `dashboard_scan.jsonl`, monitor-owned, one row per POST with the outcome and the exit code |
| M7 — the monitor was unsupervised | `dashboard_heartbeat.json` every 5 s; `status.py` reports the age and says SILENT; `HermesMT5Dashboard` registered with restart-on-failure |

## Deferred, with the reason

| Finding | Why |
| --- | --- |
| M5 — move the inline app script to `/vendor/app.js`, then `script-src 'self'` | Mechanical, but it splits the single embedded file the build and the fixture harness both assume: `ui.test.cjs` reads the helper block out of `index.html`. Doing it properly means teaching the harness the second file in the same change. One evening, not five minutes. |
| L7 — the diagram's evidence pin is at a8da643 | Regenerating needs the Archify generator, which is not installed on this machine. The alternative the audit offers — labelling the pin on the page — is a hand edit to a generated file, which the repo forbids. It is stale, and it is marked as such here. |
| L5 — 1 Hz full-frame broadcast | The mtime gate is applied: an unchanged file is not re-parsed (stat + hash-keyed cache). The full frame is still sent every beat, deliberately: `ts` and `engine_age_s` advance every second, so a byte-identical frame never exists. Cutting the wire size means changing the contract the client renders, which needs its own pass. |
