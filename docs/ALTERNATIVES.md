# Alternatives we looked at, and what we took from them

Two sources, both about *charting platforms* rather than data: the
[r/TradingView "alternatives" thread](https://www.reddit.com/r/TradingView/comments/1woge5d/tradingview_alternatives_what_are_you_guys_using/)
(Nov 2026) and [OpenCharts](https://github.com/dylanpersonguy/OpenCharts)
(MIT, TypeScript, ~250 stars, last push 2 Nov 2026).

Short version: nothing here replaces the broker as our data source, and the one
idea worth stealing needs a feature we do not have yet.

## What the thread actually says

The question was "what do you use instead of TradingView", and the answers are
platforms a human trades on, not datasets:

| Mentioned | Why it came up |
| --- | --- |
| Quantower | the closest match for TV-style charts with a serious order panel |
| NinjaTrader / Tradovate | futures, custom drag-and-drop bracket tools |
| ThinkorSwim, TradeStation, TC2000, DeepVue, eSignal | established broker terminals |
| TrendSpider | indicators in JavaScript, so Pine ports are easy |
| GoCharting, LuxAlgo Vela | TradingView **under the hood** — they license the chart and the Pine runtime |
| MT5 | "you can drag SL/TP directly, but only after entry" |

Two things follow, and neither is new to us:

1. **Every recommendation is a broker terminal or a reseller of TradingView's own
   engine.** There is no free data firehose in that thread — which is consistent
   with what the licensing text says, and it is why this repository backtests on
   the book it will trade.
2. **The feature people actually want is bracket editing on the chart** (drag
   entry, stop, target; see what is live at the broker). We are structurally not
   allowed to offer it: the dashboard is read-only, and execution is a human
   typing `APPROVED` plus the bridge token (`AGENTS.md` §1, §9). Copying that
   feature would delete the product.

## What OpenCharts is

A self-contained, browser-only trading terminal: candlestick chart with a
TradingView-style drawing toolbar, 8 indicators, watchlist, depth-of-market
ladder, order ticket, session replay and an in-browser paper-trading engine
seeded with real Binance klines bundled at build time. No backend, no keys, no
signup. MIT.

Its honesty section is worth reading as a model: it labels exactly which parts
are real (OHLC, fills, P&L) and which are simulated (DOM sizes, spreads, the news
tab), and it says the components that faked market analysis — a sentiment gauge
and "STRONG BUY" signals derived from a hash of the symbol name — were *deleted*
rather than left for someone to wire up. That is the same discipline this repo
applies to `—` versus `0`.

**The integration contract is the useful part.** You do not fork it: you
implement one interface and hand it over.

```ts
const adapter: OpenChartsAdapter = {
  name: "my-broker",
  marketData: {
    getSymbols: () => myApi.instruments(),
    getCandles: ({ symbol, timeframe, limit }) => myApi.candles(symbol, timeframe, limit),
    subscribe: (handler) => myFeed.on("quote", (q) => handler({ type: "tick", ...q })),
  },
};
```

`marketData` is the only required group; `trading` and `storage` are optional.
Everything else — chart, ticket, replay — runs against whatever you pass in.

## Verdict for this repository

**Not integrating it.** Three reasons, in order of weight:

1. **It is a Node/Next.js app.** Our dashboard is one Rust process with no build
   step and no `node_modules` (`AGENTS.md` §9, "Why not Next.js"). Embedding
   OpenCharts would reintroduce exactly what that design avoids, and running it
   beside the dashboard gives two UIs that can disagree about the same state.
2. **We have no OHLC to draw.** The dashboard reads files; the only bars in the
   tree are inside the backtest, in Python, and they never reach the snapshot. A
   candle chart today would be an empty axis — the same lie as a flat line for a
   missing series.
3. **The order ticket is forbidden here.** The half of OpenCharts that makes it
   interesting is the half we must not have.

## What would actually be worth building, if you want candles

Priced, because it is real work and it should be chosen, not assumed:

| Piece | Where | Size |
| --- | --- | --- |
| export the last N bars for each deep-tested candidate to `candidate_bars/<symbol>_<tf>.json` | `tier3_deep.py`, after `analyse()` returns | ~40 lines |
| `GET /api/candles?symbol=&tf=` reading that file, bounded, `null` when absent | `dashboard/src/main.rs` (a **read** route — the one-write rule is untouched) | ~40 lines |
| vendored `lightweight-charts` standalone build + a candle card on `#/research` | `dashboard/web/vendor/` + `index.html`, with a `PROVENANCE.json` entry | ~120 lines |

That is the TradingView-class chart people keep asking for, drawn from **our own
broker's bars**, in a page that still cannot place an order. Roughly half a day.
Not built yet: nothing on screen asked for it, and a chart of bars we do not
currently export would be decoration.
