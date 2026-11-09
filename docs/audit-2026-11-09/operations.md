# Operations, security, packaging, and documentation review

Review date: 2026-11-09. This is a source inspection. No scheduler, registry,
terminal, credentials, production policy, or runtime ledger was changed. Findings
describe the checked-out files; they do not establish what is already public in
Git history or which repository protection settings are enabled.

## Confirmed findings

### OPS-01 — P1: the privacy guard contains the account identifier it protects

**Evidence:** `.github/workflows/ci.yml:53` excludes the workflow itself from the
content scan; line 55 embeds the broker account identifier as a fixed search
pattern. The file is tracked. The identifier is intentionally not reproduced here.

**Rationale and impact:** a CI rule that rejects a private identifier elsewhere
still ships that identifier in its own source. This contradicts the privacy
contract in `SECURITY.md:44` and `CONTRIBUTING.md:61`. Excluding the whole workflow
also creates a scanner blind spot.

**Recommendation:** remove the identifier from tracked configuration and use a
private CI variable for installation-specific checks. Scan the workflow too,
with narrow exemptions for harmless detector patterns. Use a maintained secret
scanner alongside explicit runtime-artifact rules. Its output should identify
the file and rule without printing sensitive matched values. Establish whether
the identifier exists in published history before deciding any separate history
cleanup; this review did not rewrite history or publish changes.

**Verification:** fixtures containing synthetic account-like identifiers and
credentials must fail; the workflow itself must be scanned. Confirm sanitized
output and scan failures both produce nonzero status. The current `grep` in an
`if` also treats a scanner error as the clean branch; distinguish no-match from
an execution error.

### OPS-02 — P1: portable ignore rules lag the runtime artifact names

**Evidence:** `.gitignore:10` ignores only `engine_state.json`, whereas
`position_manager.py:282` creates a temporary generation file and line 287 writes
`engine_state.<timestamp>.json`. `idempotency.db*` is protected by a local
`.git/info/exclude` entry, not by tracked `.gitignore`. The same local-only
approach protects installation launchers. `.env` is ignored exactly, while
additional `.env` variants have no tracked exclusion.

**Rationale and impact:** a fresh clone does not inherit `.git/info/exclude`.
Generated account snapshots, consent/idempotency records, temporary state, or
locally pinned launchers can therefore become ordinary addable files. The checked
tracked inventory contained no generation snapshots or idempotency database;
this is a prevention gap, not evidence that those files have been committed.

**Recommendation:** track explicit ignore patterns for generation files and
their temporary siblings, idempotency database sidecars, private launcher names,
and local environment variants, retaining an exception for `.env.example`.
Replace the broad `*.lock` rule with runtime-specific lock rules so the dashboard
dependency lockfile can be versioned.

**Verification:** a temporary clean repository using only tracked ignore rules
should reject a table of synthetic sensitive artifact names while allowing
`.env.example` and `dashboard/Cargo.lock`. Add a CI assertion that these runtime
classes never appear in `git ls-files`.

### OPS-03 — P2: builds and the scheduled interpreter are not reproducible

**Evidence:** `requirements.txt:1`–3 contains lower bounds only; CI installs
unbounded current `numpy`/`pandas` directly at `.github/workflows/ci.yml:27` and
installs unpinned Ruff at line 69. `dashboard/Cargo.toml` uses normal compatible
version ranges but has no tracked `Cargo.lock`, because `.gitignore:4` ignores
every `*.lock`. The Rust toolchain follows `stable` at CI line 77. Both scheduled
launchers invoke whichever `python` is on PATH (`run_funnel.cmd:7`,
`run_rebuild.cmd:4`).

**Rationale and impact:** the same source revision can execute different numeric,
terminal, or web dependencies after a reinstall. Scheduler and interactive shells
can resolve different interpreters. This makes an incident or a measured result
harder to reproduce.

**Recommendation:** keep an approved dependency lock/constraints set for the
execution environment and commit the binary application's Cargo lockfile. Record
Python, MT5 package, numerical dependency, Rust, and code versions with research
provenance. Pin scheduled launchers to a local virtual environment without
publishing machine paths. Test dependency updates separately before promotion.
Add dependency vulnerability checks and explicit minimal CI token permissions;
pin third-party actions to reviewed immutable revisions.

The [Cargo Book](https://doc.rust-lang.org/cargo/guide/cargo-toml-vs-cargo-lock.html)
explains that Cargo.lock records exact dependency resolution; the
[Cargo FAQ](https://doc.rust-lang.org/cargo/faq.html#why-have-cargolock-in-version-control)
describes the reproducibility benefit and caveats. A successful build against
an ignored local lockfile does not provide that guarantee for a fresh clone.

**Verification:** clean builds from the same revision resolve the same versions;
CI uses the declared dependency set and `cargo --locked`. A launcher diagnostic
reports a sanitized interpreter/version identity. Root audit validation owns the
actual test and dependency-check results.

### OPS-04 — P2: the documented publication build is local and stale

**Evidence:** `AGENTS.md:87` names `build_public.py` as the publication mechanism.
It exists locally but is excluded from Git and absent from the tracked source
inventory. Static inspection shows an explicit source list missing modules now
imported by the engine, dashboard/test content, and newer documentation. It uses
private helper inputs and reports missing copied sources without necessarily
rejecting the build. No publication build was executed.

**Rationale and impact:** a fresh clone cannot reproduce the stated build.
The existing local builder can produce an incomplete release, even if the
sanitization check passes. A privacy check alone does not prove package integrity.

**Recommendation:** make the supported publication process reproducible from
tracked sanitized inputs, or clearly retire the obsolete builder and document
the actual release process. Reject missing required source files. Test imports,
tests, asset availability, and forbidden-file exclusions inside the produced
artifact. Keep runtime state out of that artifact.

**Verification:** build in a temporary directory from a clean source checkout;
validate artifact completeness and privacy without using private helper files or
production state. Never use this check to mirror runtime data.

### OPS-05 — P2: initial configuration instructions do not produce a runnable scan

**Evidence:** README quick start at `README.md:319`–327 installs dependencies and
runs a scan without exporting the required account pin. `.env.example:1` says to
copy and fill `.env`; however, `run_funnel.py:46` obtains `MT5_EXPECTED_LOGIN`
only from the process environment. Its preflight correctly refuses an absent
pin at line 64. The example also lists a server/window setting that the scanner
does not read as configuration.

**Recommendation:** document which variables each component actually consumes
and how a human sets the required numeric demo account pin in the launcher
environment. Keep execution consent separate and unset by default. Provide a
sanitized policy schema/example or an exact external bridge setup reference.
Do not solve onboarding by defaulting the account or bypassing preflight.

**Verification:** follow the documented setup with a stubbed terminal in a clean
shell; missing settings must produce a clear refusal and the documented valid
settings must pass the configuration stage.

### OPS-06 — P2: diagnostics print the full policy value set

**Evidence:** `status.py:67`–70 prints every non-comment/non-section policy line.
The private policy contents were not read for this audit.

**Impact:** a diagnostic transcript can disclose identifiers, installation paths,
or future sensitive policy fields when shared for support. This is a demonstrated
unredacted code path; which sensitive values it currently emits is unverified.

**Recommendation and verification:** print an allowlist of operationally useful
fields, redact sensitive values by default, and test with a synthetic policy
containing a dummy credential/account/path. The dummy values must never appear
in the captured diagnostic output.

## Broader improvements

| Area | Existing strength | Recommended next step |
| --- | --- | --- |
| Governance | `CODEOWNERS` covers orchestration, gates, supervisor, and tests | Add market data, trial-producing tier 2, and stop persistence to the protected review map; verify mandatory owner approval in repository settings. Local files cannot prove enforcement. |
| Platforms | Linux Python 3.11–3.13 CI and Rust build/test/Clippy | Add isolated Windows CI for actual path, process, launcher, and file-sharing semantics; keep MT5 stubbed and production state inaccessible. |
| Recovery | Append-only trial history and an HMAC chain | Document encrypted local backup, key recovery, and restore drills. Preserve all losses/trials and never lower history after restore; a stale backup must not silently reduce the significance bar. |
| Deployment | One documented execution tree and explicit demo guard | Document maintenance/promotion/rollback in that same tree. Scheduler reads working files, including uncommitted changes; a commit is not the deployment boundary. Add a fail-closed approved-version check before unattended research if editing the active tree. |
| Observability | Explicit failures, stale indicators, audit rows, and diagnostic script | Define non-trading alerts for repeated failed scans, stale artifacts, audit-write failure, and zero tested symbols. Document who responds and how to establish recovery. |
| Documentation | Strong invariant, ownership map, lineage, and measurement warnings | Mark old research sections as historical. Reconcile the README's entry-path claims with its manual order-test section, AGENTS' state-file/API descriptions with current code, `CONTRIBUTING.md:12` threshold wording with the hard prohibition, and dated trial totals/test counts. |
| Security response | Private advisory reporting and defined vulnerability classes | Document supported dependency/runtime versions, a dependency-update cadence, and a sanitized incident evidence procedure. Verify external bridge deployment separately. |

Skipped: production policy, live terminal/scheduler/registry checks, published Git history, repository protection settings, destructive publication execution, and recovery drills.
Residual risk: passing local tests does not establish the installed bridge's consent guarantees or prove a restored ledger contains every historical trial.
