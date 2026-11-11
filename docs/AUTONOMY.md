# Autonomy: what is automated today, what is not, and what blocks it

This file exists because "automate everything" is the most reasonable request
this project receives and the one that could destroy it. It states, with
numbers, what runs on its own, what is deliberately gated, and the single
measurement that decides whether the gated part should ever be opened.

Nothing in this document is a plan that has been half-built. Each section says
what exists in the tree today.

---

## 1. What already runs without a human

| Thing | Cadence | Where |
| --- | --- | --- |
| Universe scan (tier 1 → tier 2 → tier 3 → gate) | every 15 min, 05:00–17:00 UTC | `HermesMT5Funnel` → `run_funnel.py` |
| Universe rebuild (tier 1 + liquidity) | daily | `HermesMT5Rebuild` |
| Four-agent desk brief | every 15 min in-window | `HermesMT5Desk` → `agent_brief.py` |
| Stop management (trail, break-even, session close) | **not scheduled** — manual, dry-run until a real position exists (AGENTS.md §12) | `position_manager.py` |

The manual step left is the one that can lose money: typing `APPROVED` for an
entry.

## 2. What is deliberately not automated

Order **entry**. `AGENTS.md` §1 is the product, not a preference:

- `AutoTrading` is off and no code path enables it.
- An order needs a human `APPROVED` **and** a single-use token from the bridge's
  consent gate.
- The pre-flight refuses any terminal whose server name does not contain `demo`.

Order **exit** is automated but can only reduce risk: `position_manager.py`
trails a stop, moves to break-even, closes at session end. It cannot open,
enlarge or reverse. `--apply` is required to send anything; the default is a dry
run, and no scheduled task passes `--apply`.

## 3. The measurement that blocks full automation

**29,999 cumulative trials. The bar stands at z = 4.79. Nothing has ever
cleared it.** (Measured 11 Nov 2026; the figure moves with every scan — read it
from `tier3_results.json`, not from here.) The best candidates found today, with shares in the universe:

| candidate | n | win | avg R | t | p | gate |
| --- | --- | --- | --- | --- | --- | --- |
| SAN.ES / donchian | 248 | 47.2 % | **+0.308R** | +2.68 | 0.182 | fail |
| CBKd.DE / donchian | 240 | 45.0 % | **+0.222R** | +1.79 | 0.177 | fail |
| ATCOA.SE / donchian | 210 | 49.0 % | **+0.185R** | +1.50 | 0.420 | fail |
| WMT.US / bollinger | 296 | 2.4 % | −0.861R | −13.89 | 0.006 | fail |

So automatic entry would fire **zero times**. Wiring it today would automate
standing down — a real feature, but not the one being asked for.

Two candidate readings of that fact, and they point in opposite directions:

1. *The bar is too strict; lower it and the system trades.* The bar is 4.56
   because 10,128 hypotheses had been tested on 2026-11-09. Lowering it does not create an
   edge, it relabels noise as one: `tools/exit_policy_ab.py` and the rr sweep in
   `docs/SIZE-AND-RUIN.md` show every configuration landing 1–10 points below its
   own breakeven. A 2.68 t-statistic after 10,128 tries is what luck looks like
   (figures as of 2026-11-09; the live bar is the one in `tier3_results.json`).
2. *The families are wrong, not the bar.* True, and testable: shares produced the
   first positive-expectancy candidates this project has seen. The work is to
   find a family that survives 4.56 — not to lower it.

## 4. If autonomy is still wanted: the only safe shape

There is a version of "fully automated" that does not require lying about the
numbers, and it is bounded by construction:

```toml
# config.toml — gitignored, read by the bridge and the funnel
[execution]
mode = "demo_auto"          # "manual" (default) | "demo_auto"
max_trades_per_day = 1
max_lots = 0.01
kill_file = "KILL"
```

Rules a `demo_auto` mode must satisfy, all of them checkable:

1. **Demo only.** Refused unless `"demo" in account.server.lower()` *and*
   `AutoTrading` is enabled *by the human* in the terminal — the code still never
   enables it. A live server string is a hard stop, not a warning.
2. **One trade a day, 0.01 lots.** The risk unit is already 1.14 % of equity; more
   than one shot a day turns a measurement into a habit.
3. **The `KILL` file wins.** If it exists at the moment of sending, no order is
   sent. Checked immediately before `order_send`, not at start-up.
4. **Every automatic order writes an audit row** with the candidate, the gate
   values, the size, the ticket, and the reason — the same row shape a manual
   order writes today.
5. **The desk still cannot trade.** The jury may `propose`; it may not trigger.
   Only a candidate that cleared both gates reaches `autoexec`.
6. **A clean stand-down is the expected outcome.** If `demo_auto` is on and
   nothing passes for 30 days, that is the system working, and the audit trail
   must make it obvious rather than looking broken.

What that buys: a complete, unattended loop on fake money, with the honest
ledger showing what it did. What it costs: ~150 lines in a new `autoexec.py`
plus the gate wiring — and it still fires zero times until a family clears 4.56.

## 5. What the research says is worth taking

Checked against public implementations (TradingAgents, arXiv 2412.20138) and the
walk-forward literature; only the items that change a number here are listed.

| Idea | Why it matters here | Status |
| --- | --- | --- |
| **Point-in-time integrity** — never let a backtest see a value published after the simulated date | the digest and the deep test already read only closed bars; the desk reads artifacts, not a live feed | done |
| **Score every decision against the first close after it** | turns the ledger into a scorecard instead of a list; the desk already stamps every verdict in `desk_history.sqlite` | half — needs a nightly join with prices |
| **Deflated Sharpe / multiple-testing correction** | the cumulative Bonferroni bar is already this, in a cruder form; DSR would price the *variance of trial Sharpe*, not just the count | open, needs tier 2 to persist a per-trial Sharpe |
| **Bull vs bear researcher debate** | the desk has only a bear (`skeptic`); a bull would make the rebuttal visible | open, costs one model call per run |
| **Provider per role** | the desk already records drift per role; different models per role is a config change | open, low value |
| **Kill switch before send, not at start-up** | the only pattern in that literature that prevents a bad afternoon | required by §4 |

## 6. The line

> An agent may research freely and may change the system. It may not trade.

Automation that respects that line is worth building. Automation that crosses it
does not create an edge; it removes the last person who would have noticed there
wasn't one.
