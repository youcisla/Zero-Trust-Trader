# Statistical and market-data audit

Audit date: 2026-11-09. This is an evidence and decision record, not a claim of a measured market edge. No terminal was connected, no historical market experiment was run, and no production ledger or trading configuration was modified. Line numbers refer to the source reviewed on this date.

## Method and limits

Read the strategy scanners, Tier 1/2/3, cumulative-trial implementation, universe builder, relevant orchestration and supervisor interfaces, and mathematical/parity regression tests. Followed the values from candidate selection through historical returns, significance, permutation grading, and the ledger. Executed isolated synthetic examples after installing `tests/_mt5stub.py`; the null example explicitly mocks exit outcomes. Findings below distinguish observed behavior from methodological recommendations. P1 means a material correctness or fail-closed defect; P2 means a substantive reliability or validation improvement. No significance threshold reduction, change to frozen historical trials, permutation-count change, or strategy expansion is recommended.

## Confirmed findings

### S1 — P1: the default gates can approve negative mean returns

Evidence: `timemachine_mt5.py:392-417`, `:537-539`, `:590`; `run_funnel.py:200-203`, `:328-329`; `tests/test_t_gate.py` already records the negative-mean failure of the default significance statistic. The historical policy has variable trailing exits, but the default significance gate still compares the fraction of positive outcomes with `1/(1+rr)`. In default permutation mode, `actual > 0` means positive win-rate z, not positive mean R.

Synthetic observation: 138 returns of +0.01R and 112 returns of -1R have mean **-0.44248R**. The default significance result is z **4.906**, corrected bar **4.237** for 2,205 trials, `passes_corrected=True`, and mean-R t **-13.902**. With the null resolver explicitly mocked to lose every trade, exactly 1,000 permutations produce p **0.001** and `passes=True`. This demonstrates the logical possibility of both gates passing a losing series; it does not establish that a production market candidate has done so.

Recommendation: make nonpositive finite mean net R an unconditional refusal for every gate mode. Treat the choice of an economically meaningful primary statistic as an explicit reviewed policy decision; preserve or strengthen all numerical gates. Add an end-to-end regression that a negative-mean sample cannot become a survivor through either CLI mode. The existing tests only require this protection under `use_t=True`.

### S2 — P1: cumulative accounting has permissive failure and manual paths

Evidence: `run_funnel.py:312-322` catches ledger/read errors and substitutes zero. `tier3_deep.py:153-158` accepts an explicit trial count without checking a historical lower bound; when the argument is omitted, it uses `max(scan_tests, legacy + logged)` and omits the new scan's tests.

Trigger: unreadable/malformed ledger or a hand-run Tier 3. Impact: the correction can become weaker than `legacy + logged + this scan`, contrary to the central project invariant. A broken ledger must not be interpreted as no previous research.

Recommendation: derive and validate a single authoritative cumulative count; refuse incomplete reads and arguments below that count. Ensure newly completed Tier 2 tests are accounted for even if the deep pass later fails. Test ledger read failure, invalid counts, the exact manual sum, and failures after Tier 2. Keep `LEGACY_TRIALS` unchanged.

### S3 — P1: horizon expiry reads one additional unchecked bar

Evidence: `timemachine_mt5.py:92-112`, `:139-164`. The loop checks through `i+horizon`, increments `j`, then marks the trade at the following bar.

Synthetic observation: enter at index 0/price 100 with horizon 2, no stop hit, and closes 100 at indices 1/2 but 200 at index 3. `_resolve_live` returns `horizon`, exit index **3**, exit price **200**; `_resolve` also returns index **3**. The price movement and any barrier crossing on index 3 are never processed by the loop.

Recommendation: exit on the final permitted processed bar and define whether the entry/exit timestamps represent bar open or bar close. Add a test where the next bar jumps through a stop or target so the implementation cannot accidentally borrow its close.

### S4 — P1: session exits use a different clock from the declared UTC policy

Evidence: `marketdata.py:88-101` treats timestamps as broker/server time and returns the conversion offset separately. `timemachine_mt5.py:157-161` interprets the raw timestamp as UTC, ignoring `bars['offset']`.

Under the code's own timestamp convention, a +2-hour server offset makes the simulated 17:00 close occur at 15:00 UTC. The source also uses the candle's close price while comparing its timestamp, so the bar-open/bar-close convention needs explicit treatment.

Recommendation: establish one documented timestamp contract at the market-data boundary and use it consistently in entry sessions, session closes, reports, and fixtures. Verify nonzero offsets and daylight-saving transitions using synthetic data before relying on session parity. The terminal's actual timestamp convention was not measured in this audit.

### S5 — P1: unavailable spread can silently become free execution

Evidence: `timemachine_mt5.py:623-628` obtains a tick but computes historical cost from `info.spread * info.point`, substituting zero when metadata is absent/zero. `tier1_universe.py:8-10` documents that the broker can report zero metadata spread even after selection; `:137-140` correctly uses the live bid/ask difference and rejects missing cost.

Recommendation: obtain spread through one validated shared helper after selection; refuse absent, nonfinite, nonpositive, crossed, or stale quotes. Record the cost source. Add a fixture with `info.spread=0` and a valid positive tick spread and another with no usable tick. Historical time-varying spread, commission, slippage, and gaps remain unmodelled costs and should be disclosed in every evidence report.

### S6 — P2: short stop fills charge an extra spread and supervisor inputs differ

Evidence: `timemachine_mt5.py:149-152`. A short is stopped when ask reaches the stop (`bid_high + spread >= cur`) but receives a fill of `cur + spread`. A synthetic initial stop at **101**, spread **0.2**, and bid high **100.8** returns effective exit **101.2**. Once the stop is expressed as an ask execution level, the spread has already determined the trigger and must not be added again to that fill.

The resolver also supplies bid close and zero spread to `next_stop`, whereas the supervisor interface accepts side-dependent live price and spread. Calling the same helper alone does not prove equivalent inputs or fills.

Recommendation: define bid/ask conventions for entry, SL, trigger, trail, break-even, and fill together; repair the initial-stop error and test long/short cases through both simulated and supervisor calculations. This particular initial-stop error is pessimistic for shorts, but inconsistent inputs can distort ranking and null comparisons.

### S7 — P1: unknown correlations are accepted as independent

Evidence: `tier2_patterns.py:128-142`, `tier3_deep.py:92-102`, `make_liquid.py:100-105`; `marketdata.py:210-222`. A missing series or insufficient shared history returns `None`; candidates are retained because only a known correlation above the ceiling causes rejection. Nonfinite correlation likewise does not satisfy the comparison.

Recommendation: treat unknown/nonfinite correlation as unverifiable and refuse or quarantine the additional candidate. Preserve the known candidate and record the reason. Test missing series, too little overlap, flat returns, nonfinite values, and identical symbols. Do not convert unknown correlation into zero.

### S8 — P2: Monte Carlo output grades a different exit policy

Evidence: `timemachine_mt5.py:435-483`, `:687-696`. `simulate()` uses a fixed target/stop with synthetic candle resampling; it lacks the trailing, break-even, spread, and session-close rules used by the historical resolver and permutation null. Its `avg_R`, target probability, and loss distribution therefore describe another policy. It is advisory rather than one of the two acceptance gates, which limits the direct safety impact.

Recommendation: either use the shared exit policy with explicitly appropriate synthetic data or label the output as a separate fixed-barrier scenario and keep it out of live-policy return/ruin claims. Test the report labels and outcome semantics.

## Methodological improvements requiring separate validation

1. **Match historical entry eligibility to the proposed system.** Scanner loops evaluate all historical hours; a hard closing time alone does not restrict entries to 05:00-17:00 or the narrower per-family session windows. The random-entry null samples all eligible array indices, including times the live scan never proposes. Carry the declared UTC family session into both observation and null; verify the same entry-eligibility predicate in both. Any newly measured hypothesis must be counted normally.
2. **Calibrate the null under its actual sampling design.** Real scanners skip ahead after exit, but `permutation_test` samples entry indices independently with replacement (`:549-550`), allowing duplicate and overlapping positions. The observation and null also use rounded versus unrounded R. Exchangeability and the false-positive rate are not established by the current parity tests. Use synthetic no-edge simulations and matched chronological/no-overlap eligibility to validate calibration; retain exactly 1,000 permutations.
3. **Validate the analytic statistic.** Mean-R t is compared with a normal critical value, regardless of sample size, tails, or serial dependence. The normal approximation and effective sample size need documented evidence before it is presented as a calibrated probability guarantee. Consider stricter sample requirements, an appropriate corrected t reference, and dependence-aware validation; do not lower the bar to obtain a survivor. Bonferroni's union bound itself does not require independent tests, contrary to the wording at `tier3_deep.py:5-9` and `timemachine_mt5.py:67`.
4. **Separate discovery and evaluation.** There is no visible held-out or walk-forward evaluation contract in this path. Cumulative correction is useful but does not by itself measure robustness to regime change, execution delay, alternative cost assumptions, or repeated human strategy revision. Predeclare a validation protocol and preserve negative outcomes and trial counts.
5. **Validate market-data structure once.** `marketdata.fetch` currently accepts numeric arrays without enforcing finite positive OHLC, OHLC ordering, monotonic unique timestamps, or expected gaps. Add boundary validation and degenerate fixtures; callers should receive an explicit unusable-data reason rather than propagate NaN/infinity.
6. **Preserve reproducibility.** Future reports should capture source revision, source/artifact IDs, timestamp convention, symbol contract metadata, requested/received bars, cost assumptions, seed, exact gate mode, session, trial count, and data digest. Store no broker identifiers in tracked files. This audit did not copy production datasets.

## Existing strengths and documentation corrections

The fixed 1,000-permutation CLI choices, add-one p-value, common indicator helpers, exclusion of forming bars, non-overlap rule in the observation scanners, cumulative-trial intent, flat/short-series indicator tests, explicit outcome buckets, and dry-run supervisor are useful foundations. Existing tests catch several prior silent errors, but some now encode a known unsafe default rather than requiring its rejection.

Update stale descriptions: Tier 2's `trigger` field still names MACD (`tier2_patterns.py:212`); Tier 3 describes per-scan correction and independent gates despite cumulative usage and shared observations; `analyse` proposes a fixed target that the live path does not execute; `--bars` help refers to H1 although the default deep-test timeframe is M15. Document tick volume as activity rather than traded notional and benchmark priority as an explicit selection rule.

Skipped: no terminal verification, current spread measurement, provider data-quality assessment, historical re-run, profitability estimate, or financial recommendation. Risk: the present numbers can be internally reproducible while still describing different costs, clocks, sessions, or exit rules from the intended system.
