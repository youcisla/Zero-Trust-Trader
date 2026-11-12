Read `AGENTS.md` in this repository. It is the authority: the safety invariants,
the eight fail-closed gates, and the hard rules for agents all live there, and
they are not repeated here because a second copy is the copy that drifts.

The parent workspace's `AGENTS.md` still applies where it does not conflict:
evidence over self-report, no gate weakening, no secrets in a tracked file.

Quick commands:

```bash
python -m unittest discover -s tests      # 343 tests, no terminal needed
python tools/check_docs.py                # docs agree with the tree
python tools/check_public.py              # nothing private is tracked
cd dashboard && cargo test --release      # 43 Rust tests
```
