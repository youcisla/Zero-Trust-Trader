# Size and ruin: can a small account be multiplied by trading?

This answers one question with arithmetic instead of opinion: **can 50 EUR become
1,000 EUR in 21 trading days?** Everything here is either measured on this
machine or cited, and each section ends with the command that reproduces it.

Short answer: no - and the reason is not a missing strategy. The requirement is
independent of every choice a strategy makes, and the population evidence says
the same thing from three other directions.

---

## 1. The invariant

To reach a target `T` with `S` starting equity you must earn `T - S`. Your
broker closes the position when equity runs out, so the distance to ruin is
also `S` (less margin already committed). Both the profit you need and the
distance that kills you scale with `1 / lots`, so their ratio does not:

```
profit_needed / ruin_distance = (T - S) / S = T/S - 1 = 19x
```

19x is not a target, it is the ratio of "what I want" to "what I have". It does
not improve with leverage, a better instrument, a better stop, or a better
signal. Per day it is `19 / 21 = 0.90x` of the entire distance between the
account and zero, compounded, for 21 consecutive days.

This is the number to check against any claim of "20x in a month".

## 2. What the broker actually allows

Read from the terminal, not from a marketing page. The server leverage is **1:30**
(the EU retail cap), which is the fact that changes everything else.

```
python tools/broker_math.py
```

| symbol | min lot | margin at min lot | openable on 50 EUR? | spread | M15 ATR | spread / ATR |
| --- | --- | --- | --- | --- | --- | --- |
| EURUSD | 0.01 | 33.40 EUR | yes | 10 pt | 0.00063 | **16.0%** |
| XAUUSD | 0.01 | 183.16 EUR | **no** | 18 pt | 8.24 | 2.2% |
| US30 | 0.10 | 233.85 EUR | **no** | 20 pt | 93.22 | 2.1% |
| BTCUSD | 0.01 | 333.12 EUR | **no** | 1500 pt | 368.17 | 4.1% |

On a 50 EUR balance the only position the server will open is **EURUSD, 0.01
lots** - 67% of the account in margin, 16.60 EUR free, **186 pips** before the
margin call. Reaching 1,000 EUR from there needs 507 pips per day, against a
daily range of 60-80 pips. The target is roughly seven times the instrument's
own daily travel, every day.

Two consequences worth stating plainly:

- Gold, indices and crypto - the instruments most often sold as "fast account
  growers" - cannot be opened at all on a small balance at 1:30.
- On M15 EURUSD the spread is ~16% of the ATR you are trying to capture. Every
  trade starts well behind.

## 3. What the best measured edge actually buys

`tier3_deep.py` on the live demo gave XAUUSD/bollinger: n=458, win 36.0%,
avg R +0.102, t 1.215 against a 4.54 bar. Simulating that edge over 63 trades
(3/day for 21 days), compounding, 20,000 paths:

```
python tools/ruin_20x.py
```

| risk per trade | P(reach 20x) | P(ruin) | median outcome |
| --- | --- | --- | --- |
| 1% | 0.00% | 0.00% | 1.06x |
| 2% | 0.00% | 0.00% | 1.12x |
| 5% | 0.00% | 0.00% | 1.27x |
| 10% | 0.08% | 0.00% | 1.37x |
| 20% | 4.54% | 0.27% | 1.00x |
| 35% | 9.81% | 20.7% | 0.22x |
| 50% | **10.3%** | **61.9%** | 0.01x |

The best available outcome is a ~10% lottery ticket paid for with a 62% chance
of losing everything. A zero-edge control at 20% risk reaches 20x 0.81% of the
time, so the edge moves the number by ~3.7 percentage points while costing a
quarter of the median account.

At survivable risk the same edge is worth roughly:

| risk per trade | per month | time to 20x |
| --- | --- | --- |
| 1% | +5.9% | 4.4 years |
| 2% | +11.7% | 2.3 years |
| 5% | +29.3% | 1.0 year |

## 4. The edge is not established in the first place

The projection above is conditional on the edge being real. It is not yet:

```
mean R  +0.093    sd 1.056    se 0.049    t = 1.88
required bar (8,766 trials)               4.54
```

A t of 1.88 is indistinguishable from zero at this sample size. The live run
agreed (`t 1.215 / 4.54, permutation p 0.0609`). Compounding at 15%/day on a
number that has not cleared its own gate is the actual error, before the
month-long question is even asked.

## 5. What the population data says

| evidence | number | sample | source |
| --- | --- | --- | --- |
| Brazilian equity futures, everyone who began 2013-2015 and persisted 300+ days | **97% lost money**; 1.1% beat minimum wage; 0.5% beat a bank teller | 19,646 individuals | Chague, De-Losso, Giovannetti (2019), SSRN 3423101 |
| Taiwan, complete exchange data 1992-2006 | **<1%** earn predictable positive abnormal returns net of fees; 84.3% lose, median -8.7% | ~360,000 traders/year | Barber, Lee, Liu, Odean (2014), J. Financial Markets 18:1-24 |
| EU retail CFD accounts | **74-89% lose money**, average loss 1,600-29,000 EUR | all EU national regulators | ESMA35-43-1000 |
| prop-firm challenge pass rates | **NOT VERIFIED** - no large-sample published figure located | - | - |

## 6. One real flaw this work exposed

The funnel sizes every candidate at the broker minimum (0.01 lots) regardless of
instrument. The backtest is immune - `_trade_row` divides by the per-trade ATR
risk, so R is dimensionless - but the money is not:

```
python tools/vol_dispersion.py --account 200
```

```
median 1R      0.58 EUR   (0.29% of a 200 EUR account)
min / max      0.03 EUR / 22.08 EUR     -> 640x
Cocoa 11.0%   NAS100 4.7%   US30 4.2%   XAUUSD 3.7%   NERUSD 0.02%
```

One "R" is 0.02% of the account on one symbol and 11% on another. Volatility-
targeted sizing (lot = equity x risk% / (ATR x point value)) fixes this. It
changes risk, not returns, and belongs with the first live trade rather than
before it. The same is true of a drawdown circuit breaker - `position_manager`
trails stops and can only reduce risk, but there is no "stop trading after N
losses / X% day" rule anywhere.

## 7. What would change the conclusion

Only four things, and three of them are not trading:

1. **More starting capital.** The invariant is `T/S - 1`. Going from 50 to
   5,000 EUR does not make 20% in a month easy, but it makes it a modest,
   survivable target instead of a lottery.
2. **A longer horizon.** 20x at 2% risk per trade is a ~2.3 year project, not a
   month.
3. **An edge that clears its bar.** Until `t > 4.54` on the cumulative count,
   there is nothing to compound. This is the only item on the list that more
   research can fix.
4. **Income, not returns.** A 50 EUR account cannot generate 950 EUR in a month
   at any survivable risk; a job can. This is not a moral point, it is the same
   arithmetic.

## 8. What was not done

No gate was lowered, no size was raised, `AutoTrading` stayed off and
`auto_approve_notional` stayed at its value. The funnel continues to stand down
when it finds no edge, which is the correct outcome under this arithmetic.

---

## "Can we guarantee an 80% win rate?"

No — but we can *have* one, and the cost is the whole point.

Win rate is not a skill score. It is a dial you set with the distance to the
target, and it moves against the payoff:

    breakeven win rate = 1 / (1 + rr)

At rr = 0.25 (target a quarter of the stop distance) a losing strategy wins 80%
of its trades. Measured on 15,000 M15 bars, 1.5xATR stops, spread charged, the
target actually modelled (`live_exits=False`):

| symbol | rr | trades | win rate | breakeven | avg R |
|---|---|---|---|---|---|
| EURUSD | 0.25 | 819 | 69.5% | 80.0% | −0.131 |
| EURUSD | 0.50 | 723 | 57.1% | 66.7% | −0.140 |
| EURUSD | 1.00 | 568 | 44.4% | 50.0% | −0.111 |
| EURUSD | 1.50 | 498 | 34.9% | 40.0% | −0.143 |
| EURUSD | 3.00 | 382 | 29.3% | 25.0% | −0.001 |
| BTCUSD | 0.25 | 604 | **78.6%** | 80.0% | −0.015 |
| BTCUSD | 0.50 | 572 | 65.6% | 66.7% | −0.008 |
| BTCUSD | 1.00 | 487 | 49.3% | 50.0% | −0.009 |
| BTCUSD | 1.50 | 419 | 38.4% | 40.0% | −0.053 |
| BCHUSD | 0.25 | 735 | 75.1% | 80.0% | −0.060 |
| BCHUSD | 1.50 | 483 | 39.8% | 40.0% | −0.022 |

Read the two columns that matter. The win rate follows the dial obediently —
78.6% is right there, one parameter away. And at **every** setting the result
sits 1–10 points *below* its own breakeven line. The strategy is not
mis-tuned; it is tracking a fair coin and paying the spread to flip it.

That is the shape of the problem. There is no rr that makes 80% profitable,
because 80% and profit are not the same axis. Moving the target closer raises
the hit rate and shrinks the payoff by the same step; the product is what the
spread eats.

**Where "80% guaranteed" actually comes from.** Systems that advertise 90%+ are
usually grids or martingales: keep adding to a loser until the position comes
back, and count that as a win. The win rate is real. So is the day the sequence
does not come back, which erases a year of them. The 19x invariant at the top of
this file is the same arithmetic from the other side: the deeper you let a loser
run, the shorter the distance from the account to zero.

**What can actually be controlled:** expectancy per trade (measured, with a bar
that survives 9,000 comparisons), cost per trade (spread — EURUSD's is 16% of
its M15 ATR), and the fraction of the account risked per trade. A guarantee of
win rate is not among them, and any system that offers one has moved the risk
somewhere you cannot see it.
