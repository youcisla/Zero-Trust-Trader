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

Every order needs two independent approvals:

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

It was **refused**. Against the corrected bar for 568 tests (z = 3.92), +2.40 is
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

## Six gates before anything reaches the order book

| # | Gate | Failure it catches |
| --- | --- | --- |
| 1 | **Universe filter** — live market, spread ≤ 15% of ATR, ≥ 160 days of history | trading an instrument that is shut, illiquid, or newly listed |
| 2 | **Correlation dedupe** — collapse candidates above \|ρ\| 0.7 | `AMD.US-24` vs `AMD.US-PERP` measure **+0.997**: the same bet billed twice |
| 3 | **Bonferroni-corrected z** | the scan itself — 5,388 hypotheses tried means the bar is 5, not 1.96 |
| 4 | **Permutation null** — random entries, same barriers | autocorrelation and barrier geometry that the analytic z assumes away |
| 5 | **Human `APPROVED`** | the agent's own judgement |
| 6 | **Bridge consent gate** | a compromised or confused agent |

Gates 3 and 4 are not redundant. The permutation test is per-instrument and does
**not** correct for the scan: at 568 tests, a p-value of 0.015 is expected ~8
times per run. Passing one gate is not permission to trade.

---

## Architecture

```
Tier 1  universe filter      ~1,700 symbols → ~600 tradable, liquid, live
Tier 2  strategy matrix      3 families, correlation-deduped → 5 distinct bets
Tier 3  deep test            10k Monte-Carlo paths + 15k bars + both gates
        ↓
        human APPROVED  →  bridge consent  →  order
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

---

## Trials accounting, which is the whole point

Every scan appends its own hypothesis count to a durable ledger, and the
significance bar is derived from the accumulated total — not from the current
scan.

| Cumulative tests | z_crit |
| --- | --- |
| 150 (one scan) | 3.59 |
| 1,791 | 4.19 |
| 5,388 (today) | **5.30** |
| 77,256 (a month of intraday) | 4.98 → rising |

Look at the second and third rows together. The bar rises because the search
space rises — and the system says so out loud instead of quietly re-using a
friendlier number.

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

## Running it

```bash
pip install -r requirements.txt          # MetaTrader5, numpy, pandas
# MetaTrader 5 terminal must be running and logged into a DEMO account

python status.py                         # everything: state, positions, trials
python run_funnel.py --rebuild           # Tier 1 → liquid universe (~35 min)
python run_funnel.py --window-utc 13:00-17:00   # intraday scan
python report_funnel.py --full           # today's verdict
```

Windows Task Scheduler drives the cadence; `run_funnel.cmd` and
`run_rebuild.cmd` are provided.

The consent-gate policy lives in `%APPDATA%\mt5-mcp\config.toml` (not committed —
see `docs/BOTLAB_COMMAND.txt` for the ledger/backtest workflow).

## Tests

35 tests, **no trading terminal required** — `MetaTrader5` is Windows-only, so
`tests/_mt5stub.py` stubs it and the indicator maths and safety guards run
anywhere, including Linux CI:

```bash
python -m unittest discover -s tests -v
```

| Suite | Covers |
| --- | --- |
| `test_math` | RSI / MACD / ATR / Donchian / Bollinger edge cases, Bonferroni values, naive-vs-corrected |
| `test_safety` | demo-only pre-flight, the run window, lock acquire / refuse / stale takeover |
| `test_ledger` | trials accounting, including that `LEGACY_TRIALS` is frozen |

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
