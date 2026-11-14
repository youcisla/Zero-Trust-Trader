# Prior art, evaluated

Software we looked at, what it actually is, and what it changed here. An entry
that adopts nothing **and** refuses nothing is not an entry — write it in the
chat instead. Every claim below is either a number someone can re-derive from
this tree or a citation to the source we read.

---

## 1. `day-trader-skill` — 11 Claude skills for US day trading

Read 2026-11-11. MIT, ~6.9k stars, one commit, Python 3.8+, no dependencies.
Scanner, news, entry timer, exit planner, time machine, risk, budget, broker,
alerts, analyst, congress-trades tracker. Plain stdlib Python that reads Yahoo
Finance charts, Nasdaq Trader symbol lists, SEC EDGAR and the House Clerk.

### What we already do, and deeper

| Their component | Ours | Verdict |
| --- | --- | --- |
| Time Machine: 10,000 replays of the rest of the session **plus** every past time the setup fired | `mc` (10,000 replays, seed 19) **and** `history.occurrences/target/stop/neither`, printed side by side in `timemachine_mt5.analyse()` | ours is stricter: the permutation null and the cumulative Bonferroni bar are gates; theirs is a report |
| Exit planner: stop and target before entry | `current.proposed_stop` / `proposed_target`, trailed by `position_manager.next_stop` | ours is a supervisor, theirs is a plan |
| Risk manager: max loss, daily limit, trades/day, PDT guard | demo pre-flight, protective-direction rule, bridge consent | **one real gap: no daily-loss cap** — see *The one gap worth carrying forward* |
| Budget: size to a max loss per trade | `tools/broker_math.py`, `docs/SIZE-AND-RUIN.md` at the broker's real 1:30 | ours is measured against the real terminal |
| Broker: ticket, paper by default, never without a yes | `report_funnel` ticket, `APPROVED` + single-use bridge token | same shape |
| Analysts that cite facts rather than recall numbers | `desk.uncited()` — every number in a note must exist in the source | ours is enforced, theirs is instructed |

Their Time Machine sentence is worth quoting because it is the honest version of
the same idea: *"close to a coin flip. That is the point of looking before you
risk a dollar."* Our default outcome is the same one, and the system reports it
without being asked.

### What we took

**`current.max_loss`** — the loss at the stop, in account currency, from the
broker's own `trade_tick_value` (contract-size fallback), printed on the ticket
with the caveat on the same line: a stop is a plan, not a guarantee. Before this,
the ticket carried `risk` per unit and left the multiplication to a human at the
moment attention is worst. Landed with five tests.

### What we refused

| Idea | Why not |
| --- | --- |
| An entry-timer state machine (waiting / triggered / expired) | our signals are confirmed at bar close by construction; a second decision point in the scan path is a new way to be wrong, and the funnel already re-proposes every 15 minutes |
| Copying congress-trade disclosures | different asset class, no measurable edge, and a public-figures signal on the front page is a brand and legal surface we do not need |
| Eleven small `SKILL.md` modes | their pack proves the shape works, and our `AGENTS.md` is 34 KB against a 20 KB target — but splitting it is churn that belongs to the docs pass, not to this evaluation |
| A 60-day Yahoo window as the evidence base | ours is the broker's own bars, the live spread is charged, and nothing is scraped from an unofficial endpoint |

### The one gap worth carrying forward

They enforce a daily loss limit and a trades-per-day count; we do not. Our caps
are per-order (demo guard, protective direction, consent) and per-hypothesis (the
Bonferroni bar), not per-day. A daily-loss cap is a **policy** change and belongs
in `policy_check.py`, not in the scan — it is not added here because a gate that
limits trading is a decision for the owner, not a refactor.

---

## 2. Jev (`typesafe/jev-1.14`) — a decision model, not a chat model

Read 2026-11-11 from OpenRouter's guide, the "What is Jev" post, the
`jev-verified-cascade` cookbook and the Banking77 comparison.

### What it is

A *System One* model: send `state` plus typed questions, get back a typed answer
with probabilities and no prose. Four primitives — Choice, Noul (does this hold →
P(yes)), Score, Box. `$0.042` per million input tokens, output free, p50 175 ms.
On Banking77 (77 intents, 3,080 utterances): **81.0%** vs Claude Opus 5's
84.4%, 13× faster, 1/22 the cost; confidence is **not calibrated** (96.3% accurate
at ≥0.99, 29.6% below 0.5). The documentation is explicit: it "is not a drop-in
replacement for a chat model" and produces no reasoning traces.

### Why it cannot replace the desk model

The desk's product is the sentence a human reads before typing `APPROVED` —
`summary`, `contradictions`, `watch`, `why`, `conditions`. That is generative
output; Jev returns a label. Replacing DeepSeek would delete the reason, not
reduce the price. There is no version of "better than DeepSeek" here: they
answer different questions.

### Why it is not needed as a validation layer either

The validation layer already exists, and it is deterministic — which is stronger
than a probability you threshold:

| Check | Enforces |
| --- | --- |
| `desk._validate_note` | field schema per role; `verdict` inside its allowed set; `size_lots` exactly 0.01 (the judge **may not size an order**) |
| `desk.uncited` | every number in a note must exist in the source it was shown — a fabricated figure fails the whole note |
| scout rule | may only name symbols that appear in the snapshot it was given |

That is the same draft-verify-escalate shape as their cascade, with a verifier
that cannot be talked into a different answer.

### The measurement that mattered

`desk_history.sqlite`, 204 role answers at the time of writing (the file grows;
re-running the query gives a different ratio): **9 of 37 chief notes rejected
(24%)**, and seven of the reasons were fragments — `uncited numbers: 22, 835` —
of a figure the model had quoted correctly. `uncited()` split `21,854` at the comma
and looked for `21` and `854` in a source holding `21854`.

Fixed: digit-group commas are normalised on both sides before the scan; a list
(`1,2`) and a European decimal (`2.191`) are untouched; a fabrication is still
rejected and now reported as the figure the model wrote. Five tests, including
the one that would have caught it.

The lesson generalises: **before buying a probabilistic verifier, measure the
false-positive rate of the deterministic one.** It was 24% on the role that
matters most, and no model would have told us — the journal did.

### The one thing Jev would add

`uncited` only sees numbers. A chief note saying *"volatility is compressing and
the edge is decaying"* passes with zero digits and may be entirely invented.
Semantic grounding is the gap, and a regex cannot close it.

Two ways, in order of preference:

1. **Deterministic:** require each qualitative claim to carry a fact key from the
   digest, checked against the sections that exist. ~30 lines, no key, no latency.
   Cost: a prompt change and a rejection cycle while the model learns the format.
2. **Jev, assistive only:** one Noul per claim, ~`$0.00004` per desk run (about
   `$0.002`/day at 48 runs). It must **never reject** — record the verdict in the
   journal, render it on the card, and only after 50 runs of measured agreement
   above a threshold may it earn the right to refuse anything.

A decision model that is 81% right on someone else's classification task does not
belong in the refusal path of a system whose thesis is that we do not trade at
78% confidence. If it is ever added, it is added the way a strategy family is
added: measured against this tree's own claims first.

**Implemented 2026-11-11 as the third layer, not as a gate.** `desk.jev()` sends
the same digest and the desk's own verdict to `typesafe/jev-1.14` and asks two
typed questions — *what deserves a person's attention* (show / stand_down /
escalate) and *is the desk's verdict grounded in the figures it cites* — and
records the choice, the probability and the confidence. Nothing reads the answer
back: a test asserts the record is written and never consulted, so the layer
cannot become a gate by accident. The layer is off by default (`desk.toml`
`[decisions] jev_enabled` or `ZTT_JEV=1` turns it on): off, the record says
`disabled`; on without `OPENROUTER_API_KEY`, it says `not configured` with the
questions it would have asked, because an absent layer must be visible rather
than zero. The comparison it enables is the point: the
engine computes, the desk writes a sentence, the decision model returns a label
and a probability, and all three sit on one page next to the same evidence.

---

## 3. The rankings, read 2026-11-11

Trendshift's four boards (daily / weekly / monthly / yearly, 25 each) and
star-history's two (all-time by stars, weekly by gain). Method: dedupe, then keep
only what could change how Hermes or this tree is built. Everything else is
dropped without a note — a ranking is not a reading list.

### The headline: the yearly board describes this machine

Twelve of the twenty-five yearly entries are already here — `openclaw` (migrated
from), `obra/superpowers`, `morluto/rea`, `NousResearch/hermes-agent` (the
harness writing this), `mattpocock/skills`, `msitarzewski/agency-agents`,
`opencode`, `DietrichGebert/ponytail` (the mode this session runs in),
`thedotmack/claude-mem`, `Graphify-Labs/graphify`, `garrytan/gstack`,
`anthropics/claude-code`. Two more exist in another form: `anthropics/skills` as
the `claude-api` and `brand-guidelines` skills, and `firecrawl` as the fetch
backend. The board is a description of the toolchain, not a shopping list, and the
marginal value of reading it is entirely in the ten entries we do not have.

### Ranking is not quality, and the daily board proves it

Roughly ten of the daily twenty-five are "installer" repositories created
2026-11-06, with **HTML as their only language** and ~1,300 stars in five days:
`cubase-studio-desktop`, `archicad-desktop-setup`, `adobe-substance-3d-studio`,
`aida-64-extreme`, and their siblings. That is the exact shape of a cracked-software
malware drop, and the board ranks it because stars are cheap at volume. The same
pattern appears in the monthly list as clones of a vendor tool —
`ccch1mneyyy/dsh-Go` (19.7k stars in 17 days, a Dart wrapper around DeepSeek's
harness) and `Ebony-Vinyl/dsh-our-free-model`. **Never install from a ranking.**
Check the account's age, the star count against the size of the code, and whether
the language matches the claim.

### What was worth reading

| Repository | Board | What it is | Verdict |
| --- | --- | --- | --- |
| `TauricResearch/TradingAgents` | yearly #22, 122k | multi-agent trading framework, arXiv 2412.20138, Python, still shipping (v0.7.1 in October) | **read** — three lessons below |
| `deepseek-ai/dsh-superagent-prototype` | monthly #2, 22k | a proactive coding agent for one repo, with an explicit theory of attention | **adopt the attention rule** — below |
| `cloudflare/security-audit-skill` | monthly #20, 70k | a six-phase audit protocol with a coverage ledger and independent verifiers | **adopt for the next audit** |
| `GoogleChrome/modern-web-guidance` | daily #3, 4.8k | web-platform guidance packaged as an agent skill | read when the dashboard next changes |
| `janestreet/agent_skills` | daily #2, 5.5k | created 2026-11-03, one push, no README on `main` | nothing to read yet — recheck |
| `meta/astryx` | weekly #1, 24.7k | a design system | no — Sentio has its own |
| `deepseek-ai/deepseek-harness` | yearly #11, 279k | DeepSeek's plugin-based agent harness | no — our harness is Hermes |
| `storytold/craft-anything` | monthly #1, 28k | reverse-engineering toolkit | already installed |
| `we-promise/sure` | daily #6, 12.8k | a personal-finance app in Ruby | no |

### TradingAgents: three things they learned the hard way

1. **Identity and prices are resolved deterministically before any agent runs.**
   Their release notes say earlier runs produced "different companies" and
   "fabricated price levels"; the fix was to resolve the ticker before the agents
   start and to ground every price claim in a verified snapshot. That is precisely
   what `desk.uncited()` does here, arrived at independently — the strongest signal
   in this whole exercise is that two projects converged on the same structural
   answer rather than on a better prompt.
2. **Point-in-time integrity.** v0.7.1 fixed "model dates that could read filings
   made after the run date". Our equivalent exposure is narrow — the desk reads the
   tree at run time and is advisory — but it is exactly why every tier-3 record
   carries `timestamp_basis` and `timestamp_limitations` rather than assuming the
   broker's offset never moved.
3. **A decision log that gets settled.** They keep past decisions and score them
   against what subsequently happened. We log every run and never score it. That is
   the one real gap this survey found: we cannot currently answer "was the desk
   right?" — only "what did it say?". Everything needed to close it is already
   stored.

### Cloudflare's audit protocol: what our 72-finding audit lacked

Our audit was one pass, by one agent, producing findings of one kind. Theirs has a
coverage ledger (what was *examined*, not just what was found), a fresh verifier
for every candidate that never the finder, three verdicts — `confirmed`,
`needs_validation`, `rejected` — and a rule that a defense-in-depth gap is a
hardening note, not a vulnerability. Their measured observation is the one that
matters: *a single run found roughly half of the vulnerabilities that repeated
runs found in total.*

Rule for the next audit here: keep the ledger, split finder from verifier, and do
not present one pass as coverage.

### dsh-superagent: the sentence that fixed the desk

Their philosophy document states the rule the desk was missing: **"It speaks only
when a person must act or can use a result."** Everything else follows from it —
one concrete question at a time, a panel that lists only what waits for a person,
and "when the agent is noisy, make its prompts shorter and stricter, or pause it.
Do not add more messages to explain the noise."

Applied here, that became one line in the jury's schema: **an escalation must name
the action a person takes**, or it is refused as a malformed note. The prompt says
the same thing in the voice the model reads: if you cannot name the action, stand
down. A desk that escalates without an action is asking a person to go and find out
what to do, which is the most expensive thing it can produce.

Their second lesson is the instrument: a healthy state takes no space. That is why
the scorecard reports the escalation *rate* rather than a list of escalations, and
why it can be split at a moment — the only question that matters after a fix.

### Adopted: the scorecard, and the legend it exposed

The survey's one gap — a decision log that is never settled — became
`tools/desk_scorecard.py`, and building it immediately paid for itself. It showed
that the jury had escalated on **34 of 45 judged runs (76%)**, including six in a
row. That is not a desk exercising judgement; it is a desk with a stuck gate.

The reasons were all the same shape: `tier2` close against `tier3` reference_bid a
few seconds later, `significance.z` against `permutation.actual_z`, an empty
`deduped` list against a ledger count of 0. Every one of those pairs is two
different things, and the digest showed them as raw JSON with nothing saying so —
so the model, told to name contradictions, named the only differences it could
find. The fix is a `FIELD LEGEND` at the top of the digest that says which figure
is which moment and which statistic, and one sentence in the chief and jury
prompts: a disagreement the legend explains is not a contradiction.

Measured once after the change: `stand_down`, zero chief contradictions, and a
`why` that cites the legend by name ("the two-statistic difference the legend
covers"). One run is a direction, not a result — the scorecard is the instrument
that will settle it.

---

## 4. Standing conclusions

- **A refusal is cheaper than an integration.** Five of the seven ideas above
  were declined, and the tree is smaller for it.
- **Copy the number, not the code.** What transferred from `day-trader-skill` was
  one field and its caveat; what did not transfer was an architecture built for a
  different asset class and a different data source.
- **A verifier you cannot argue with beats one you can.** The desk's guard is
  arithmetic; a probabilistic check is a second opinion, and second opinions are
  for humans, not for gates.
- **A ranking measures attention, not engineering.** The yearly board described
  this machine; the daily board described a malware campaign. Both were correct
  about what they measure, and neither is a backlog.
- **Convergence is the best evidence.** Two independent projects arriving at
  "ground every number in the source, before any agent runs" is worth more than any
  single benchmark in either README.

---

## 5. A 16-module backtest validation framework, and the López de Prado checklist — what the 30-day sweep on multiple testing found

Read 2026-11-13. A `last30days` run over 2026-10-14 → 2026-11-13 on "backtest
overfitting / multiple-testing correction in retail algorithmic trading"
returned **24 items across 3 active sources** (GitHub 19, Hacker News 4,
YouTube 1), and its own freshness header said **5 of 24** dated items came from
the last seven days. Reddit was rate-limited inside that sweep (HTTP 429 in its
coverage block, 403 in its error block), so
the threads below came from `agent-reach reddit search`; Reddit's read then
answered with a login wall, so they are cited from search results, not full
threads. `agent-reach github search` failed on this host (socket access denied),
so the issue below was read from its own API page. The thinness is the first
finding: the topic is settled and old, and the last 30 days added nothing new to
it.

- https://github.com/starsstar2327-commits/Quant-project-/issues/8 — a 16-module
  framework, October 2026, written as a sequence of pass/fail gates
- https://www.reddit.com/r/quant/comments/1rlobeh/opensourced_a_cheat_sheet_on_lopez_de_prados/ —
  CPCV, Deflated Sharpe, Probability of Backtest Overfitting, purging and
  embargoing
- https://www.reddit.com/r/algotrading/comments/1shokdu/dont_be_afraid_to_overfit/ —
  the adversarial take, which concedes the point in its own top reply

### What we already do

| Their module | Ours | Verdict |
| --- | --- | --- |
| M1 execution timing: signal on close, fill at next open | `fetch` drops the forming bar; signals are confirmed at bar close | same boundary, held structurally |
| M2 significance under multiple testing: Φ⁻¹(1 − α/2N), **N is what was actually tried** | gate 5, `bonferroni_z(cumulative)` — and `student_t_crit(0.05/N, df)` when the statistic is the mean-R t | same formula, wider N |
| M3/M13 block bootstrap: resample 10–20 bar blocks, the 5th-percentile drawdown must be **deeper** than the historical one | `simulate()`: 10,000 replays, seed 19, real bars resampled by slot of day ±2 from a 1,500-bar pool | preserves intraday seasonality, not multi-bar dependence |
| M4 walk-forward: rolling train/test | tier 3 on 15,000 bars, both gates | one window, no rolling split |
| M9 correlation: a new stream above ρ 0.7 adds little | gate 4 dedupe at \|ρ\| 0.7, keeps the higher z | same threshold, independent arrival |
| M16 cost sensitivity: 2D commission × slippage matrix, the realistic cell decides | the live spread charged per symbol | one measured point, not a matrix |
| — | gate 6, a random-entry permutation null at p < 0.05, 1,000 permutations | none of the 16 modules has one |

The arithmetic agrees exactly, which is the confirmation the ticket asked for.
Their M2 threshold is Φ⁻¹(1 − α/2N) and ours is `bonferroni_z(n)` of the same
quantity: `bonferroni_z(170)` returns `3.62`, the number their text states for
N = 170. At this tree's frozen history, `bonferroni_z(2_205)` returns `4.237`;
the cumulative count when this was read (2,205 legacy + 49,479 logged = 51,684)
gives `4.898`, and each scan adds its 50/100/150 tests on top. Their
"the relevant N is not 1" discipline is our `required_trials()`, and we count it
across the life of the machine rather than across one parameter grid.

### What we took

**One named measurement for §7, and the checklist to run it with.** The
Deflated Sharpe Ratio and the Probability of Backtest Overfitting are the two
documented ways to ask what our Bonferroni bar cannot: whether the *selected*
candidate is real given the dispersion of every candidate tried, using the
return series rather than a win-rate count. Neither is adopted here — a
correction change is a hypothesis under §7, with before and after numbers on
the same bars — but both now have a page number in this file. The r/algotrading
thread converges on the experiment design: multiple out-of-sample stages, regime
stability, and low sensitivity to parameter changes, which is a better test of a
new correction than a single holdout.

### What we refused

| Idea | Why not |
| --- | --- |
| Swapping Bonferroni for DSR or PBO as the gate | DSR needs a per-candidate Sharpe with skew and kurtosis over T observations; the gate judges a win-rate z or a mean-R t on decided trades. A newer statistic that resets the cumulative record is not a tightening, and §4.2 forbids the shape of the change |
| A noise-injection look-ahead audit as a gate | our look-ahead boundary is the forming bar dropped in `fetch`, true by construction and cheap; their M6 re-runs the strategy at every timestamp, which is minutes per symbol inside a scan that already takes tens of minutes. Worth a `tools/` audit one day, under §7's before/after rule |
| A block bootstrap inside the live Monte Carlo | it changes the drawdown distribution `docs/SIZE-AND-RUIN.md` is built on; a silent change to a sizing input is the class of edit §4 exists to stop |
| "Don't be afraid to overfit" | its own top reply is our gate: 10,000 backtests and one god-tier equity curve is selection bias, not a strategy. The thread's answer — control for data mining — is what gate 5 already does |

---

## 6. TradingAgents (`TauricResearch/TradingAgents`) — a firm of LLM analysts and a simulated exchange

Read 2026-11-14. Apache-2.0, ~122.6k stars as `gh repo view` reported them, Python, created 2024-12-28, last push 2026-11-04. A LangGraph multi-agent framework: fundamentals, sentiment, news and technical analysts run in parallel, a bullish/bearish researcher debate follows, then a trader, a risk team and a portfolio manager drive `TradingAgentsGraph().propagate(ticker, date)` over any provider (OpenAI, Anthropic, DeepSeek, Qwen, GLM, Ollama, a ChatGPT plan). It reads SEC EDGAR fundamentals as filed, keeps a decision memory log, and scores a backtest grid on realized alpha against a regional benchmark. https://github.com/TauricResearch/TradingAgents

Its own README says it is "not a strategy with a fixed, replicable return" and that two runs of the same ticker and date can differ.

### What we already do

| Their component | Ours | Verdict |
| --- | --- | --- |
| Portfolio Manager approves and sends to a **simulated exchange** | no code path places an order; human `APPROVED` + single-use bridge token; demo-only pre-flight | ours refuses execution structurally, not by a flag |
| Sentiment / News / Fundamentals / Technical analysts, four roles | `desk.py` scout / skeptic / chief / jury, four roles, post-scan and advisory | same shape; ours cannot gate or size (the jury's `size_lots` is forced to 0.01) |
| "Grounds exact price and indicator claims in a verified data snapshot" | `desk._guard()` rejects a note containing any number absent from its source artifact | ours is enforced (whole note rejected), theirs is a grounding step |
| Memory log settles each past decision and reflects | `desk_history.sqlite` + `docs/desk-history.jsonl`, and `tools/desk_scorecard.py` measures a role's firing rate | ours is queryable history, theirs is per-ticker prose |
| Technical analyst uses MACD and RSI | MACD was removed as duplicate trend exposure (`AGENTS.md` §7); RSI kept with a flat-series fix | ours carries the degenerate-case test |
| Point-in-time SEC EDGAR fundamentals | the broker's own bars, forming bar dropped in `fetch` | different data, the same look-ahead concern |
| Backtest scores realized alpha vs a regional benchmark | gate 5 cumulative Bonferroni + gate 6 random-entry permutation | ours corrects for the number of hypotheses; theirs reports |

### What we took

A page number for the desk's own structure, and one question to test under §7 rather than adopt here. Their separation of an analyst team from a risk team, with one portfolio manager as the single approver, is the boundary `desk.py` already draws between the advisory three and the jury — so this evaluation confirms the shape instead of changing it. The one thing the README states that ours does not yet act on is passing the **actual portfolio** to the risk roles so the analysis is written against the real book; our desk reads the snapshot, and whether the risk roles should receive an explicit position context is a desk question, not an execution one. Nothing lands in code: this is a read-only ticket.

### What we refused

| Idea | Why not |
| --- | --- |
| The portfolio manager's authority to send an approved order | `AGENTS.md` §1: the agent may research and change the system, it may not trade. "Approved by a model" is not gate 8, the human `APPROVED` + one-time bridge consent |
| A framework of eight LLM roles around one decision | a scan must not block on a model call or a provider's latency; `agent_brief.py` runs after the scan and writes one derived file. More roles is more prose, not more gate |
| Its backtest grid as evidence of an edge | scored by realized alpha with no multiple-testing correction over a ticker x date grid; our permutation null exists because a p of 0.015 is expected ~8 times in a 568-symbol scan (`AGENTS.md` §3) |
| Reproducibility by temperature | the README concedes sampling is non-deterministic and reasoning models ignore temperature; a gate's resolution may not vary between runs (§4.10, the same reason the permutation count is fixed) |

---

## 7. Vibe-Trading (`HKUDS/Vibe-Trading`) — a personal agent with live broker connectors

Read 2026-11-14. MIT, ~38.2k stars as reported, Python 3.11+ with FastAPI and React 19, created 2026-04-01, last push on the day this was read. CLI, Web, HTTP API and an MCP server around one agent: market research, an "Alpha Zoo" of 478 signals across five libraries (Qlib, Alpha101, GTJA, and the academic and fundamental libraries), backtest engines for A-share, crypto and futures, an options lab, paper trading, and **19 broker connectors** of which alpaca, binance, etoro, futu, **mt5**, okx, robinhood and tiger are marked live. https://github.com/HKUDS/Vibe-Trading

Its disclaimer says the broker-trading capability is "experimental and not verified by us against a real broker account".

### What we already do

| Their feature | Ours | Verdict |
| --- | --- | --- |
| Live trading under a "mandate", halted by a kill switch | `KILL_SWITCH` freezes scan and supervisor, but no connector can place an order — human + bridge token only | ours refuses by construction; theirs refuses by configuration |
| Alpha Zoo, 478 factors, "check whether the result survives a walk-forward test" | `trials_total` counts every hypothesis; gate 5 corrects against the cumulative count, never the zoo's best | 478 uncounted tests is 478 ways to look significant |
| Research Lab: select on train, freeze, test out of sample, separate final holdout | tier 3 on 15,000 bars, both gates, one window (§5 already notes no rolling split) | theirs has the split we lack; we have the correction it does not show |
| 28 market-data sources with smart fallback | the broker's own bars, live spread charged per symbol | one measured source beats a fallback chain for a gate |
| `lookahead-analysis`, run records, dated research artifacts | `fetch` drops the forming bar; the ledger is the record; `desk_history` is the advisory record | same concern, different mechanism |
| Web + API + MCP + CLI, several write surfaces | the dashboard has exactly one write (`POST /api/scan`) and no order route | ours is a monitor; theirs is a control surface |

### What we took

The Research Lab's stated protocol — choose parameters on training results only, then freeze them for an out-of-sample test and a separate final holdout — is the one discipline here worth naming for §7. It is the split our single 15,000-bar window does not make (already noted beside the r/quant list in §5), and naming it changes no gate. Nothing lands in code.

### What we refused

| Idea | Why not |
| --- | --- |
| The live-broker execution path, MT5 included, however gated | `AGENTS.md` §1 and gate 8: an in-app mandate is not a human `APPROVED` plus a single-use bridge token, and their own README calls it unverified against a real account |
| The 478-alpha zoo as a shortcut to a candidate | every factor is a hypothesis; the bar is cumulative `bonferroni_z(trials_total)` plus a permutation null, neither of which a zoo clears by being large |
| A 28-source data fallback chain | a backtest on a scraped unofficial endpoint with an inferred market is not the broker's market; our spread is charged from the live terminal |
| A second and third write surface (Web, MCP, HTTP) | this process has one write by design (`AGENTS.md` §9); `POST /api/scan` runs exactly what the scheduler runs and places nothing |

---

## 8. Kronos (`shiyu-coder/Kronos`) — a foundation model for K-line sequences

Read 2026-11-14. MIT, ~47.8k stars as reported, Python 3.10+, created 2025-07-01, last push 2026-04-13; accepted at AAAI 2026. A decoder-only, autoregressive transformer over discrete tokens: a tokenizer quantizes multi-dimensional OHLCV K-lines into hierarchical tokens, and the model is pretrained on data from "over 45 global exchanges". Four sizes, 4.1M to 499.2M parameters (the 499.2M large model is not open-sourced); context 512 for small/base, 2048 for mini. `KronosPredictor.predict(...)` returns a sampled OHLCV forecast (`T`, `top_p`, `sample_count`), and a Qlib finetune pipeline ends in a "simple top-K" backtest. https://github.com/shiyu-coder/Kronos

Its README calls that pipeline "a simplified example and not a production-ready quantitative trading system", and says raw signals need portfolio optimization and risk-factor neutralization before they are alpha.

### What we already do

| Their component | Ours | Verdict |
| --- | --- | --- |
| A forecast of the next K bars | a measured trade — entry, stop, target — scored over 10,000 Monte-Carlo replays (seed 19) | ours is scored against a null; a forecast is not |
| `sample_count` averaged probabilistic paths | `simulate()` resamples real bars by slot of day ±2 from a 1,500-bar pool | both are distributions; ours is gated |
| Simple top-K A-share backtest on daily bars | tier 3 deep test on 15,000 bars, both gates, live spread charged | one backtest, no correction, vs cumulative Bonferroni + permutation |
| A 100M–500M-parameter model | indicators with a degenerate-case test (flat series, zero range, short series), `AGENTS.md` §6 | ours is auditable line by line; a foundation model is not |
| Tokens quantized from OHLCV | the broker's bars via `marketdata.py`, forming bar dropped | no modeling of the data, just the data |

### What we took

A page number and a negative result. A frozen, reproducible forecaster is the opposite of the desk's citation rule: `desk._guard()` requires every number to already exist in the source artifact, and a sampled K-line path is a number that exists nowhere. If a Kronos-family model is ever probed it belongs in `tools/` under §7 — measured on the same bars and costs, counted as a hypothesis — never in the scan or the order path. Nothing lands in code.

### What we refused

| Idea | Why not |
| --- | --- |
| Using Kronos forecasts as a signal | a sampled forecast has no Bonferroni bar and no permutation null; the same candidate could pass one run and miss the next, which §4.10 forbids for a gate |
| A GPU model on the scan path | one terminal, one IPC channel, a scan that already takes tens of minutes (`AGENTS.md` §10); inference latency inside the run window is a new failure mode |
| Its top-K backtest as evidence | a single split, none of the cost modeling the pipeline itself says is missing, no correction; the README disclaims production readiness |
| Folding a 500M-parameter checkpoint into the advisory desk | nothing a model emits can enter `agent_notes.json` without passing `_guard`; an unlisted model input is a way to put an uncited number in front of the jury |

---

## 9. freqtrade (+ FreqAI) (`freqtrade/freqtrade`) — a crypto bot with two bias analysers

Read 2026-11-14. GPL-3.0, ~56.9k stars as reported, Python 3.12+, TA-Lib, sqlite persistence, created 2017-05-17, last push 2026-11-12. A long-lived crypto trading bot: backtesting, `hyperopt` parameter search, a webUI and Telegram control, spot and futures on Binance, Bybit, OKX, Kraken, Gate, Bitget, HTX and Hyperliquid among others, dry-run by default. **FreqAI** is its "adaptive prediction modeling" layer — a model that self-trains to the market and feeds a strategy. Two CLI commands matter here: `lookahead-analysis`, "check for potential look ahead bias", and `recursive-analysis`, "check for potential recursive formula issue". https://github.com/freqtrade/freqtrade

### What we already do

| Their component | Ours | Verdict |
| --- | --- | --- |
| `lookahead-analysis` re-runs a strategy to detect look-ahead | `fetch` drops the forming bar; signals are confirmed at bar close | ours is structural and cheap; theirs is a check you run |
| `recursive-analysis` finds an indicator whose value depends on its own prior value | every indicator is closed-form over a fixed lookback, and the degenerate cases are tested (`AGENTS.md` §6) | no recursive formula to find |
| `hyperopt` searches a parameter space for a passing set | every parameter set is a hypothesis; `trials_total` counts it and gate 5 corrects against the cumulative N | theirs finds a winner; ours pays for the search |
| FreqAI: a model that self-trains beside the strategy | the desk is advisory, runs after the scan, and can place nothing; `agent_brief.py` writes one derived file and one audit row | ours has no model in the order path |
| Dry-run as the default mode | demo-only pre-flight refuses a server whose name lacks `demo`, structurally (gate 1) | theirs is a mode a user leaves; ours is a gate |
| A webUI and Telegram that start, stop and `/forceexit` trades | the dashboard is read-only, one write (`POST /api/scan`), no order route | ours cannot be a control surface |

### What we took

Nothing new lands from freqtrade, and that is the finding. The two analyses it names are the two silent-bug classes `AGENTS.md` §6 already records as having happened here — RSI returning 100 on a flat series, MACD's signal line all-NaN — and this evaluation is the external confirmation that both are worth a named command. If a look-ahead audit is ever wanted in `tools/`, `lookahead-analysis` is the reference design. Nothing lands in code.

### What we refused

| Idea | Why not |
| --- | --- |
| The bot itself — live orders on a dozen-plus exchanges | `AGENTS.md` §1; it exists to trade, and trading is the one thing this repository's agent may not do |
| `hyperopt` as an edge generator | a search over parameters without a lifetime cumulative correction is gate 5 bypassed by construction, the same trap `AGENTS.md` §3 warns about when widening a session |
| FreqAI's self-training model in the scan or order path | a model whose weights move with the market between runs changes the gate's resolution; §4.10's fixed permutation count exists to stop exactly that |
| Pasting any of its code | GPL-3.0 is copyleft; we cite the design, not the source, because copying it would carry obligations this tree has not taken on |
| A webUI or Telegram channel that can start or stop trading | `AGENTS.md` §9: never add an endpoint that places, modifies or closes an order |
