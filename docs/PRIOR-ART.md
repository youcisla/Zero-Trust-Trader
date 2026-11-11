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

### Adopted today

Nothing new. Every relevant entry is either already installed or a protocol for the
next change — which is the correct outcome of reading a ranking: it maps the
industry, it does not set the backlog.

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
