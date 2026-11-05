# Contributing

This repository exists to demonstrate one idea: **an AI trading system should be
able to prove that it declined to trade.** Contributions are welcome if they
strengthen that, and declined if they weaken it.

## The bar for a change

Every pull request that touches strategy logic, thresholds, or the significance
gates must answer:

1. **Does it raise or lower the bar for a trade?** Lowering it needs evidence,
   not preference. "It produces more signals" is not evidence.
2. **What did you measure it against?** A change to an indicator needs a
   before/after on the same instrument, same window, same cost model.
3. **Did you add trials?** Any new strategy family, parameter sweep, or
   instrument set adds hypotheses. Update the ledger discipline in `docs/TRIALS.md`
   rather than quietly widening the search.

## Local checks (must pass)

```bash
python -m compileall -q . -x '(^|/)(\.git|tests)/'
python -m unittest discover -s tests -v
```

35 tests, no trading terminal required — MetaTrader5 is stubbed in
`tests/_mt5stub.py`, so the indicator maths and the safety guards run anywhere.
CI runs the same commands on Python 3.11–3.13 and additionally fails the build
if a machine path, account number, or key prefix appears in the tree.

## Regressions worth keeping in mind

Two real bugs in this codebase were silent, and both now have tests:

- **RSI on a flat series returned 100**, because `au/ad` with `ad == 0` went to
  infinity. A dead instrument read as wildly overbought and fired a false short.
- **The MACD signal line was all-NaN**, because the signal EMA seeded from a
  window containing NaNs. A whole strategy family looked implemented and could
  never fire a signal.

Neither produced an error. Both produced plausible-looking output. If you add
indicator code, add the degenerate case to its test.

## Style

- Standard library first. The only runtime dependencies are `numpy`, `pandas`
  and `MetaTrader5`.
- No abstraction with a single implementation and no scaffolding for later use.
- Comments explain *why*, especially where the obvious implementation is wrong —
  see the `ddof=0` note in `bollinger()` and the random-entry rationale in
  `permutation_test()`.

## Never acceptable

- Removing or weakening the human `APPROVED` requirement
- Making any scheduled job able to place an order
- Throwing away trades or trials that did not work out
- Committing a real account number, API key, or personal filesystem path
