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
Tier 1  universe filter      ~1,700 symbols -> ~600 tradable, liquid, live
Tier 2  strategy matrix      3 families x all symbols, correlation-deduped
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
| `marketdata.py` | the only module that talks to the terminal |
| `position_manager.py` | continuous supervision of open positions |
| `trials_total.py` | cumulative trial count (feeds the significance bar) |
| `status.py` | read-only diagnostic: terminal, policy, tasks, universe |
| `dashboard/` | Rust read-only viewer; tails files, never mutates state |

### One tree

This repository is **the** tree:
the git repository, the execution environment Task Scheduler runs, and the
directory the ledger lives in. There is no second copy and nothing to sync.

| What | Where |
| --- | --- |
| Code | this repository (tracked) |
| Execution | the same directory — `HermesMT5Funnel` runs `run_funnel.local.cmd` here, `HermesMT5Rebuild` runs `run_rebuild.local.cmd` here |
| Ledger | `funnel_history.log`, `trades.csv`, `stops.json`, `liquid_universe.json`, `engine_state.json`, `audit.jsonl`, `idempotency.db` — same directory, gitignored |
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
- **The dashboard reads this directory.** `start_dashboard.bat` sets `ZTT_ROOT`
  to the parent of `dashboard\` (this tree). Launch it through the `.bat`, never
  the raw `.exe` — the exe would inherit `System32` as its working directory and
  find no files.
- **Publication is a build, not a tree.** `build_public.py` sanitises a copy into
  `_publish/` and refuses to ship if it finds a machine path. Never edit
  `_publish/`; rebuild it.

---

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
| `GET /ws` | read | updates on change, plus a 1 Hz heartbeat so `engine_age_s` keeps ticking |
| `GET /architecture` | read | the stage map, embedded from `dashboard/web/architecture.html` |

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

`desk.py` + `agent_brief.py`: three LLM roles that read this tree and write
`agent_notes.json`. They are **advisory**. Nothing they produce can place, size,
cancel or veto anything, and there is no code path from this module to the
terminal — `AGENTS.md` §1 still owns execution.

| Role | Reads | Produces | Why a model |
| --- | --- | --- | --- |
| `scout` | RSS headlines + the snapshot | symbol mapping, stance | text, not arithmetic |
| `skeptic` | one candidate's own test output | reasons it is noise | adversarial prose |
| `chief` | the ledger, tier2/tier3, engine state | one paragraph + contradictions | synthesis |

**The citation rule.** Every number a note contains must already appear in the
artifact the model was given. `desk._guard()` rejects the whole note otherwise
(`status: "rejected"`, reason names the figures) and the run is marked rejected.
This is what makes an LLM safe next to a quantitative pipeline: it cannot invent
a z-score, a win rate or a trial count, and a rounded rendering of a real number
(`4.53` for `4.531`) is still a citation.

It never runs inside the scan. A scan must not depend on a network call or a
model's mood; `agent_brief.py` runs after it, writes one derived file and one
audit row.

```bash
python agent_brief.py --offline     # print the fact sheet, call nothing
python agent_brief.py               # needs DEEPSEEK_API_KEY (env or .env, never committed)
```

No key is a **failure**, not a quiet skip: the CLI exits 2 and the dashboard card
says "no model key — nothing was called". Feeds live in `config.toml`:

```toml
[agents]
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
| `mt5.symbol_select()` leaks Market Watch entries | Market Watch grew to 1,628; always call `release_auto_selected()` |
| EA / indicator warm-up NaNs | MACD signal went all-NaN and the family silently never fired |
| Forming-bar inclusion | Look-ahead bias; `fetch` drops the in-progress bar by design |
| Reporting a stale file as fresh | `report_funnel` prints `[STALE]`; keep the 3-hour guard |
| Overlapping scheduled runs | `funnel.lock`; outside the window the process exits in ~0.3 s |
| Floating-point equality at threshold boundaries | test fixtures sit clear of exact 1R rather than pinning the boundary |
| Task Scheduler stores **local** time | Europe/Paris shifts at DST; enforce any critical UTC window in Python, not in the trigger |
| `Last Result=267011` | `SCHED_S_TASK_HAS_NOT_RUN` — the task is registered but has never fired. Not an error; check the trigger start boundary |
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
| Amber **engine Ns stale** | The supervisor is not running, or is writing to a different tree. The UI is showing old data — **not** a quiet market |
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
