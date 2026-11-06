# Changelog

Dated, newest first. One line per change a reader could verify.

## 2026-11-06

- **Empty shortlist is a stand-down, not a failure.** A scan that triggered
  nothing exited `rc=1`, so the orchestrator logged `FAILED` and the 100
  hypotheses that scan had tested never reached the ledger — under-counting
  trials weakens the Bonferroni bar in the permissive direction.
- **Per-scan HTML report** (`report.py`): every candidate, both gates, the
  corrected bar beside the naive one, and the code SHA. Written by tier 3 next
  to the JSON, served by the dashboard at `/api/report/latest`.
- **Dashboard shows local time.** Logs and the ledger stay UTC (the HMAC chain
  covers their exact bytes); the browser renders the viewer's own zone and names it
  in the header.
- **Lineage**: `docs/LINEAGE.md` plus dated banners in the two predecessor
  repositories, both now public.

## 2026-11-05

- Engine state published as a new generation (`engine_state.<ns>.json`) instead of
  replacing one file, which made `WinError 5` structurally impossible.
- One MT5 session per process instead of connect/disconnect every 5 s
  (17,280 cycles/day), which removed the `-10005 IPC timeout`s.
- `--apply` requires `ZTT_ALLOW_APPLY=1` in the launching shell.
- Ledger rows HMAC-chained; `status.py` reports `chain: ok` or `TAMPERED`.

## 2026-11-04

- Two independent gates, both required: Bonferroni-corrected z over the
  cumulative trial count, and a permutation null that trades the identical rule.
- Session strategy matrix (5,000 tests/day) replacing the full 12-hour matrix.
- Backtest and live share `next_stop()`; spread, EMA gate and session close
  threaded through both. `tests/test_parity.py` fails on any divergence.

## 2026-11-03

- CI gates: tracked-artifact check, `grep -F` PII check, and `ruff --select F821`.
  The last one exists because an undefined name parsed fine and failed twelve
  consecutive production runs while CI stayed green.
