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
cannot become a gate by accident. With no `OPENROUTER_API_KEY` the layer records
`not configured` with the questions it would have asked, because an absent layer
must be visible rather than zero. The comparison it enables is the point: the
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
