from decimal import Decimal
from guardian_core import TrovePosition, classify_health

def p(cr, mcr=110):
    return TrovePosition(
        owner="0x0000000000000000000000000000000000000001",
        branch="WETH", trove_id=1, status="active",
        collateral=Decimal("1"), debt_bold=Decimal("1000"),
        annual_interest_rate_pct=Decimal("5"),
        collateral_price_usd=Decimal("2000"),
        collateral_ratio_pct=Decimal(str(cr)), mcr_pct=Decimal(str(mcr)),
        liquidation_price_usd=Decimal("1100"),
        distance_to_liquidation_pct=Decimal("45"),
        batch_manager=None, price_is_fallback=False, block_number=1,
    )

assert classify_health(p(109)) == "LIQUIDATABLE"
assert classify_health(p(115)) == "RISKY"
assert classify_health(p(130)) == "SICKLY"
assert classify_health(p(150)) == "MILDLY HEALTHY"
assert classify_health(p(170)) == "HEALTHY"
print("offline health tests passed")
