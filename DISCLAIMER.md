# Disclaimer

**This is research software. It is not financial advice.**

Zero-Trust Trader connects to a MetaTrader 5 terminal and is technically capable
of placing orders. Published for research and educational purposes.

## The honest summary

- **The system has not found an edge.** Every funnel run to date has ended in
  `Market is choppy, no statistically significant edge found today. Standing
  down.` That is a result, not a bug, and it is not a claim that an edge does
  not exist — only that this search did not find one that survives correction.
- **Nothing here predicts prices.** The indicators are ordinary, decades-old
  technical constructs. The contribution of this repository is the machinery
  around them that decides whether a result deserves to be believed.
- **A passing backtest is not proof.** The most a pass means is "worth a small
  forward test with a kill switch". Regimes change; a backtest is one path of
  history.

## Risk

Trading leveraged instruments involves substantial risk of loss and is not
suitable for everyone. You can lose more than you deposit. Past performance does
not guarantee future results.

**Use a demo account.** The pre-flight in `run_funnel.py` actively refuses to
scan a server whose name does not contain `demo`, but that guard is a
convenience, not a safety certification. Verify your own account before running
anything.

## Known limitations of the measurement

- The permutation null is a random-entry test on one instrument. It does not
  correct for the width of the scan — that is the Bonferroni gate's job, and
  neither gate substitutes for the other.
- The trials ledger is only as honest as its history. `LEGACY_TRIALS` is a
  frozen constant; if it is ever edited downward, every significance bar is
  wrong.
- Trades are treated as independent. Overlapping or partially-closed positions
  make the statistics optimistic.
- Costs are modelled approximately (spread as a fraction of ATR). Real slippage
  in fast markets is worse than any model here.
- Tick volume is used as a turnover proxy. It is not true traded notional.

## No warranty

Provided "as is", without warranty of any kind, under the MIT License. The
authors accept no liability for any trading losses, data loss, or damages
arising from use of this software.

If you point this at an account holding real money, that decision is yours
alone — and it contradicts the design intent of the repository.
