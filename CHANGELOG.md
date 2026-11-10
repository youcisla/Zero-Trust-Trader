# Changelog

Adopted 2026-11-10 (Keep a Changelog shape). History before 2026-11-07 was
re-rooted when the project went public, so it is not listed here.

## [Unreleased]

### Added
- `9333666` feat(dashboard): pipeline canvas — six stages, live wires, stage inspector
- `8dfd081` feat(dashboard): serve the Archify stage map at /architecture
- `1153a35` feat(dashboard): Minecraft skin — blocks, hearts, hunger, XP bar, hotbar
- `fdf3d30` feat(dashboard): the canvas fits the width, and the numbers get a chart
- `c0791de` feat(desk): three advisory agents, and a guard against the empty universe
- `eb85eb6` feat(desk): the rulebook every agent reads, and a journal that outlives a run
- `011ed58` feat(dashboard): an app shell with pages, and a design that reads as a tool
- `5597db3` feat(desk): record the model the provider actually served
- `15b68b0` feat: a wider scan, and the four fields its artifacts were missing
- `f719a51` feat(desk): four agents with a voice, and a record of everything they said
- `e878584` feat: shares in the universe, and a scan you can start and watch
- `a8da643` feat(dashboard): one duration helper, so no counter reads 41940s again
- `182cbea` feat(dashboard): six pages, a live flow canvas, and the series it never had
- `b197c28` feat(dashboard): filters, pagination, a freeze control, and a socket that heals
- `d74256f` feat(universe): 0 means everything, and the caps are explicit
- `81e8416` feat(dashboard): what each agent spent, per role
- `6f88909` feat(docs): a check, because prose could not keep the map honest

### Changed
- `da67e15` chore(status): fall back to the tree-local config.toml when the per-user one is absent
- `c44e718` test(funnel): prove --t-gate reaches tier3_deep without touching the ledger
- `f913e16` test(offset): install the MetaTrader5 stub before importing marketdata
- `5f6e94f` perf(dashboard): bounded tail, O(1) clip, and a monitor that fails visibly
- `c6900bc` research(crypto): all twelve crypto combinations are negative, and the only affordable one is not the least bad
- `ead4260` chore: the crypto probe inputs were runtime files, not source
- `15b3bb8` chore(terminal): Market Watch was holding 1,626 of 1,723 symbols
- `ae308e9` audit remediation: risk supervisor, process locks, trial accounting, CI

### Fixed
- `fc252af` fix(marketdata): take the broker clock from the freshest probe tick
- `c9fbbbe` fix(dashboard): stop the UI claiming "flat" when it is blind
- `fa19ebf` fix(dashboard): wrapped header, blind Mode card, and JSON localised in the audit pane
- `a103ac4` fix(dashboard): the canvas says 'no scan data', not 'STAND DOWN', when nothing has run
- `c816cae` fix(supervisor): exclusive-create its lock, and say out loud when the ledger is unsigned
- `9938376` fix(funnel): an exit code is a claim; check that the artifact is this run's
- `e89c389` fix(desk): the guard was rejecting honest notes, and a failed run said ok
- `9861d7c` fix(tier3): the log line said 8952 tests were 62 x 3
- `1ae2887` fix: two defects the e2e round found, and neither was cosmetic
- `da7a76f` fix(dashboard): charts were plotting unix seconds on a millisecond axis
- `84c3954` fix(tools): the sweep's bar was two thousand trials short
- `070f72f` fix(desk): say why the model call failed, instead of showing four dashes
- `8814760` fix(universe): the other two caps, and a guard for each way I broke it
- `76286b9` fix: batch 1 and 2 of the 2026-11-10 audit
- `6560ac6` fix: batches 3 and 4 of the audit - the dashboard's quiet defects and the Python failure paths

### Docs
- `eb1ac73` docs(tree): make this clone the only tree, everywhere a rule is stated
- `e4f6bb7` docs(readme): make the front page describe the system that exists
- `b7f78ce` docs(contributing): same stale test count as the README
- `cee3ca3` docs(agents): the server is 1:30, and what that costs at 50 EUR
- `daa80ef` docs: the size-and-ruin arithmetic, and the three tools that produce it
- `335a238` docs: 80% win rate is one parameter away, and it loses
- `8e08e21` docs: scaliing is the same zero at a steeper price
- `afc0957` docs(audit): closure map — 30 of 30 findings verified, not assumed
- `a1a657c` docs: regenerate the architecture map for the system as it is now
- `65d5d91` docs: no machine path in AGENTS.md, the guard was right to refuse
- `e145841` docs: the charting alternatives people recommend, and why none of them is our data source
- `fef609a` docs: why the universe is one scan, not batches of fifty
- `06fe92c` docs: 248 symbols is what 'everything' measured today
