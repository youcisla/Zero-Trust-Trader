# Operations, promotion and recovery

This repository is also the execution directory. Task Scheduler reads working
files, including uncommitted edits; a commit is not an activation boundary.
Keep the terminal on demo and AutoTrading off. No step here grants entry consent
or changes the supervisor's dry-run default.

## Supported environment and setup

The approved runtime versions are in `requirements.txt`; use Python 3.12–3.14.
The checked-in NumPy version requires Python 3.12+. Create a local `.venv` and
install through that interpreter. The portable Windows launchers call
`.venv\Scripts\python.exe` and fail when it is absent. Private `.local.cmd`
launchers were not modified: their human owner must check interpreter selection
and account-pin propagation before using them with a registered task.

The dashboard writes `dashboard_heartbeat.json` every five seconds and one row per
scan POST to `dashboard_scan.jsonl`; `python status.py` reports the first and the
last is the record of who started what. It is registered as `HermesMT5Dashboard`
with restart-on-failure, so killing it returns within a minute -- the matrix
below is what it says when it answers.

The scanner reads `MT5_EXPECTED_LOGIN` at call time: an exported value wins, and a
`.env` beside the tree is the fallback. The launchers pin it; the dashboard button
and the scheduler inherit it.
Its value must be the numeric demo login selected by a human. The actual terminal
must also satisfy the server-name and explicit demo-mode checks. Missing settings
must stay a refusal. `ZTT_LEDGER_KEY` and `DEEPSEEK_API_KEY` are loaded by their
own components; keep them private, and do not arm apply mode in a scheduled
launcher. The bridge policy schema belongs to the installed `mt5_mcp` version;
use that version's schema and `policy_check.py`, rather than adding unrelated
sections to its `config.toml`.

The Rust dashboard uses the checked-in Cargo.lock and `rust-toolchain.toml`.
Build with `cargo build --release --locked --manifest-path dashboard/Cargo.toml`.
Do not start the raw binary with an arbitrary working directory; use its launcher
or supply a deliberate fixture root for tests.

## Change and promotion procedure

1. In the execution directory, create the empty `.ztt-maintenance` marker before
   editing production code. New research/supervisor invocations refuse while it
   exists. This marker does not stop a process already running; establish a safe
   idle period before editing and do not interrupt active risk supervision.
2. Keep every historical trade/trial and private state file in place. Implement
   small changes and validate using `python tools/run_tests.py`, repository Ruff
   rules, privacy checks and locked dashboard tests/Clippy. The test runner
   installs the terminal stub and blocks non-loopback socket connections.
3. Review the complete diff, verify the required owner reviews in repository
   settings, and record tested code/dependency versions. Local CODEOWNERS entries
   do not prove that remote approval rules are enabled.
4. Validate a clean tracked-source publication build with
   `python tools/build_public.py --check`. New files must be tracked first.
   Build a distributable into a new empty destination with `--out`; the builder
   never deletes an existing artifact or mirrors runtime data. The older ignored
   root `build_public.py` is retired and must not be used.
5. Remove the maintenance marker only after the coherent change passes checks
   and its human owner has reviewed operational readiness. Do not modify the
   scheduler, private launchers, AutoTrading or apply settings as a side effect.
   A code rollback is a code change: repeat this procedure without rolling back
   accumulated trial counts or the ledger.

## Failure response

The primary view is the local read-only dashboard. Check both transport heartbeat
and producer age; a connected WebSocket alone does not prove fresh observations.
Unknown positions, missing/mismatched scan artifacts, failed scans, unavailable
costs/correlations and zero tested symbols are failures or unknown states rather
than evidence of a flat account or absence of opportunity.

Respond to repeated failed scans, stale producer/transport state, audit-write
failure, ledger verification failure or unresolved partial closes by preserving
evidence and refusing new research/proposals until the cause is understood.
Existing risk supervision requires human attention; do not automatically stop it
or retry orders to silence an alert. Nothing in this procedure schedules a
mutating action or an external notification.

`status.py` redacts account/server identity and policy locations and prints only
allowlisted numeric controls. Logs, exported ranges, reports and screenshots may
still contain private trading information. Review them locally before sharing;
never paste raw policy, credentials or runtime ledgers into an issue.

## Recovery discipline

Preserve original append-only ledgers and integrity keys. Do not mirror or copy
runtime state into source trees, publication artifacts or development fixtures.
Any separately authorized backup must have an explicit private/encrypted custody
procedure; no backup or key-copy operation is performed by this project change.

Rehearse restore logic with synthetic ledgers and test keys in a temporary
directory. Verify row order/signatures, cumulative trial count and the highest
known count before considering a recovery complete. A valid signature chain
does not prove an old backup contains later trials: compare independent retained
run/count evidence. If completeness cannot be established, stand down; do not
lower `LEGACY_TRIALS`, truncate losses, accept a smaller count or invent rows.
The production restore and broker reconciliation require separate human review.

## Dependency and incident review

Review dependency updates separately with the same synthetic tests; do not
upgrade the live environment during a scan. Run the pinned Python advisory tool,
review Cargo advisories and vendor provenance, and record the results and limits.
Known-advisory scans do not establish general safety or cover the external bridge
and terminal application.

The privacy scanner checks every tracked file, including its own workflow, and
prints filenames/rule names rather than matching values. An optional private
`ZTT_PRIVATE_IDENTIFIER` CI secret detects installation-specific identifiers
without embedding them in source. Do not put a real identifier into the detector.
An identifier was present in an earlier tracked workflow; its removal from the
working tree does not prove published history is clean. Investigate publication
history privately before any separately authorized cleanup. No history rewrite
or remote repository setting was performed here.
