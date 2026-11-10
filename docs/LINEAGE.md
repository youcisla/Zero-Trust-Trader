# Lineage: three stages, one question

This repository is the third stage of a single research arc that began in March
2026. The stages are kept as separate, untouched repositories so the design
history stays auditable. Nothing was rewritten retroactively; each stage carries
a dated pointer forward and a record of what it measured.

| | Repository | Dates | Size | Outcome |
|---|---|---|---|---|
| 1 | [TradeBot](https://github.com/youcisla/TradeBot) | Mar–Sep 2026 | 423 files, ~50k lines | **No edge.** |
| 2 | [TradeBotV2](https://github.com/youcisla/TradeBotV2) | 13 Sep 2026 | 20 modules, 69 tests | Measurement first. |
| 3 | this repo | Nov 2026 | gates, ledger, consent | **Refuses to trade.** |

## Stage 1 — the sophisticated system

A multi-agent MT5 system: nine-role pipeline, LSTM, LightGBM ensemble, GARCH and
HMM regime detection, an LLM council and a debate layer, FastAPI + Next.js +
Telegram. Roughly 50,000 lines.

Two findings killed it, in this order:

1. **The live path was never reachable.** `MT5Connector` inherited an abstract
   base class without implementing its abstract methods, so constructing it
   raised `TypeError`. The test suite passed throughout because every test used
   a mock.
2. **Once it could connect, the strategy had no edge.** 3,271 positions across
   six majors: 37.9% win rate, −0.431R average, profit factor 0.46, 100% max
   drawdown — negative in every symbol, every fold and the frozen holdout, and
   still negative under maximally optimistic intrabar fills. Inverting every
   signal lost *worse*, so it was not a sign error.

The lesson was not "the strategy was bad". It was that **complexity had been
built on an unmeasured foundation**.

## Stage 2 — measurement before automation

A rebuild whose first artefact was the measuring instrument, not the strategy:
as-of slicing with no look-ahead, real spread and commission on every fill,
per-position R, a time-frozen holdout. Agents were reintroduced with a
structural fence rather than a prompt — a capability enum with **no order
capability at all**, so "the analyst cannot trade" is a property of the types.

## Stage 3 — this repository

The same premise, taken to its conclusion. If a strategy must earn trust through
measurement, then the honest output of a scan that finds nothing is *no trade*:

- Two independent gates: a Bonferroni-corrected z over the **cumulative** trial
  count, and a permutation test whose null trades the identical rule.
- A tamper-evident ledger: every trial row HMAC-chained; editing one breaks
  verification.
- Fail-closed consent: execution requires an environment variable a human sets,
  not a flag an agent can type.
- The success state is `STAND_DOWN`.

Figures as of 2026-11-05: 6,320 cumulative trials → a 4.47σ bar; every scheduled
scan so far stands down. The live count is `python trials_total.py` — a static
number in prose is true only on the day it was written.
