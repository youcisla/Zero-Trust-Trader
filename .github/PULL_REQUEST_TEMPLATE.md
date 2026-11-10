## What changed

## The three questions (CONTRIBUTING.md)

1. Does this lower a gate, or make a candidate pass that failed?
2. Does it add hypotheses? Are they counted in `total_tests`?
3. Is there a test for the case that would have caught this?

## Checklist

- [ ] The bar was not lowered; no threshold, permutation count or `LEGACY_TRIALS` changed
- [ ] Trials are counted (`total_tests`), or the change adds none
- [ ] A degenerate-case test exists for new indicator/maths code
- [ ] `python tools/check_docs.py` is green (README + architecture map + mémoire)
- [ ] No runtime state in the diff (`engine_state.json`, `*.log`, `desk_history.sqlite`, `liquid_universe.json`)
- [ ] Commit style: Conventional Commits, lowercase scope (e.g. `fix(tier3): ...`)
