# Trials accounting — multiple-comparisons log

Tracks how many independent hypotheses this project has tested, so
`botlab-oss honest_backtest --trials N` deflates the Sharpe Ratio honestly
instead of flattering us.

## Log

| Round | Tests | What was tested | Outcome |
| --- | --- | --- | --- |
| EURUSD pullback (Phase 2) | 1 | pre-registered EMA20/50 pullback, H1, 1.5xATR, 1.5R | z +1.0 — noise |
| EURUSD pullback grid | 18 | 2 sides x 3 atr_k x 3 rr | best z +1.0 — noise |
| XAUUSD Donchian-20 | 1 | pre-registered breakout, H1, 1.5xATR, 1.5R | z +1.36 — noise |
| Tiered funnel (Phase 3) | 568 | full Pepperstone universe, Donchian-20 + EMA50/200 | 5 deep-tested, **0 passed** |
| Funnel run 1 (Phase 4, automated) | 568 | same, plus permutation gate + correlation filter | 0 passed both gates |
| Funnel run 2 (automated, scheduled path) | 585 | identical pipeline, fresh universe snapshot | 0 passed both gates |
| Funnel run 3 (subset smoke) | 525 | 175 x 3 families — strategy-matrix validation | 0 passed both gates |
| Funnel run 4 (strategy matrix, full universe) | 1,791 | 597 x 3 families, permutation gate, corr dedupe | 0 passed both gates |
| Funnel run 5 (subset, revised families) | 525 | 175 x 3: donchian / rsi 35-65 / bollinger | 0 passed both gates |
| Funnel run 6 (logging verification) | 525 | same subset, after Tier 3 took over logging | 0 passed both gates |

**Cumulative: 5,107.**

Counted even though the last run was started by hand: the rule is that a run is
a run. The alternative — discounting runs that did not produce a candidate —
is exactly how a search space gets under-declared.

The 525 subset run is counted deliberately: it was a real scan of 175
instruments across 3 families and it consumed real statistical budget, even
though its purpose was to prove the code ran. Smoke tests that look at market
data are trials.


### Every run costs symbols x strategies

Each run scans every surviving symbol with **three** strategy families, so one
pass is three hypotheses per instrument:

    trials = 587 + sum over past runs of (symbols_tested x strategies_tested)

At ~585 symbols x 3 families that is ~1,755 tests per run, and ten days is
~17,550. The correction is therefore much harsher than the single-strategy
funnel, which is the price of testing three families instead of one.

Run counts drift between runs because instruments enter and leave the liquidity
filter as markets open and close — always use each run's own
`total_tests=` figure from `funnel_history.log`, never a constant.
`tier3_deep.py` reads `total_tests` from the Tier 2 artifact, so the per-run
declaration is automatic; only the historical accumulation is manual at the
botlab call.

### The strategy matrix

| Family | Trigger | Designed to work when |
| --- | --- | --- |
| `donchian` | close beyond the prior 20-bar channel, EMA50>EMA200 filter | the market trends |
| `rsi` | RSI-14 crosses INTO <35 (long) or >65 (short) | the market chops / mean-reverts |
| `bollinger` | close crosses OUT of the 20-bar, 2.0sd band — faded, not followed | the market chops / overshoots |

MACD was trialled as a third family and **removed**: a line/signal cross is
trend-following, so it duplicated Donchian's exposure instead of diversifying
it. Two trend families would have raised the multiple-comparisons bar without
adding a genuinely different bet.

`score` in the Tier 2 artifact is each trigger's strength divided by its own
family's best of the day. It is a **selector heuristic, not a ranking claim** —
an RSI trigger scoring 1.0 is not being asserted as better than a Donchian
trigger scoring 1.0. Tier 3, not Tier 2, decides what is real. One breakout
family alone fails by construction in a sideways market, which is why three
families exist.

Correlation dedupe runs between Tier 2 and Tier 3 so the five Monte-Carlo runs
buy five distinct bets; the survivor list is deduped again as a final guard.

### Trade sparsity — thresholds were not the problem, history length was

The first matrix run shortlisted three `rsi` candidates whose samples were
134-148 trades, far under G1's 300-trade floor. The obvious fixes do not work:

| Attempt | Measured effect |
| --- | --- |
| RSI 30/70 → 35/65 on H1 | 134 → 204 trades (still short) |
| RSI 30/70 → 40/60 on H1 | 134 → 273 (still short, and 40/60 is a weak signal) |
| Move to M15 | **no help** — 133 vs 134, because the scanner advances to the exit bar, so entries are horizon-limited, not bar-limited |
| **Extend history 5k → 15k M15 bars** | **134 → 616** |

The binding constraint was how many bars the scanner could see, not how loose
the threshold was. Tier 3 therefore fetches **15,000 M15 bars** while Tier 2 keeps
4,000 — Tier 2 only needs the last bar's indicators, so paying for 15k bars
across 600 symbols every run would cost ~4x the runtime for nothing.

Consequence worth stating plainly: an instrument listed within the last ~year
cannot reach 300 RSI trades at any threshold, so it fails G1 by construction.
Several shares in the universe are in that bucket. That is the gate working,
but it does bias the funnel toward instruments with long histories.

**Conclusion: this was a history-length constraint, not a threshold constraint.**
Loosening 30/70 to 35/65 bought +70 trades; extending 5,000 to 15,000 bars
bought +480. Thresholds only trade signal quality for volume — history *is* the
sample. **15,000 M15 bars is now the standard deep-test window.** Tier 2 keeps
4,000 on purpose, because it reads only the last closed bar.

The decisive evidence that the sparsity had been masking nothing: ETHAUD/rsi
went from n=134 (z −0.18) to n=566 and its z *fell* to −0.50. A larger, less
noisy estimate moved toward zero — exactly what happens when the original
reading was luck. More trades did not reveal a hidden edge; they confirmed its
absence.

## Accounting integrity

`funnel_history.log` is written by **Tier 3**, not the orchestrator. It used to
be written only by `run_funnel.py`, so a hand-started deep test left no trace —
and the log-derived total disagreed with this file by exactly the 1,618 trials
from three manual runs (568 + 525 + 525). Tier 3 now owns the line, so every run
is recorded exactly once whether scheduled or manual. Runs before that
cut-over (2026-11-05T16:37Z) are frozen into `LEGACY_TRIALS = 2,205` in
`trials_total.py`.

`python trials_total.py` is the authoritative number for `--trials`. If it ever
disagrees with this file, this file is wrong.


## The funnel result — why 568 is the right number

Tier 1 kept 568 symbols, Tier 2 kept the 5 strongest current breakouts, Tier 3
ran 10,000 Monte-Carlo paths on each. Corrected bar for 568 tests: **z > 3.92**.

| Symbol | Trades | Win | Break-even | z | Avg R | Max DD |
| --- | --- | --- | --- | --- | --- | --- |
| SON.PT | 266 | 34.2% | 40.0% | −1.88 | −0.139 | −40.47R |
| QCOM.US-24 | 301 | 31.0% | 40.0% | −3.11 | −0.185 | −65.09R |
| AMD.US-24 | 344 | 41.5% | 40.0% | +0.56 | +0.046 | −11.73R |
| SHBA.SE | 304 | 39.7% | 40.0% | −0.10 | +0.013 | −15.98R |
| HNR1d.DE | 304 | 46.9% | 40.0% | **+2.40** | +0.192 | −12.98R |

**HNR1d.DE is the exhibit.** z = +2.40 clears a naive 1.96 comfortably and would
have been reported as a finding by any single-symbol test. Against the corrected
3.92 it is ordinary — exactly what taking the best of 568 draws produces. This
one row is the whole justification for the correction; without it we would have
traded a coin flip with a −13R tail.

Note also that QCOM's z = −3.11 is *not* a short signal. Acting on the most
negative of 568 results is the same data snooping as acting on the most
positive.

## How to count

`--trials` is the number of independent-ish comparisons that produced the
winner, not the number of trades and not the number of files.

- Single pre-registered rule on one instrument: `--trials 1`.
- N symbols scanned, best kept: `--trials N`.
- Parameter grid also swept: multiply, N symbols x M variants.

Under-counting is the classic way a backtest lies. When unsure, round **up**.
`tier3_deep.py` defaults `--trials` to the authoritative cumulative count
(`legacy + logged + this scan's tests`) and **refuses** an explicit value below
it, so the funnel cannot silently under-declare.

## The two gates (both required)

| Gate | Question it answers | Weakness it covers |
| --- | --- | --- |
| Bonferroni z | Is this better than the best of N random searches? | The scan itself |
| Permutation | Is this better than *random entry timing* on this instrument? | Autocorrelation, barrier geometry, cross-candidate correlation |

A candidate ships only if **both** pass. They are not substitutes: the
permutation test is per-symbol and does **not** correct for the scan. On the
2026-11-05 run, HNR1d.DE reached p = 0.0149 (beats random entry) yet still failed
the corrected z — and that is correct, because 568 x 0.015 is about 8 false
positives per scan. Passing the permutation test alone is not a licence to trade.

Correlation filter: survivors above |rho| 0.7 are collapsed to the higher-z leg,
so the book never doubles risk on one underlying. Measured on this broker:
EURUSD/GBPUSD +0.83, XAUUSD/XAGUSD +0.85 (would be merged), AMD/QCOM only +0.51
(genuinely two trades).

## Standing rule

A result that only clears a naive z = 1.96 after a wide scan is **not** a
finding. It must clear `bonferroni_z(n)` — 2.58 at n=5, 3.92 at n=568. When the
Bonferroni bar and DSR's staging level of 0.5 disagree, the harsher one wins and
the trade is not taken.

## Frequency is not free (the M15 intraday move)

Scanning more often does not buy more edge; it buys a higher bar. Measured with
`bonferroni_z`:

| Scenario | Tests | z_crit |
| --- | --- | --- |
| One M15 scan, top-50 x 3 families | 150 | **3.59** |
| One day at 16 scans (13:00-17:00Z overlap) | 2,400 | 4.26 |
| Cumulative after one day (5,256 + 2,400) | 7,656 | 4.51 |
| Cumulative after one week | 22,056 | 4.73 |
| Cumulative after one month | 77,256 | 4.98 |
| *rejected:* 96 scans a day, no window | 14,400 | 4.64 |

**Historical: the London/NY overlap only (13:00-17:00 UTC, 16 scans/day).** That
was the adopted cadence when this table was written; it has since been widened
to 05:00-17:00 UTC — see the session matrix below, which is the current rule.
Outside the window `run_funnel.py` exits 0 immediately, before it takes the lock
or touches MT5.

Restricting to the overlap was a 6x reduction in daily looks and it moved the
wall out by roughly a month: the 96-scan cadence reaches z 4.64 inside a single
day, while 16 scans/day takes about a month to reach 4.98. Scanning less is the
only honest lever once a cadence is chosen — the bar is a function of how often
you look, not of how hard you try.

Two things follow, and only the second is usually understood:

1. **The per-scan figure is misleading.** Restricting to 50 symbols cuts a scan
   from 5,133 tests to 150 (34x), which looks like it buys back a much easier
   bar — 3.59 instead of 4.55. True, but only if you pretend it is the only scan
   you will ever run.
2. **The sequence is what counts.** You trade the first candidate that passes
   across *all* scans, so family-wise error is set by the accumulated number of
   looks. The first scan of a day is judged against 3.59, but by the end of that
   day the honest bar is already 4.51 — and tomorrow's first scan does not get
   to reset to 3.59 either, because the cumulative ledger keeps climbing.

The accounting is implemented, not aspirational: every scan appends its own
`total_tests` to `funnel_history.log` and `trials_total.py` accumulates. A
candidate that clears 3.59 today will be refused next month at the same z.


---

## Session matrix (05:00-17:00 UTC) and the cumulative correction

The scan window widened from 16 scans/day (13:00-17:00) to 48 (05:00-17:00).
The strategy matrix narrows in the early sessions so the daily hypothesis count
does not scale with the window. The count is `tested x families`, where
`tested` is the subset of the 180-symbol universe with fresh bars at that
moment; it is not a fixed 50 symbols.

| Window (UTC) | Scans | Families | Tests/scan (measured 2026-11-12) |
| --- | --- | --- | --- |
| 05:00-08:00 | 12 | donchian | tested (1 family) |
| 08:00-13:00 | 20 | donchian, bollinger | 196-356 |
| 13:00-17:00 | 16 | donchian, rsi, bollinger | 330-402 |

The 35 scans recorded on 2026-11-12 from 08:31 to 16:45 added 10,358
hypotheses. That partial day already exceeds the 5,000 this table used to claim
when the universe was 50 symbols. Fewer families early is not a saving, it is a
smaller bet; read the actual per-scan count from the ledger's `total_tests` or
`tier3_results.json`'s `trials_declared`.

### z_crit is cumulative, never per-scan

The gate uses the running total of every hypothesis ever tested, including the
scan being judged.

| Point | Cumulative trials | z_crit |
| --- | --- | --- |
| now (2026-11-05) | 9,081 | 4.54 |
| +1 day | 16,281 | 4.67 |
| +1 week | 59,481 | 4.93 |
| +30 days | 225,081 | 5.18 |

A per-scan bar would have been 3.29 (50 tests), 3.48 (100), 3.59 (150). At 30
days that is **1.52 sigma of unearned leniency** - the exact data-snooping error
this file exists to prevent.

`run_funnel.py` therefore passes `--trials <cumulative>` to `tier3_deep.py`,
computed as `legacy + logged + this scan's tests`. Before this was wired,
tier3 fell back to the per-scan count, so every run after the first was judged
as if it were the only run ever made.
