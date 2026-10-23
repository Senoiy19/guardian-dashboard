from __future__ import annotations

import argparse
import json
import logging
from decimal import Decimal

from guardian_core import GuardianError, LiquityReader, classify_health


def money(x: Decimal | None) -> str:
    return "n/a" if x is None else f"${x:,.2f}"


def pct(x: Decimal | None) -> str:
    return "n/a" if x is None else f"{x:,.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Liquity V2 Trove inspector")
    parser.add_argument("wallet", help="Ethereum wallet address")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    try:
        positions = LiquityReader().inspect_wallet(args.wallet)
    except GuardianError as exc:
        print(f"ERROR: {exc}")
        return 1

    if args.json:
        print(json.dumps([p.to_dict() | {"health": classify_health(p)} for p in positions], indent=2))
        return 0

    if not positions:
        print("No active or zombie Liquity V2 Troves found for this wallet.")
        return 0

    print(f"Found {len(positions)} Liquity V2 Trove(s)\n")
    for n, p in enumerate(positions, 1):
        print(f"{n}. {p.branch} | {classify_health(p)}")
        print(f"   Trove ID: {p.trove_id}")
        print(f"   Status: {p.status}")
        print(f"   Collateral: {p.collateral:,.6f} {p.branch}")
        print(f"   Debt: {p.debt_bold:,.2f} BOLD")
        print(f"   Interest rate: {p.annual_interest_rate_pct:,.2f}%")
        print(f"   Liquity collateral price: {money(p.collateral_price_usd)}")
        print(f"   Collateral ratio: {pct(p.collateral_ratio_pct)}")
        print(f"   Branch MCR: {pct(p.mcr_pct)}")
        print(f"   Estimated liquidation price: {money(p.liquidation_price_usd)}")
        print(f"   Price buffer to liquidation: {pct(p.distance_to_liquidation_pct)}")
        if p.batch_manager:
            print(f"   Interest batch manager: {p.batch_manager}")
        if p.price_is_fallback:
            print("   PRICE WARNING: current oracle fetch failed/flagged; using Liquity fallback price.")
        print(f"   Ethereum block: {p.block_number}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
