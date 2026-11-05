"""Prove the MT5 bridge policy is armed in the units the engine really uses.

Run:  python policy_check.py
Exit 0 = every control behaves as intended. Exit 1 = a control is broken.

This exercises the bridge's OWN code (load_config, check_preflight_limits) and
replays the exact requires_approval expression from tools/orders.py, so it
cannot drift from the shipped behaviour the way a prose description can.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from mt5_mcp.config import load_config
from mt5_mcp.policy.preflight import PreflightInputs, check_preflight_limits
from mt5_mcp.types import OrderRequest


def requires_approval(cfg, volume: Decimal, price: Decimal) -> bool:
    """Verbatim replay of tools/orders.py: notional = volume * ref_price."""
    notional = volume * price
    return bool(
        cfg.policy.auto_approve_notional > 0
        and notional >= cfg.policy.auto_approve_notional
    )


def main() -> int:
    cfg = load_config()
    ask = Decimal("1.12236")  # live EURUSD ask at setup time
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str) -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
        if not ok:
            failures.append(label)

    print(f"config: auto_approve_notional={cfg.policy.auto_approve_notional} "
          f"max_notional_per_trade={cfg.policy.max_notional_per_trade} "
          f"max_orders_per_minute={cfg.policy.max_orders_per_minute} "
          f"allowlist={cfg.symbols.allowlist}")

    # 1. The gate must arm for the authorised 0.01-lot size.
    check(
        "consent gate fires on 0.01 lot",
        requires_approval(cfg, Decimal("0.01"), ask),
        f"notional={Decimal('0.01') * ask} >= {cfg.policy.auto_approve_notional}",
    )

    # 2. A 0.01-lot EURUSD entry passes preflight (allowed, pending approval).
    r1 = check_preflight_limits(
        "place_order",
        OrderRequest(symbol="EURUSD", side="buy", type="market", volume="0.01"),
        PreflightInputs(notional=Decimal("0.01") * ask),
        cfg,
    )
    check("0.01 lot EURUSD passes preflight", r1 is None, f"refusal={r1}")

    # 3. Doubling the size must be refused by the cap, not merely advised.
    r2 = check_preflight_limits(
        "place_order",
        OrderRequest(symbol="EURUSD", side="buy", type="market", volume="0.02"),
        PreflightInputs(notional=Decimal("0.02") * ask),
        cfg,
    )
    check(
        "0.02 lot EURUSD refused by max_notional_per_trade",
        r2 is not None and r2.code == "EXCEEDS_LOCAL_LIMIT",
        f"code={getattr(r2, 'code', None)}",
    )

    # 4. The symbol allowlist must refuse an out-of-scope instrument.
    r3 = check_preflight_limits(
        "place_order",
        OrderRequest(symbol="GBPUSD", side="buy", type="market", volume="0.01"),
        PreflightInputs(notional=Decimal("0.01") * Decimal("1.27")),
        cfg,
    )
    check(
        "GBPUSD refused by symbol allowlist",
        r3 is not None and r3.code == "EXCEEDS_LOCAL_LIMIT",
        f"code={getattr(r3, 'code', None)}",
    )

    # 5. An exit must NEVER be blocked by the entry-side caps.
    r4 = check_preflight_limits(
        "close_position",
        OrderRequest(symbol="EURUSD", side="sell", type="market", volume="0.01"),
        PreflightInputs(
            notional=Decimal("100"),
            estimated_realised_loss_on_close=Decimal("-50"),
        ),
        cfg,
    )
    check("close_position never blocked by caps", r4 is None, f"refusal={r4}")

    print()
    if failures:
        print(f"BROKEN: {', '.join(failures)}")
        return 1
    print("OK — gate armed, 0.01-lot ceiling enforced, exits always permitted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
