# AGENTS.md

Instructions for AI coding agents (Claude Code, Codex, Cursor, Hermes, or any
harness) working in this repository.

**This project places orders against a live trading terminal API.** A well-meaning
refactor here can lose real money in seconds. Read this file before touching
anything under `run_funnel.py`, `position_manager.py`, `tier3_deep.py`, or
`policy_check.py`.

---

## 1. The invariant everything else serves

> **The agent may research freely and may change the system. It may not trade.**

Enforcement is structural, not behavioural:

- `AutoTrading` is off. No code path in this repository enables it.
- Orders require a human typing `APPROVED` **and** an independent consent gate in
  the MCP bridge, which returns a single-use approval token with idempotency keys.
- The pre-flight refuses any terminal whose server name does not contain `demo`.

If a change you are considering would weaken any of those three, stop. It is not
a trade-off, it is the product.

---

## 2. The pipeline

```
Tier 1  universe filter      1,711 quoted -> 349 tradable, liquid, live at 11:28 UTC
        liquidity build      every survivor, not a top-N: FX, indices, commodities,
                             crypto AND shares (180 at the 2026-11-11 rebuild, 148 shares)
Tier 2  strategy matrix      3 families x every symbol with fresh bars
Tier 3  deep test            15k bars, 10k Monte-Carlo paths, BOTH gates
   |
   v
human APPROVED  ->  bridge consent  ->  order
   |
   v
position_manager.py          trails stops, break-even, session-close (24/5)
   |
   v
dashboard/                   read-only viewer (Rust), tails files, never writes
```

| File | Owns |
| --- | --- |
| `run_funnel.py` | orchestration, pre-flight, lock, UTC run window |
| `tier1_universe.py` | tradability filter (live, spread/ATR, history floor) |
| `tier2_patterns.py` | 3-family matrix, correlation dedupe, `total_tests` |
| `tier3_deep.py` | deep test, **owns** the `funnel_history.log` line |
| `timemachine_mt5.py` | strategy engines, `bonferroni_z`, `permutation_test` |
| `marketdata.py` | owns the terminal connection, the broker offset and the bar cache; `symbol_info()` and `account_currency()` are the read-only facts other modules ask it for |
| `position_manager.py` | continuous supervision of open positions |
| `trials_total.py` | cumulative trial count (feeds the significance bar) |
| `status.py` | read-only diagnostic: terminal, policy, tasks, universe |
| `dashboard/` | Rust read-only viewer; tails files, never mutates state |
| `tools/` | read-only diagnostics: broker arithmetic, sizing dispersion, ruin simulation, and `batch_backtest.py` — a pre-screen of every symbol on our own bars. They answer feasibility questions and never place an order |

### One tree

This repository is **the** tree:
the git repository, the execution environment Task Scheduler runs, and the
directory the ledger lives in. There is no second copy and nothing to sync.

| What | Where |
| --- | --- |
| Code | this repository (tracked) |
| Execution | the same directory — `HermesMT5Funnel` runs `run_funnel.local.cmd` here, `HermesMT5Rebuild` runs `run_rebuild.local.cmd` here |
| Ledger | `funnel_history.log`, `trades.csv`, `stops.json`, `liquid_universe.json`, `engine_state.json`, `audit.jsonl`, `idempotency.db` — same directory, gitignored |
| Desk record | `desk_history.sqlite` — same directory, **gitignored** (§9b); the published view is `docs/desk-history.jsonl`, written by `tools/export_desk_history.py` |
| Policy | `config.toml` at the root — gitignored, pinned by both the MCP `--config` argument and its own `[audit]` / `[idempotency]` paths |
| Dashboard | `dashboard/target/release/zero-trust-dashboard.exe`, launched through `dashboard/start_dashboard.bat` |

Rules that follow from this:

- **Commit the script and it is scheduled.** A new component goes into this tree;
  nothing has to be copied anywhere else first.
- **Runtime state is gitignored, never mirrored** (§4.5) — it carries the broker
  account number, so it must not appear in a commit, a copy or a publish build.
- **The launchers are local-only.** `run_funnel.local.cmd` and
  `run_rebuild.local.cmd` pin `MT5_EXPECTED_LOGIN` and are excluded through
  `.git/info/exclude`; the tracked `run_funnel.cmd` stays portable and sanitised.
- **The eight pages answer eight questions, in reading order.** `#/command`
  (default) is "is it alive, what did it decide, what do I hold". `#/pipeline` is
  the nine-stage canvas — scheduler, pre-flight, tier 1, tier 2, correlation
  dedupe, tier 3, verdict, consent, supervisor — with per-edge counts and a
  click-to-inspect drawer. `#/research` is the gate margin and the candidates;
  `#/universe` is every symbol as one measured number; `#/trials` is the
  accounting that sets the bar; `#/account` is equity, drawdown and outcomes;
  `#/desk` is the advisory chain under its own rule ("it explains, it never
  gates"); `#/ops` is health, alerts and the raw files. Keys `1`–`8` switch
  between them, `?` lists them, `Ctrl/Cmd+K` opens the palette. `#/flow`,
  `#/overview`, `#/performance` and `#/heatmap` redirect: a bookmark from last
  week is not a 404.
- **A refusal is calm.** `STAND_DOWN` is the system working, so its band is
  neutral/teal, never red or magenta. Magenta is the *advisory* hue and nothing
  else. `PROPOSAL` is amber — human review required, not an executable order —
  and only `FAILED` is red.
- **Two switches, one file.** `◑ light` and `⇕ compact` persist in
  `localStorage`; light re-points the same token block (dark for duration, light
  for glances) and compact changes air, never type size.
- **The footer is the boundary in one line.** "Monitor only — no execution
  authority", beside the snapshot timestamp and `schema_version`.
- **`/api/meta` is the contract, not decoration.** It names the one write and
  four capabilities that are false (`execution`, `approval`, `sizing`, remote
  bind). A test asserts they stay false.
- **One strip, one place per number.** `CmdStrip` sits under the top bar on every
  route and carries verdict, bar, best candidate, gap, trials, equity, positions,
  the last scan's exit code and the engine age. Nothing else in the chrome may
  repeat one of those values - the top bar carries alerts, the zone and the log
  copy, and nothing else. A number that appears twice is a number that can
  disagree with itself.
- **The gate is a picture, not a sentence.** `GateGauge` draws the bar as a dashed
  line, the best candidate as a marker, the bar's own history from the ledger, and
  `n = (bar / edge)^2` for three edge sizes - the same arithmetic as the README's
  "566 trades at +0.20R", and `bonferroniMirror()` is the one source for every bar
  the UI shows.
- **The equity series is memory, not history.** `collector.rs` samples the resolved
  engine state once per second into three ring buffers (2 h @ 1 s, 48 h @ 30 s,
  14 d @ 5 min) and downsamples with LTTB on the way out. Nothing is persisted and
  no file is created: a restart starts the record over, and the chart says
  `in-memory since <time>`. Do not "fix" this by writing a series to disk — that
  would make the monitor a writer in the engine's tree.
- **Alerts are derived on the server** (`health.rs`), so two clients cannot disagree
  about whether the engine is stale. The client renders `alerts[]` verbatim.
- **The ledger parser is bounded** (`ledger.rs`, 512 KiB tail, newest 400 rows). It
  reports `signed` as the *presence* of a `sig=` segment; it holds no key and must
  never claim a row is verified.
- **Charts cannot invent a point.** A range with no observations renders an empty
  state, never a flat line; a one-point series pins the axis to a minute so it
  cannot read as a day of silence.
- **The socket must prove it is alive.** The server sends the snapshot on change and
  once every five seconds regardless, and pings every 15 s; the watch loop logs a
  failed build and continues instead of ending (it used to `return`, which left every
  client holding an open socket that would never speak again). The client closes and
  reconnects itself when messages stop, with 0.5→5 s backoff, and retries at once on
  `online` or when the tab becomes visible. A half-open socket is the failure this
  prevents: it never fires `onclose`, so nothing recovers without the watchdog.
- **Long lists are paginated, not dumped.** The ledger (400 rows) and the candidate
  cards are filtered, sorted and paged in the browser — the data is already in the
  snapshot, so a filter is not a request. The canvas has a freeze control: a live
  graph is unreadable while a scan runs, and freezing holds the snapshot without
  touching what the engine is doing.
- **Progress is a file, not a channel.** `run_funnel.progress()` writes
  `scan_progress.json` at each stage boundary (atomically, and never failing the
  scan if it cannot). The UI reads it to show which stage is running, and stays
  ignorant of everything else. The engine still does not know the dashboard
  exists.
- **The dashboard reads this directory.** `start_dashboard.bat` sets `ZTT_ROOT`
  to the parent of `dashboard\` (this tree). Launch it through the `.bat`, never
  the raw `.exe` — the exe would inherit `System32` as its working directory and
  find no files.
- **Publication is a build, not a tree.** `build_public.py` sanitises a copy into
  `_publish/` and refuses to ship if it finds a machine path. Never edit
  `_publish/`; rebuild it.

---

## 2b. Nothing is finished while something still describes the old tree

**This is a standing rule, not a suggestion, and it is now partly mechanical.**
Every change — a family, a gate, a cap, a route, a threshold, a page, a tool —
lands together with everything that describes it. The tree has three records of
itself and they move in lockstep with the code, in the same commit.

| Artifact | Lives | Update when | Enforced by |
| --- | --- | --- | --- |
| `docs/architecture.archify.json` → `dashboard/web/architecture.html` | this repo (the map is generated, never hand-drawn) | **any change to the pipeline**: a stage, a cap, a file it names, a number in a tag | `tools/check_docs.py` — fails if a cited file is gone or a tag disagrees with the artifacts |
| `README.md` | this repo | any change a reader can see: endpoints, pages, tools, test counts, the bar, the universe | same check: every `tools/*.py` must be mentioned |
| the EISI mémoire | `../MemoireIPSSI/TradeBot_Memoire/` (beside this repo, never inside it) | any change to the thesis, the gates, the numbers or the operational surface | `python sync_toc.py --check` + a rebuild; advisory in `check_docs.py` because CI cannot see it |

```bash
python tools/check_docs.py        # exit 1 and a list of what disagrees — run before every commit
```

The map is **generated**: edit the JSON, then re-run the Archify skill (its own
`bin/archify.mjs`; the path is machine-local, so take it from the skill, never
from this file) and rebuild the binary that embeds it. The receipts are
gitignored because they carry absolute paths.

```bash
rm -f dashboard/web/architecture.*.json
node "$ARCHIFY/bin/archify.mjs" finalize architecture \
  docs/architecture.archify.json dashboard/web/architecture.html \
  --quality showcase --repo-root . --json     # four gates must pass
cd dashboard && cargo build --release           # the HTML is embedded with include_str!
```

Why this is a rule and not a habit: the map said `1,723 → 120` for a full day
after the universe became 248, because nothing read it but a human. Two numbers
per tag, typed by hand, is exactly the kind of thing that rots silently — so the
numbers in a tag are now checked against `liquid_universe.json` and the newest
`tier1_universe_*.json`, and CI fails on the parts that do not need runtime state.

The mémoire is a `.docx` built from `05_memoire_long.md`; never edit the docx by
hand, and never renumber the table of contents:

```bash
cd ../MemoireIPSSI/TradeBot_Memoire          # from this repo's root
uv run --quiet --with python-docx python build_docx.py
python sync_toc.py            # ToC page numbers are hand-typed text, not a field
python sync_toc.py --check    # exit 1 if stale
```

Rules that bind the mémoire (the jury has the ToC):

- **The ToC is frozen.** No new part, no new numbered subsection. New material goes
  in as **prose under a bold lead-in** inside an existing section.
- **Body 65–80 pages**, counted from "Introduction et problématique" to
  "Conclusion". Measure with Word, not with a word count: `ComputeStatistics(2)`
  plus the page of the introduction and of the conclusion.
- **Every number is a claim someone can falsify from this tree.** Read it from
  `tier3_results.json` (`trials_cumulative`, `z_crit_corrected`) — the pair the gate
  actually used — not from memory, and not from the README's historical example.

The full procedure, with the pitfalls that have already bitten (typographic
apostrophes, Word holding the file, `Find` matching the ToC first), is the
`eisi-memoire` skill.

## 3. The fail-closed gates

Every gate fails **closed**: when it cannot verify, it refuses.

| # | Gate | Where | Refuses when |
| --- | --- | --- | --- |
| 1 | Demo-only pre-flight | `run_funnel.preflight_check` | server name lacks `demo`, terminal disconnected, login mismatch |
| 2 | Run window | `run_funnel.in_window` | outside 05:00–17:00 UTC |
| 3 | Lock | `run_funnel.acquire_lock` | another run holds the lock (stale locks are taken over) |
| 4 | Correlation dedupe | `tier2_patterns` | two candidates above \|ρ\| 0.7 — keeps the higher z |
| 5 | Corrected z | `tier3_deep` | z below `bonferroni_z(cumulative_trials)` — **cumulative, never per-scan** |
| 6 | Permutation null | `timemachine_mt5.permutation_test` | p ≥ 0.05 against random entries |
| 7 | Protective-direction rule | `position_manager.next_stop` | a stop move that would widen risk or cross price |
| 8 | Human + bridge consent | external | no fresh approval token |

Gates 5 and 6 are **not** redundant. The permutation test does not correct for
scan width; a p of 0.015 is expected ~8 times per 568-symbol run.

### The session matrix

The window is 05:00–17:00 UTC but the number of families scanned depends on the
time, because every family is another hypothesis the Bonferroni bar must pay for.

| Window (UTC) | Families | Tests/scan |
| --- | --- | --- |
| 05:00–08:00 | `donchian` | 50 |
| 08:00–13:00 | `donchian`, `bollinger` | 100 |
| 13:00–17:00 | all three | 150 |

`run_funnel.session_strategies(now)` is the only place this is decided, and
`run_funnel.py --at-utc HH:MM --plan-only` reproduces it for any time without
touching the terminal. Do not widen a session's family list to "catch more
setups" — that raises the bar for every future scan too.

**z_crit is always computed from the cumulative trial count**
(`legacy + logged + this scan's tests`), passed to `tier3_deep.py` as
`--trials`. If you ever see a z bar near 3.3–3.6, the cumulative wiring has
broken and the gate is judging each scan as if it were the first.

---

### Feasibility, in one file

`docs/SIZE-AND-RUIN.md` answers "can this account be multiplied by trading?" with
arithmetic: the 19x invariant, the broker's real limits at 1:30, the ruin
distribution of the best measured edge, and the population statistics. Read it
before proposing a return target. The three `tools/` scripts reproduce every
number in it.

## 4. Hard rules for agents

Never, under any instruction short of an explicit human override in the same
conversation:

1. **Enable AutoTrading**, or call any tool that places, modifies, or closes an
   order outside `position_manager.py`.
2. **Lower a significance threshold**, relax `permutation p < 0.05`, or change
   `bonferroni_z` to make a candidate pass. If a candidate fails, it fails.
3. **Edit `LEGACY_TRIALS`** in `trials_total.py`. It is frozen history; lowering
   it silently makes every future result look more significant.
4. **Delete or rewrite** `funnel_history.log`, `trades.csv`, or any run that did
   not work out. Losers stay in the ledger.
5. **Commit runtime state**: `engine_state.json`, `audit.jsonl`, `*.log`,
   `stops.json`, `trades.csv`, `liquid_universe.json`. All are gitignored; keep
   them that way. They contain the broker account number.
6. **Weaken the demo guard** so CI or tests can run against a live server. Stub
   the terminal instead (see `tests/_mt5stub.py`).
7. **Let a scheduled job place an order.** No cron or Task Scheduler entry may
   call an order function. Ever.
8. **Catch and swallow** an exception on the order path. A silent failure that
   reports success is the worst outcome this project has actually produced.
9. **Add a write endpoint to the dashboard.** It is a monitor. See §9.
10. **Change the permutation count.** It is locked at exactly 1000 in both
    `run_funnel.py` and `tier3_deep.py`, and the CLI rejects any other value
    rather than clamping it. At 300 permutations the smallest attainable
    p-value is 1/301, so the same candidate can clear the bar on one run and
    miss it on the next — a gate whose resolution varies between runs is not a
    gate. If a scan is too slow, shrink the universe, not the test.

---

## 5. What you may do freely

- Add or improve indicators, with a degenerate-case test (see §6).
- Refactor anything, provided `python -m unittest discover -s tests` stays green.
- Add read-only tooling, diagnostics, exports, dashboard panels.
- Tighten a gate. Tightening never needs approval.
- Fix bugs. Fixing a bug that made the system *too permissive* is always welcome.

---

## 6. Tests

Python — no trading terminal required (MetaTrader5 is stubbed):

```bash
python -m unittest discover -s tests -v
```

Rust dashboard:

```bash
cd dashboard && cargo build --release && cargo clippy -- -D warnings
```

CI runs both on every push and additionally greps **tracked files** and fails the
build if a machine path, the broker account number, or a key prefix appears.

If you change an indicator, add the degenerate case: flat series, zero range,
all-equal bars, series shorter than the lookback. Two real bugs in this codebase
were silent — RSI returning 100 on a flat series (a dead instrument reading as
overbought and firing a false short) and MACD's signal line being all-NaN (a
strategy family that could never fire). Neither raised an exception.

---

## 7. Modifying a strategy without violating safety

1. **State the hypothesis** and the exact instrument/timeframe you will measure it on.
2. **Measure before and after** on the same data, same costs. Keep the numbers.
3. **Count the trials you added.** New family or new parameter sweep = new
   hypotheses. `tier2_patterns` must report them in `total_tests`, or the
   correction is wrong and every downstream z is inflated.
4. **Do not touch the gates** to make the new family pass. A new family that
   only survives a relaxed gate has no edge.
5. **Add the test** for the degenerate case before you add the feature.
6. **Report a negative result as a result.** "No edge found" is the expected
   outcome; the system defaults to standing down and that is working correctly.

### Adding a strategy family

- `timemachine_mt5.py`: a `scan_<family>()` returning the same trade dict shape.
- `tier2_patterns.py`: register it in the matrix so its tests are counted.
- `tier3_deep.py`: no change — it iterates whatever the matrix produced.
- Prove it is not a duplicate of an existing family (that is why MACD was
  removed: a signal-line cross is trend-following, same exposure as Donchian).

---

## 8. Modifying the risk supervisor

`position_manager.py` may only **reduce** risk. Any change must preserve:

- `next_stop` returns `None` rather than a stop that moves away from price;
- long stops stay below the live price, short stops above it;
- `--apply` remains required to send anything (dry run is the default);
- no code path opens, enlarges, or reverses a position.

`tests/test_position_manager.py` asserts these as a property sweep over 3,000
randomised positions. If your change breaks it, your change is wrong — not the test.

The supervisor takes its lock with `O_CREAT | O_EXCL` (`position_manager.acquire_lock`),
not by checking-and-writing: two supervisors starting in the same millisecond once
both slipped through and could both move stops. A stale lock is still taken over —
a crashed supervisor must not stop the system forever.

**The supervisor runs in dry-run mode until there is a real open trade to manage.**
Do not add `--apply` to any scheduled task speculatively.

---

## 9. The dashboard (`dashboard/`)

A local **read-only** monitor. Rust/Axum backend, React UI embedded in the
binary, streaming over a WebSocket.

### Launching it

```bash
cd dashboard
cargo run --release          # builds if needed, serves http://localhost:8787
```

Options: `--port 8787` (default), or set `ZTT_ROOT` if the engine writes its
files somewhere other than the repo root. It resolves the repo root automatically
when launched from either the repo root or `dashboard/`.

### It is strictly read-only — this is a design rule, not a preference

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `GET /` | read | the UI (single embedded HTML, React vendored — no CDN, no build step) |
| `GET /api/snapshot` | read | one-shot state |
| `GET /ws` | read | the snapshot on change (checked at 1 Hz), resent at least every 5 s so a quiet tree cannot be mistaken for a dead feed; protocol ping every 15 s; age advances in the browser |
| `GET /architecture` | read | the stage map, embedded from `dashboard/web/architecture.html` |
| `POST /api/scan` | **write** | starts `run_funnel.py` — the *research* pipeline. The only write in this process |
| `/vendor/echarts.min.js` | read | the chart library, embedded from a pinned npm tarball |

`POST /api/scan` is the one exception and it is narrow. It takes no argument, so
the button runs exactly what the scheduler runs: the funnel's own lock (a double
click costs 0.3 s), its UTC window, its demo-only pre-flight. It cannot place,
modify or close anything — order entry does not live in the scan path at all.
Adding a second write endpoint requires deleting this paragraph first.

- **Never add** an endpoint, button, or socket message that places, modifies, or
  closes an order. If someone asks for one, the answer is no: execution goes
  through the consent gate, not through a web UI with no approval step.
- **Never make the engine depend on the dashboard.** There is no IPC and no
  callback. The backend tails files the engine already writes:
  `engine_state.json`, `funnel_history.log`, `position_manager.log`,
  `audit.jsonl`, and — for the pipeline canvas — the two stage artifacts
  `tier2_candidates.json` and `tier3_results.json` (absent outside a scan
  window, which renders as unknown, not as zero). Kill the dashboard and trading is unaffected; kill the engine
  and the UI shows an amber age badge instead of inventing data.
- **React is vendored** in `web/vendor/`. Do not swap it back to a CDN — that
  reintroduces an offline failure mode and an SRI hash to maintain.
- Client WebSocket messages are intentionally ignored by the server.
- **The canvas must fit the viewport.** Six stages are a CSS grid (6/3/2 columns)
  and the wires are measured from the nodes with a `ResizeObserver`. Do not
  reintroduce a fixed node pitch and a horizontally scrolling track — the page
  must never scroll sideways.
- **The tail is bounded.** `tail()` seeks to the last 16 KiB instead of reading the
  whole file, because the ledger is append-only and grows forever. Do not go back
  to `read_to_string` on a file the engine appends to.
- **The architecture diagram is generated, not drawn.** `docs/architecture.archify.json`
  is the source; regenerate with the Archify skill into
  `dashboard/web/architecture.html`, then rebuild — the binary embeds it, so the
  route cannot serve a stale or half-written file. `.archify/` working folders
  are ignored because their receipts carry absolute machine paths.

### Why not Next.js

A single-page localhost dashboard streaming one socket does not need SSR, a
routing layer, or a Node toolchain in CI. One Rust process, no `node_modules`,
no second port, no CORS.

---

---

## 9b. The desk (agents)

`desk.py` + `agent_brief.py`: four LLM roles that read this tree and write
`agent_notes.json`. They are **advisory**. Nothing they produce can place, size,
cancel or veto anything, and there is no code path from this module to the
terminal — `AGENTS.md` §1 still owns execution.

| Role | Reads | Produces | Why a model |
| --- | --- | --- | --- |
| `scout` | RSS headlines + the snapshot | symbol mapping, stance | text, not arithmetic |
| `skeptic` | one candidate's own test output | reasons it is noise | adversarial prose |
| `chief` | the ledger, tier2/tier3, engine state | one paragraph + contradictions | synthesis |
| `jury` | the facts, the skeptic's objection, the chief's summary | `stand_down` / `escalate` / `propose`, plus conditions | the last voice before a human, and the only role whose `size_lots` is policed: anything but 0.01 is rejected |

Each role's card in the dashboard carries what that role **spent** — `5.9k in · 801 out` —
read from `agent_notes.json`'s `role_metadata[role].usage`, and the footer sums it. A
role that was never called shows no line at all: `null` is not zero here either.

`desk_store.py` keeps every answer the desk ever gave in `desk_history.sqlite`
(one row per run, one per role), and exports the last 24 runs to
`desk_history.json` for the dashboard. `agent_notes.json` is the current state;
the database is the record, and it is the only one that answers "what did the
judge say before the last three scans?":

```bash
python desk_store.py --history 20      # the last 20 runs, every role
python desk_store.py --query jury      # every jury answer, newest first
```

**`desk_history.sqlite` is runtime state and is no longer tracked.** It grows on
every 15-minute run, it carries third-party headline text, and two machines
editing it produce a binary merge conflict every time — it was re-committed 14
times before the export existed. What ships is a **view**: `tools/export_desk_history.py`
writes `docs/desk-history.jsonl` (last 100 runs; role, status, headline, token
counts; every string through `desk_store._redact`). No raw payloads, no prompts,
no feed bodies. The broker login and any machine path are scrubbed before the
write, and `tests/test_desk_store.py` fails if either survives into the database.

**The citation rule.** Every number a note contains must already appear in the
artifact the model was given. `desk._guard()` rejects the whole note otherwise
(`status: "rejected"`, reason names the figures) and the run is marked rejected.
This is what makes an LLM safe next to a quantitative pipeline: it cannot invent
a z-score, a win rate or a trial count, and a rounded rendering of a real number
(`4.53` for `4.531`) is still a citation.

`HermesMT5Desk` runs `run_desk.local.cmd` every 15 minutes with `--window-only`,
so the four roles brief on the same cadence as the funnel and call nothing outside
05:00–17:00 UTC. A green scheduled run outside the window means "nothing to do",
never "the desk worked all night".

It never runs inside the scan. A scan must not depend on a network call or a
model's mood; `agent_brief.py` runs after it, writes one derived file and one
audit row.

```bash
python agent_brief.py --offline     # print the fact sheet, call nothing
python agent_brief.py               # needs DEEPSEEK_API_KEY (env or .env, never committed)
```

No key is a **failure**, not a quiet skip: the CLI exits 2 and the dashboard card
says "no model key — nothing was called".

**Pin what the provider actually serves.** The account answers `deepseek-chat`
with `deepseek-flash`; the first live run caught that through `model_drift`
(asked vs served, logged in the notes and the journal), so the pin is now
`deepseek-flash`. If a future run reports drift again, the model changed under
the thresholds, and that is a finding rather than noise. Feeds live in `desk.toml` — **not** `config.toml`, whose schema is the MCP
bridge's own policy and rejects unknown sections:

```toml
news_feeds = ["https://example.com/rss"]
```

Without feeds the scout reports `no feed configured` and does not call the model.

**What has no agent, on purpose:** chart reading, strategy testing, sizing and
execution. Tier 2 and Tier 3 already do the first two with a measured error rate
and a cumulative bar; sizing is 0.01 lots; execution is the human gate. An agent
in any of those four places is the exact thing this repository exists to prevent.

## 10. Known traps (each one has already happened here)

| Trap | Symptom |
| --- | --- |
| `mt5.symbol_select()` leaks Market Watch entries | Market Watch grew to 1,628; always call `release_auto_selected()`. It only releases what *this* process selected, so an accumulated leak survives every run: it sat at 1,626 of 1,723 until it was cleared by hand. Count with `[s for s in mt5.symbols_get() if s.visible]` — `symbols_get()` alone returns the whole server (1,723) and overstates the leak by ~97 |
| EA / indicator warm-up NaNs | MACD signal went all-NaN and the family silently never fired |
| Forming-bar inclusion | Look-ahead bias; `fetch` drops the in-progress bar by design |
| Reporting a stale file as fresh | `report_funnel` prints `[STALE]`; keep the 3-hour guard |
| Overlapping scheduled runs | `funnel.lock`; outside the window the process exits in ~0.3 s |
| Floating-point equality at threshold boundaries | test fixtures sit clear of exact 1R rather than pinning the boundary |
| Task Scheduler stores **local** time | Europe/Paris shifts at DST; enforce any critical UTC window in Python, not in the trigger |
| `Last Result=267011` | `SCHED_S_TASK_HAS_NOT_RUN` — the task is registered but has never fired. Not an error; check the trigger start boundary |
| Server leverage is **1:30**, not 1:500 | At 50 EUR only EURUSD/0.01 is affordable (33.40 EUR of margin, 186 pips to stop-out). XAUUSD needs 183 EUR of margin, US30 234, BTCUSD 333 — they cannot be opened on a small balance at all. Read it from the terminal (`mt5.account_info().leverage`), never assume it |
| `symbol_info()` before `symbol_select()` | An unselected symbol returns a struct whose `spread` is 0, and a zero spread makes the backtest a cost-free market: BCHUSD measured 372 trades at −0.007R instead of the real 464 at −0.830R. Select first, then read the quote |
| `--horizon` defaults to **32**, but `HORIZON_DEFAULT["M15"]` is **96** | `analyse()` only falls back to `HORIZON_DEFAULT` when `horizon is None`, so any caller that omits it silently backtests a 24-hour hold while the pipeline measures an 8-hour one. The two produce different trade sets on the same bars |
| **An escalation that fires 76% of the time is not an escalation** | The jury escalated on 34 of 45 judged runs, always for the same reason: the digest showed two stages of one scan as raw JSON, so `tier2.close` against `tier3.reference_bid` (a few seconds apart) and `significance.z` against `permutation.actual_z` (two different statistics) read as contradictions. The digest now opens with a `FIELD LEGEND` naming what each figure is and when it was taken, and the chief and jury prompts say a disagreement the legend explains is not one. Measure a gate's firing rate before trusting its output — `tools/desk_scorecard.py` exists to do exactly that, and `--since` splits it at the moment of a change. **An escalation must name the action a person takes** or `_validate_note` refuses it: a complaint without an action costs attention and returns nothing |
| **A normal quantile is not a t quantile** | The mean-R gate compares a Student-t statistic, so its bar must come from the t distribution at `df = n - 1`. At 25 trades the normal bar's true two-sided tail is 26x its nominal value: the stated family-wise error was fiction exactly where the deep test lives. `student_t_crit()` is pinned against published tables at df 1/2/5/10/25/50/100/200, and the normal bar is still reported beside it rather than deleted |
| **A route that runs scripts states what may run** | `/architecture` served 798 KB of generator output with four script blocks under the default policy - the whole origin's authority - one route from the report page that was sandboxed for exactly that reason. It is now `default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src data:; sandbox allow-scripts`: an opaque origin, so the viewer pans and exports but a fetch to `/api/snapshot` arrives with `Origin: null` and is refused. Verified in a browser, not just in a header |
| **Bytes you can hash, you can cache** | `/vendor/*` answered `no-store`, re-sending 1.15 MB per reload of files whose sha256 CI already verifies. They are now `public, max-age=31536000, immutable`; `/architecture` carries an ETag computed once and answers 304. Everything else stays `no-store` |
| **The one write leaves a record** | The engine's thesis is that every hypothesis lands on a verifiable ledger, and the dashboard's own write - a human starting a scan - left an in-memory object and nothing else. `dashboard_scan.jsonl` (the monitor's file, never the engine's ledger) holds one row per POST with the refusal code, and `dashboard_heartbeat.json` lets `status.py` say the monitor is live or silent. It is registered as `HermesMT5Dashboard` with restart-on-failure |
| **A middleware that inserts headers clobbers the handler's** | The response middleware re-inserted `Content-Security-Policy` on every response, so the report route's `sandbox` policy was replaced by `frame-ancestors 'none'` and the backstop the code argued for never ran. Set a default only when the handler did not: `contains_key` first, then insert |
| **`started: true` is not "it ran"** | The scan button reported a spawn, not an outcome. The child's exit code is now waited for, recorded and served in `last_scan_run`, and a second POST while one is alive is refused - the five-second debounce could not answer "still running" for a scan that takes minutes |
| **One marker, two readers, zero switches in the UI** | `KILL_SWITCH` at the tree root (or `ZTT_KILL_SWITCH=1`) freezes the scan and the supervisor: `run_funnel` refuses before it touches the terminal, `position_manager` refuses at the top of every pass and exits **78** rather than spinning, and the dashboard only *reports* it — a test asserts the monitor never creates the file. A monitor with a switch is a control surface, and this process has exactly one write |
| **A refusal that costs 35 minutes is not a refusal** | The kill switch check sits before the terminal, the lock and the ledger in `run_funnel`, not after the universe rebuild. Order matters: a guard placed late still spends the money |
| **Break-even already pays the spread** | `next_stop` adds `spread` to the break-even candidate (`entry + side*spread`), so a flat exit is flat *after* the round trip, not before it. Two outside reviews have \"found\" this bug; it was fixed before either was written, and the comment says why. `be_lock_r` is the separate knob: it locks an extra `n x risk` past flat, default 0 |
| **A 35-minute question should cost five seconds** | `run_funnel --preflight` answers the kill switch, the trial chain, the terminal, the broker offset and a closed-bar fetch in **0.04 s** - without taking the lock or writing anything. It is the first thing to run, and the last thing that can refuse cheaply |
| **`--watch-until-flat` stops on `[]`, never on `None`** | The supervisor exits 0 when the broker reports an empty position list and keeps polling when it cannot ask. An unknown position is not a closed one; `flat_confirmed()` is the one predicate that draws that line |
| **A stop is a plan, not a guarantee** | `current.max_loss` is the loss AT the stop price, from the broker's own `trade_tick_value` (falling back to contract size). A gap fills past it, so the ticket prints the caveat on the same line as the number rather than in a footnote nobody reads. `None` when the terminal publishes neither figure — an invented number is worse than no number |
| **The live exit path has no take-profit** | `_resolve_live` walks stop, trailing, break-even, session close and horizon — never `target`. `target` counts 0 in every record, and `rr` is inert there. That is deliberate (`position_manager` sets no TP), and it is why a 24% win rate is not a bad score: the wins are whatever the trail banks, not a fixed 1.5R. `tools/exit_policy_ab.py` measures the alternative |
| **The universe is smaller than "everything"** | Three caps, not one. `--top` (after dedupe), `--pool` (**before** it), and `--max-idle 150` in tier 1. Both caps default to **0 = everything measured** in `run_funnel.py`; `make_liquid.py` still defaults to `--top 120 --pool 150` when called by hand, which asks "is this market open *right now*". A rebuild run at 14:04 UTC excluded 1,070 US shares as `market_closed` because the session had not opened; the liquidity stage then measured 254 of the 381 tier 1 returned, deduped 66 correlation-clones, and kept 188. Rebuild after 14:30 UTC for the US and after 07:00 for Europe; the union across sessions is not reachable in one pass |
| **A diagnostic `--top` overwrites the universe** | `make_liquid.py --top 1` wrote a one-symbol universe over 188 and the next scan reported `universe=1 tested=1` as though the market had narrowed to EURUSD. The writer now refuses to replace a universe with less than half of it unless `--force`, and `tests/test_make_liquid_guard.py` fails if that inequality leaves the file |
| **Rebuilding outside the lock** | `tier1_universe.py` and `make_liquid.py` drive the terminal, so they now take `funnel.lock` themselves and accept `--lock-held` when `run_funnel --rebuild` already has it. A hand-run rebuild used to race a scheduled scan — two processes reading bars and re-selecting symbols is how a symbol's spread reads 0, and a zero spread turns a backtest into a cost-free market with no error |
| **"Run the universe in batches of 50, in parallel"** | One terminal, one IPC channel: two processes calling `copy_rates` at once is how `(-10005, 'IPC timeout')` and a corrupted Market Watch happen, and `funnel.lock` exists to stop it. Sequentially it is worse, not safer — each batch runs its own top-5 shortlist, so 250 symbols get 25 deep tests instead of 5, and the permutation gate (`p < 0.05`) is per candidate, never corrected across candidates. More deep tests means more chances to surface noise at the same bar. Nothing is lost by running one scan: every tier-3 failure writes `total_tests=<n>` to the ledger and `trials_total` counts it |
| An empty `liquid_universe.json` | A weekend `--rebuild` measured a dead terminal and overwrote a good universe with `symbols: []`; every scan then reported STAND_DOWN with `total_tests=0` for two days. Both sides now refuse: `make_liquid` will not write an empty universe, `run_funnel` logs `FAILED ... 0 symbols` |

---

## 11. When you are unsure

Do nothing, and say what you could not verify. **Standing down is a correct
outcome here** — the system reports exactly that whenever it cannot establish an
edge, and it will do so for weeks without being wrong. An agent that refuses to
trade has done its job; one that "makes it work" has broken it.

---

## 12. How to monitor the system

**Open <http://localhost:8787>.** That is the primary check — it needs no agent,
no terminal and no chat session.

| What you see | What it means |
| --- | --- |
| Green **live** dot | WebSocket connected to the Rust server |
| Large equity figure | Pushed ~every second from `engine_state.json` |
| Amber **engine 2h 34m stale** | The supervisor is not running, or is writing to a different tree. The UI is showing old data — **not** a quiet market. Durations are one helper (`fmtDur`): `45s`, `2m`, `1h`, `1h 30m`, `1d 1h` |
| Positions table | Empty = flat; the funnel is standing down |
| Supervisor actions | Every stop move made, or that would be made in dry run |
| Funnel history | Last 12 verdicts |
| MCP tool calls | Read from `audit.jsonl`; empty means no mutating tool was ever called |

- It launches **at logon** from `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`
  → `dashboard\start_dashboard.bat`. That batch file sets `ZTT_ROOT` to the
  this tree; the raw `.exe` would inherit `System32` as its working
  directory and find no files, so always launch it through the `.bat`. If the
  panel is missing, run that `.bat` manually.
- The dashboard is a **monitor, never a control surface**. Nothing on it can
  place, modify or close anything — see §9.
- For what the dashboard does not show — policy in force, Task Scheduler
  next/last run, trials and the current z bar, the liquid universe — run
  `python status.py` from this tree. That is the authoritative read.

### The supervisor is not scheduled, on purpose

`start_supervisor.bat` exists in this tree but is **not** in
Task Scheduler. It runs `position_manager.py --interval 5` with **no `--apply`**,
so it can only print what it would do.

Add it to Task Scheduler **the moment the first real position is open** — not
before. With no positions there is nothing to supervise, and an unsupervised
process that can modify stops is a liability with no upside. When that day comes,
watch it dry-run the live position first, then add `--apply` only after the log
shows it moving stops in the protective direction.
