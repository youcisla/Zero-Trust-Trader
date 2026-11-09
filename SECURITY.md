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
| `marketdata.py` | the only module that talks to the terminal |

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

## Not in scope

- The third-party MCP bridge itself — report upstream
- Losses resulting from trading decisions; see `DISCLAIMER.md`
- Broker-side behaviour, execution quality or slippage
