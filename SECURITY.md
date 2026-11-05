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

No credentials are stored in this repository and CI fails the build if a
machine path, broker account number, or key prefix appears in a commit. The
consent-gate policy lives outside version control
(`<execution-dir>\config.toml`) because it is per-installation.

## Not in scope

- The third-party MCP bridge itself — report upstream
- Losses resulting from trading decisions; see `DISCLAIMER.md`
- Broker-side behaviour, execution quality or slippage
