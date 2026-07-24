from datetime import date
from decimal import Decimal

from app.rules.base import CheckResult, Rule, SectionRef, to_weekly_rent
from app.schemas.lease import LeaseInput

ACT = "act-2010-042"


def _bond_check(lease: LeaseInput) -> CheckResult:
    """s159(1): a person "must not require or receive from a tenant or another person a
    rental bond of an amount exceeding 4 weeks rent under the residential tenancy
    agreement for which the bond was paid (as in force when the agreement was entered
    into)" (corpus text as at 2026-07-24).
    """
    weekly = to_weekly_rent(lease.rent_amount, lease.rent_frequency)
    max_bond = (weekly * 4).quantize(Decimal("0.01"))
    evidence = {
        "fields": {"bond_amount": str(lease.bond_amount)},
        "computed": {"weekly_rent": str(weekly), "max_bond": str(max_bond)},
    }
    if lease.bond_amount > max_bond:
        return (
            "red",
            f"Bond of {lease.bond_amount} exceeds the 4-week maximum of {max_bond}.",
            evidence,
        )
    return (
        "green",
        f"Bond of {lease.bond_amount} is within the 4-week maximum of {max_bond}.",
        evidence,
    )


def _advance_check(lease: LeaseInput) -> CheckResult:
    """s33(2): a landlord or agent "must not require a tenant to pay more than 2 weeks
    rent in advance under a residential tenancy agreement or to pay rent for a period
    of the tenancy before the end of the previous period for which rent has been paid"
    (corpus text as at 2026-07-24).
    """
    weekly = to_weekly_rent(lease.rent_amount, lease.rent_frequency)
    cap = (weekly * 2).quantize(Decimal("0.01"))
    evidence = {
        "fields": {"rent_in_advance_amount": str(lease.rent_in_advance_amount)},
        "computed": {"weekly_rent": str(weekly), "max_advance": str(cap)},
    }
    if lease.rent_in_advance_amount > cap:
        return (
            "red",
            f"Rent in advance of {lease.rent_in_advance_amount} exceeds the cap of {cap}.",
            evidence,
        )
    return (
        "green",
        f"Rent in advance of {lease.rent_in_advance_amount} is within the cap of {cap}.",
        evidence,
    )


NSW_RULES = [
    Rule(
        rule_id="nsw.bond_max_4_weeks",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "159")],
        applies_from=date(2011, 1, 31),
        applies_to=None,
        required_inputs=["bond_amount"],
        check=_bond_check,
    ),
    Rule(
        rule_id="nsw.rent_in_advance_max",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "33")],
        applies_from=date(2011, 1, 31),
        applies_to=None,
        required_inputs=["rent_in_advance_amount"],
        check=_advance_check,
    ),
]
