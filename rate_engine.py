from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

D = Decimal


@dataclass(frozen=True)
class MarketTrove:
    debt_bold: Decimal
    rate_pct: Decimal
    last_rate_adj_time: int


def annual_interest(debt_bold: Decimal, rate_pct: Decimal) -> Decimal:
    return debt_bold * rate_pct / D("100")


def debt_ahead(market: Iterable[MarketTrove], user_rate_pct: Decimal, user_last_adj_time: int) -> Decimal:
    """Estimate branch debt redeemed before the user.

    Liquity V2 redeems lower rates first. For equal rates, the most recently set
    rate is redeemed first, so equal-rate Troves with a later adjustment time are
    ahead of this Trove. This is branch-local ordering only; cross-branch routing
    still depends on outside debt.
    """
    total = D("0")
    for t in market:
        if t.debt_bold <= 0:
            continue
        if t.rate_pct < user_rate_pct or (
            t.rate_pct == user_rate_pct and t.last_rate_adj_time > user_last_adj_time
        ):
            total += t.debt_bold
    return total


def _weighted_rate_for_debt_ahead(market: list[MarketTrove], target_share: Decimal) -> Decimal | None:
    rows = [t for t in market if t.debt_bold > 0]
    if not rows:
        return None
    rows.sort(key=lambda t: (t.rate_pct, -t.last_rate_adj_time))
    total = sum((t.debt_bold for t in rows), D("0"))
    target = total * target_share
    running = D("0")
    for t in rows:
        running += t.debt_bold
        if running >= target:
            return t.rate_pct
    return rows[-1].rate_pct


def analyze_rate(
    *,
    debt_bold: Decimal,
    current_rate_pct: Decimal,
    last_rate_adj_time: int,
    market: list[MarketTrove],
) -> dict:
    branch_debt = sum((t.debt_bold for t in market if t.debt_bold > 0), D("0"))
    ahead = debt_ahead(market, current_rate_pct, last_rate_adj_time)
    ahead_pct = (ahead / branch_debt * D("100")) if branch_debt > 0 else None

    # Product heuristics, not Liquity protocol recommendations. These target
    # progressively larger branch-local debt buffers ahead of the borrower.
    targets = {
        "aggressive": D("0.10"),
        "balanced": D("0.25"),
        "defensive": D("0.50"),
    }
    options = {}
    current_annual = annual_interest(debt_bold, current_rate_pct)
    for name, share in targets.items():
        rate = _weighted_rate_for_debt_ahead(market, share)
        if rate is None:
            options[name] = None
            continue
        annual = annual_interest(debt_bold, rate)
        options[name] = {
            "rate_pct": float(rate),
            "target_branch_debt_ahead_pct": float(share * D("100")),
            "annual_interest_bold": float(annual),
            "annual_change_vs_current_bold": float(annual - current_annual),
            "annual_saving_vs_current_bold": float(current_annual - annual),
        }

    balanced = options.get("balanced")
    return {
        "current": {
            "rate_pct": float(current_rate_pct),
            "annual_interest_bold": float(current_annual),
            "monthly_interest_equivalent_bold": float(current_annual / D("12")),
        },
        "branch_market": {
            "total_debt_bold": float(branch_debt),
            "estimated_debt_ahead_bold": float(ahead),
            "estimated_debt_ahead_pct": float(ahead_pct) if ahead_pct is not None else None,
            "troves_sampled": len(market),
        },
        "options": options,
        "suggestion": {
            "mode": "balanced",
            "rate_pct": balanced["rate_pct"] if balanced else None,
            "annual_saving_vs_current_bold": balanced["annual_saving_vs_current_bold"] if balanced else None,
        },
        "methodology": (
            "Guardian heuristic: aggressive/balanced/defensive target roughly 10%/25%/50% "
            "of branch debt ahead in Liquity's rate ordering. This is not an official Liquity recommendation."
        ),
    }
