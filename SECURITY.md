# Security Policy

This project places orders through a trading terminal. Security issues here are
not hypothetical.

## Reporting a vulnerability

Open a **private** security advisory via GitHub
(`Security` tab → `Report a vulnerability`), or email the maintainer listed on
the GitHub profile. Please do not open a public issue for anything that could
let an unauthorised party place an order.

Expect an acknowledgement within a few days. This is a personal project, not a
funded one — there is no bug bounty.

## What counts as a vulnerability here

- Bypassing the human `APPROVED` step, or the MCP bridge's consent gate
- Executing an order without a fresh, single-use approval token
- Escaping the `max_notional_per_trade` or lot-size limits
- Any path where a live account is reached while the pre-flight believes it is
  on demo
- An order being placed by a scheduled job rather than a human-initiated flow
- Credential or key material leaking into the repository or into logs

## Design constraints you should know before auditing

By design, **AutoTrading is off** and the agent has no code path that enables
it. The agent cannot synthesise the bridge's approval token: the mutating tools
return an approval preview that must be echoed back by the same process, with
idempotency keys and a JSONL audit trail.

Relevant files:

| File | Responsibility |
| --- | --- |
| `run_funnel.py` | pre-flight (demo-only), lock, run window |
| `policy_check.py` | runnable proof that the consent gate fires |
| `keep_awake.py` | power-state guard, no trading surface |
| `marketdata.py` | owns the terminal connection, the broker offset and the bar cache |

## Secrets

CI scans every tracked file for private runtime paths, machine-home paths and
recognized key material without printing matches. An installation-specific
identifier can be supplied through the private `ZTT_PRIVATE_IDENTIFIER` CI
secret; it must never be embedded in the detector's source. This is prevention,
not a claim that earlier published history is clean. The
consent-gate policy lives outside version control
(`config.toml` at the repository root) because it is per-installation.

Dependency versions are pinned in `requirements*.txt` and `dashboard/Cargo.lock`.
The supported Python environment is 3.12–3.14; Rust validation uses 1.96.0.
Review dependency advisories and vendor provenance with each dependency update
and at least monthly. A clean advisory scan covers known reports, not unknown
vulnerabilities, malicious dependencies or the separately installed bridge.

Before sharing diagnostics, inspect logs/reports for private trading information.
`status.py` redacts account/server identifiers and prints only typed numeric
policy controls. It does not dump private config fields or their paths.
See [incident and recovery procedure](docs/OPERATIONS.md) for evidence handling.

## Data that leaves this machine

The advisory desk is the only component that calls a third party. It sends the
scan digest, the candidates and the notes to DeepSeek (`desk.py`, `agent_brief.py`),
and — when `OPENROUTER_API_KEY` is set — two typed questions to the decision model
(`desk.jev`). What goes out is artifact text: symbols, statistics, prices and the
desk's own sentences. No account number, no key, no local path: `desk_store`
redacts the login and rewrites machine paths before anything is written, and
`tools/check_public.py` fails the build if one appears in a tracked file.

Nothing else in this tree makes an outbound connection. RSS headlines are fetched
by the desk only, with DTD-bearing documents refused.

## Not in scope

- The third-party MCP bridge itself — report upstream
- Losses resulting from trading decisions; see `DISCLAIMER.md`
- Broker-side behaviour, execution quality or slippage
- `policy_check.py`, the runnable proof that the consent gate is armed: it imports
  `mt5_mcp`, which is not in `requirements.txt` because it is the bridge's own
  package. It therefore cannot run in CI and is a manual check. That is a known
  gap, not an oversight: the alternative is vendoring the bridge

## Supported versions

| Version | Supported |
| --- | --- |
| latest commit on `main` | yes — actively maintained |
| tagged releases | none exist yet |

Acknowledgement target: 7 days. Fixes are prioritised by the P1/P2 definitions in
`docs/audit-2026-11-09/README.md`. The private vulnerability reporting channel
above only works while it stays enabled in the repository settings, which cannot
be verified from the tree.

CODEOWNERS names the guardrail files, but it only bites when branch protection
requires a review — also a repository setting, not a fact this file can assert.
