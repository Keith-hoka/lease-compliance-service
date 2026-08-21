"""Law card: the existing rent-increase rules run against a hypothetical increase."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.rent_suggest.anchor import dollars
from app.rules.base import Finding
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput, RentIncrease

RENT_RULE_MARKERS = ("rent_increase", "fixed_term_increase")
_PERIODS_PER_YEAR = {"weekly": Decimal(52), "fortnightly": Decimal(26), "monthly": Decimal(12)}


def from_weekly(weekly: Decimal, frequency: str) -> Decimal:
    """Weekly amount expressed in the lease's own payment frequency."""
    return dollars(weekly * Decimal(52) / _PERIODS_PER_YEAR[frequency])


@dataclass(frozen=True)
class LawCard:
    findings: list[Finding]
    blocked: bool


async def law_card(
    session: AsyncSession,
    jurisdiction: str,
    as_at: date,
    lease: LeaseInput,
    renewal_start: date,
    proposed_weekly: Decimal,
) -> LawCard:
    """Run the deterministic rent rules against a hypothetical increase.

    Findings whose rule_id ends "_notice" are dropped: notice is a future
    act (whether and when the landlord gives it) that the hypothetical
    increase cannot carry, so a notice rule is always vacuously green here
    - keeping it would render a misleading green row beside a rent nobody
    has actually given notice of.
    """
    proposed = RentIncrease(
        effective_on=renewal_start, new_amount=from_weekly(proposed_weekly, lease.rent_frequency)
    )
    hypothetical = lease.model_copy(
        update={"rent_increases": [*(lease.rent_increases or []), proposed]}
    )
    findings = await run_audit(session, jurisdiction, as_at, hypothetical)
    relevant = [
        f
        for f in findings
        if any(m in f.rule_id for m in RENT_RULE_MARKERS) and not f.rule_id.endswith("_notice")
    ]
    return LawCard(findings=relevant, blocked=any(f.verdict == "red" for f in relevant))
