# Zero-Trust Trader

[![CI](https://github.com/youcisla/Zero-Trust-Trader/actions/workflows/ci.yml/badge.svg)](https://github.com/youcisla/Zero-Trust-Trader/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

> **My AI agent has no authority to lose my money.**

An autonomous trading-research pipeline for MetaTrader 5 where the agent may look at
everything and do almost nothing.

The system's own current verdict:

```
Market is choppy, no statistically significant edge found today. Standing down.
```

That is not a failure message. That is the product.

---

## The one rule

**AutoTrading is off.** Always. It is enabled only inside an approved trade
window, and the agent cannot enable it at all.

Every **entry** needs two independent approvals:

1. a human typing the literal word `APPROVED`, and
2. the MCP bridge's own consent gate — a separate process that returns an
   approval token the agent must echo back, with idempotency keys and a JSONL
   audit trail.

The agent cannot synthesize either one. It can only ask.

---

## Why this is the interesting problem

Anyone can wire an LLM to a broker API. The hard part is not execution — it is
building something that **refuses to trade**, and being able to prove the refusal
was correct rather than an excuse.

That requires two things most trading bots never build: an honest measure of how
many hypotheses were tried, and a gate that survives the answer being *no*.

---

## The result that justifies the whole design

> **Superseded 2026-11-05.** These numbers came from a backtest that did not
> match live execution: no EMA trend gate, no spread, and a fixed +1.5R target
> the supervisor never sets. The Time Machine now imports Tier 2's gate and the
> supervisor's own `next_stop()`, and charges the spread — enforced by
> `tests/test_parity.py`. Every figure below is being re-derived under the
> corrected rule. Treat the table as history, not as evidence about the system
> as it runs today.

I scanned 568 instruments, deep-tested the five strongest, and one candidate
looked like a genuine edge:

| `HNR1d.DE` | |
| --- | --- |
| Trades | 304 |
| Win rate | 46.9% |
| Break-even needed | 40.0% |
| **z-score** | **+2.40** |
| Avg R | +0.192 |

z = +2.40 clears the conventional 1.96 threshold comfortably. Any single-symbol
study would have called that a finding and sized a position on it.

It was **refused**. Against the corrected bar for 576 tests (z = 3.92), +2.40 is
unremarkable — precisely what taking the best of 568 draws produces by luck.

The same instrument was later re-tested on four times the history:

| `ETHAUD/rsi` | 5,000 bars | 15,000 bars |
| --- | --- | --- |
| Trades | 134 | **566** |
| z-score | −0.18 | **−0.50** |

Four times the data did not reveal a hidden edge. It made the estimate *worse* —
exactly what happens when the original reading was noise. The earlier sample
size was not "too small to tell"; it was hiding nothing.

---

## Four in-repo gates, plus two external approvals

| # | Gate | Where it runs | Failure it catches |
| --- | --- | --- | --- |
| 1 | **Universe filter** — live market, spread ≤ 15% of ATR, ≥ 160 days of history | this repo | trading an instrument that is shut, illiquid, or newly listed |
| 2 | **Correlation dedupe** — collapse candidates above \|ρ\| 0.7 | this repo | `AMD.US-24` vs `AMD.US-PERP` measure **+0.997**: the same bet billed twice |
| 3 | **Bonferroni-corrected z** | this repo | the scan itself — 5,388 hypotheses tried means the bar is 4.43, not 1.96 |
| 4 | **Permutation null** — random entries, same fills, costs and exits | this repo | autocorrelation and barrier geometry that the analytic z assumes away |
| 5 | **Human `APPROVED`** | a human | the agent's own judgement |
| 6 | **Bridge consent gate** | a separate process | a compromised or confused agent |

Gates 1–4 are code in this repository and run on every scan. Gates 5 and 6 live
outside it: they govern *entry orders* and are only as strong as the bridge
deployment. Nothing in this repo can place an entry on its own — it has no order
path at all — and protective exits through the supervisor are single-approval by
design, because a stop you cannot move without a second signature is a stop you
cannot tighten when the market turns.

Gates 3 and 4 are not redundant. The permutation test is per-instrument and does
**not** correct for the scan: at 576 tests, a p-value of 0.015 is expected ~8
times per run. Passing one gate is not permission to trade.

---

## Architecture

```mermaid
flowchart TD
    SCH["Task Scheduler<br/>05:00–17:00 UTC"] --> PRE{"pre-flight<br/>demo · connected · lock"}
    PRE -->|refuse| STOP1([abort, nothing scanned])
    PRE -->|pass| T1["Tier 1 · universe filter<br/>~1,700 → ~600 symbols"]
    T1 --> T2["Tier 2 · strategy matrix<br/>3 families"]
    T2 --> DED{"|ρ| < 0.7<br/>correlation dedupe"}
    DED --> T3["Tier 3 · deep test<br/>15k bars · 10k paths"]
    T3 --> Z{"corrected z<br/>bonferroni(trials)"}
    Z -->|fail| SD["STAND DOWN<br/>the default outcome"]
    Z -->|pass| P{"permutation test<br/>p < 0.05"}
    P -->|fail| SD
    P -->|pass| HUMAN["human types APPROVED"]
    HUMAN --> BRIDGE["bridge consent gate<br/>single-use token"]
    BRIDGE --> ORDER[("order · demo only")]

    ORDER --> PM["position_manager.py<br/>24/5 supervisor"]
    PM -->|"protective stop moves + closes only"| ORDER

    PM --> STATE[("engine_state.json")]
    SD --> LOG[("funnel_history.log")]
    STATE --> DASH["Rust dashboard<br/>read-only · file-tailing"]
    LOG --> DASH
```

**Three strategy families**, because one is not enough: a breakout strategy fails
by construction in a sideways market.

| Family | Trigger | For |
| --- | --- | --- |
| `donchian` | close beyond the prior 20-bar channel + EMA trend filter | trending |
| `rsi` | RSI-14 crossing into <35 / >65 | chopping |
| `bollinger` | close crossing out of the 2.0σ band — **faded, not followed** | overshoots |

Two details that took measurement to find:

- **MACD was removed.** A line/signal cross is trend-following, so it duplicated
  Donchian's exposure instead of diversifying it. Adding a family raises the
  multiple-comparisons bar; it must also add a genuinely different bet.
- **RSI sparsity was a history-length problem, not a threshold problem.** Moving
  30/70 → 35/65 bought +70 trades. Moving 5,000 → 15,000 bars bought **+480**.
  Thresholds trade signal quality for volume; history *is* the sample.

**The matrix narrows with the session.** Scanning all three families for twelve
hours would add 7,200 hypotheses a day; narrowing the early sessions keeps it at
5,000 without giving up the Asian and London opens.

| Window (UTC) | Families | Why |
| --- | --- | --- |
| 05:00–08:00 | `donchian` | pre-London: structure is thin, only a breakout earns a test |
| 08:00–13:00 | `donchian`, `bollinger` | London range is real enough to fade |
| 13:00–17:00 | all three | NY overlap: full matrix |

`run_funnel.py --at-utc 08:00 --plan-only` prints the plan for any time without
touching the terminal.

---

## Live dashboard

![Zero-Trust Trader dashboard](docs/dashboard.png)

A Rust (Axum) server streaming to a React UI: equity, open positions, supervisor
actions, funnel history and MCP tool calls over a WebSocket. The screenshot is
the real thing running against the demo account.

```bash
cd dashboard && cargo run --release        # http://localhost:8787
```

**Design rule: the engine never knows the dashboard exists.** No IPC, no callback,
no socket the Python side opens. The dashboard tails ordinary files the engine
already writes (`engine_state.json`, `funnel_history.log`,
`position_manager.log`, `audit.jsonl`). Kill the dashboard and trading is
unaffected; kill the engine and the UI shows an amber staleness age rather than
inventing numbers.

| Endpoint | Purpose |
| --- | --- |
| `GET /` | the UI — one embedded HTML file, React vendored, no build step |
| `GET /api/snapshot` | one-shot state, last 12 lines of each log |
| `GET /api/logs/{audit,funnel,actions}` | **whole log**, for the Copy buttons and for pasting into an issue |
| `GET /ws` | pushed updates, sent only when something actually changes |

### Debugging from the UI

Every log pane has a **copy** button that pulls the entire file from
`/api/logs/…`, not the 12-line tail the snapshot carries — so a failure can be
pasted somewhere without shelling in. The header has **copy all logs**, which
concatenates audit + funnel + supervisor with a timestamp.

Panes are **newest first**. Oldest-first made a failure that was fixed hours ago
look current for as long as the 12-line window stayed full.

`audit.jsonl` is the event trail, not a test ledger: one JSON object per line
for verdicts, refusals and supervisor actions, written by `audit.write(...)`.
The trials ledger keeps counting trials; this records everything else. It is
appended to, never rewritten, and a failed write is swallowed on purpose —
auditing must never be able to break the thing it audits.

---

## Trials accounting, which is the whole point

Every scan appends its own hypothesis count to a durable ledger, and the
significance bar is derived from the accumulated total — not from the current
scan.

| Cumulative tests | z_crit |
| --- | --- |
| 1 (a single hypothesis) | 1.96 — no correction at all |
| 132 (one M15 subset scan) | 3.55 |
| 525 (the aborted wide run) | 3.90 |
| 1,791 (one full-universe run) | 4.19 |
| 5,388 (today) | **4.43** |
| 40,388 (a week at 5,000/day) | 4.85 |
| 155,388 (a month at 5,000/day) | 5.11 |

Every value is computed by `bonferroni_z()`, not estimated — `python status.py`
prints the current one. The window is 05:00–17:00 UTC (48 scans/day), but the
session matrix below narrows the families early, so a day adds 5,000 hypotheses
rather than the 7,200 a naive 12-hour expansion would.

```bash
$ python trials_total.py
5388
```

That number is what feeds `honest_backtest check trades.csv --trials 5388`.
Under-counting trials is the classic way a backtest lies; this repository refuses
to count only the runs that looked good.

---

## It can't lose money while you're asleep, either

Operational guarantees, each enforced in code rather than in documentation:

| Guarantee | Enforced by |
| --- | --- |
| Demo only | pre-flight aborts on any server without `demo` — verified in tests |
| No overlapping runs | `funnel.lock` with stale takeover; double-fire exits in 0.3 s |
| No stale signals | Tier 2 skips any instrument whose last bar is > 45 min old |
| No silent failure | a crashed funnel logs `FAILED`; the report prints `[STALE]` rather than a quiet market |
| No terminal pollution | every symbol the scanner selects into Market Watch is released afterwards |
| Nothing placed unattended | no scheduled job calls `place_order` at all |

The failure mode that shaped this design: **a system that reports success while
quietly doing nothing is worse than one that crashes.** Every guard above exists
because something silently lied at least once during development.

---

## Quick start

Needs Python 3.11+, the MetaTrader 5 terminal (Windows) logged into a **demo**
account, and Rust if you want the dashboard.

```bash
git clone https://github.com/youcisla/Zero-Trust-Trader.git
cd Zero-Trust-Trader
pip install -r requirements.txt          # MetaTrader5, numpy, pandas

python status.py                         # does the terminal answer? is it demo?
python run_funnel.py --rebuild           # Tier 1 → liquid universe (~35 min)
python run_funnel.py --window-utc 05:00-17:00   # one intraday scan
python report_funnel.py --full           # today's verdict
```

Supervisor (dry run by default — it prints what it would do):

```bash
python position_manager.py                    # watches, changes nothing
python position_manager.py --apply --session-close 20:45   # actually acts
```

Dashboard:

```bash
cd dashboard && cargo run --release            # http://localhost:8787
```

Windows Task Scheduler drives the cadence; `run_funnel.cmd` and
`run_rebuild.cmd` are provided. Nothing scheduled can place an order.

The consent-gate policy lives in `<execution-dir>\config.toml` (not committed —
see `docs/BOTLAB_COMMAND.txt` for the ledger/backtest workflow).

## Tests

76 tests, **no trading terminal required** — `MetaTrader5` is Windows-only, so
`tests/_mt5stub.py` stubs it and the indicator maths and safety guards run
anywhere, including Linux CI:

```bash
python -m unittest discover -s tests -v
```

CI also runs `ruff --select F821` over every module. `compileall` only proves a
file parses; an undefined name parses fine and crashes at runtime, which is how
a `NameError` once reached production twelve runs in a row while CI stayed green.

## Prove the order path once, by hand

`manual_e2e_test.py` is the only thing here that sends a real order, and it is
manual by design — it is not part of the cadence and never runs in CI.

```bash
python manual_e2e_test.py
```

It refuses unless the account is **demo** and AutoTrading is **on**, shows the
exact 0.01-lot order, and sends nothing until you type `EXECUTE`. It then closes
the position immediately, verifies the account is flat, and asks you to switch
AutoTrading back off — verifying that too.

Two things it cannot do for you: AutoTrading is a terminal **GUI toggle** (the
API can read `trade_allowed`, not flip it), and it exercises the bridge's
*policy* code in-process rather than over the MCP transport. Start the bridge
separately if you also want the transport covered.

## Two switches only a human holds

**Armed execution.** `--apply` on the supervisor is a request, not a permission.
It is honoured only when `ZTT_ALLOW_APPLY=1` is set in the shell that launched
it; otherwise the process stays in dry run and says why. A command string an
agent can build is not consent.

**Ledger integrity.** Every trial row is signed into an HMAC chain keyed from
`ZTT_LEDGER_KEY` (env, or `.env` in the execution dir — never committed).
`status.py` and `trials_total.py` verify the chain and print `!! LEDGER TAMPERED`
if a row was edited, deleted or reordered. Rows written before the chain existed
are reported as `unsigned` rather than silently trusted.

| Suite | Covers |
| --- | --- |
| `test_math` | RSI / MACD / ATR / Donchian / Bollinger edge cases, Bonferroni values, naive-vs-corrected |
| `test_safety` | demo-only pre-flight, the run window, lock acquire / refuse / stale takeover |
| `test_ledger` | trials accounting, including that `LEGACY_TRIALS` is frozen |
| `test_position_manager` | protective-direction rule as a 3,000-case property sweep, break-even, session close |

The suite exists because two bugs in this codebase were **silent**: RSI returned
100 on a flat series (a dead instrument reading as overbought, firing a false
short) and MACD's signal line was all-NaN (a strategy family that looked
implemented and could never fire). Neither raised an error; both now have a
regression test.

CI also fails the build if a machine path, broker account number, or key prefix
appears in the tree — the sanitiser is enforced rather than remembered.

---

## What this does not do

- It does not claim an edge. It has found none.
- It does not predict prices, and no part of it is financial advice.
- It does not touch a live account — the pre-flight refuses one.
- It is not a strategy. It is the machinery that decides whether a strategy
  deserves to exist.

---

## License

MIT. See `LICENSE`.

Built as a demonstration that **"the AI declined to trade" can be a measurable
result rather than a slogan.**
