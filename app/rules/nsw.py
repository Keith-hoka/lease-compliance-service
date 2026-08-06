from datetime import date
from decimal import Decimal
from itertools import pairwise

from app.rules.base import CheckResult, Rule, SectionRef, add_months, to_weekly_rent
from app.schemas.lease import LeaseInput

ACT = "act-2010-042"

COMMENCED = date(2011, 1, 31)
FREQ_COMMENCED = date(2020, 3, 23)
FIRST_YEAR_COMMENCED = date(2024, 10, 31)
S42_REPEALED = date(2024, 12, 13)


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


def _holding_fee_check(lease: LeaseInput) -> CheckResult:
    """s24(1)(b): a holding fee must not be required or received unless "the fee does
    not exceed 1 week's rent of the residential premises (based on the rent under the
    proposed residential tenancy agreement)" (corpus text as at 2026-07-24).
    """
    weekly = to_weekly_rent(lease.rent_amount, lease.rent_frequency)
    evidence = {
        "fields": {"holding_deposit_amount": str(lease.holding_deposit_amount)},
        "computed": {"weekly_rent": str(weekly), "max_holding_fee": str(weekly)},
    }
    if lease.holding_deposit_amount > weekly:
        return (
            "red",
            f"Holding fee of {lease.holding_deposit_amount} exceeds 1 week's rent of {weekly}.",
            evidence,
        )
    return (
        "green",
        f"Holding fee of {lease.holding_deposit_amount} is within 1 week's rent of {weekly}.",
        evidence,
    )


def _sorted_increases(lease: LeaseInput):
    return sorted(lease.rent_increases, key=lambda i: i.effective_on)


def _frequency_check(lease: LeaseInput) -> CheckResult:
    """s41(1A)(b): rent "may not be increased more than once in any period of 12
    months" (corpus text as at 2026-07-24; limit commenced 2020-03-23). Twelve
    months is reckoned in calendar months under the corresponding-date rule.
    """
    increases = _sorted_increases(lease)
    pairs = list(pairwise(increases))
    gaps = [(later.effective_on - earlier.effective_on).days for earlier, later in pairs]
    evidence = {
        "fields": {"rent_increases": [i.effective_on.isoformat() for i in increases]},
        "computed": {"gaps_days": gaps},
    }
    short = [
        (later.effective_on - earlier.effective_on).days
        for earlier, later in pairs
        if later.effective_on < add_months(earlier.effective_on, 12)
    ]
    if short:
        return (
            "red",
            f"Rent increases less than 12 months apart (shortest gap {min(short)} days).",
            evidence,
        )
    return "green", "All rent increases are at least 12 months apart.", evidence


def _first_year_check(lease: LeaseInput) -> CheckResult:
    """s41(1A)(a): rent "may not be increased within 12 months after the start of the
    tenancy" (corpus text as at 2026-07-24; limit commenced 2024-10-31). Twelve
    months is reckoned in calendar months under the corresponding-date rule.
    """
    first = _sorted_increases(lease)[0] if lease.rent_increases else None
    days = (first.effective_on - lease.start_date).days if first else None
    evidence = {
        "fields": {
            "start_date": lease.start_date.isoformat(),
            "first_increase": first.effective_on.isoformat() if first else None,
        },
        "computed": {"days_after_start": days},
    }
    if first is not None and first.effective_on < add_months(lease.start_date, 12):
        return (
            "red",
            f"First rent increase {days} days after the tenancy start, before 12 months.",
            evidence,
        )
    return "green", "No rent increase within 12 months of the tenancy start.", evidence


def _notice_check(lease: LeaseInput) -> CheckResult:
    """s41(1)(b): a rent increase notice must be "given at least 60 days before the
    increased rent is payable" (corpus text as at 2026-07-24).
    """
    noticed = [i for i in lease.rent_increases if i.notice_given_on is not None]
    days = {i.effective_on.isoformat(): (i.effective_on - i.notice_given_on).days for i in noticed}
    evidence = {"fields": {"notice_days": days}, "computed": {"minimum_days": 60}}
    short = {d: n for d, n in days.items() if n < 60}
    if short:
        return (
            "red",
            f"Rent increases with less than 60 days notice: {', '.join(sorted(short))}.",
            evidence,
        )
    return "green", "All noticed rent increases give at least 60 days notice.", evidence


def _disclosure_check(lease: LeaseInput) -> CheckResult:
    """s42(1) (repealed 2024-12-13): rent under "a fixed term agreement for a fixed
    term of less than 2 years must not be increased during the fixed term unless the
    agreement specifies the increased rent or the method of calculating the increase"
    (corpus text as at 2024-06-01). Twenty-four months is reckoned in calendar
    months under the corresponding-date rule.
    """
    term_days = (lease.end_date - lease.start_date).days
    in_term = [i for i in _sorted_increases(lease) if i.effective_on < lease.end_date]
    under_2_years = lease.end_date < add_months(lease.start_date, 24)
    evidence = {
        "fields": {
            "term_days": term_days,
            "increases_in_term": [i.effective_on.isoformat() for i in in_term],
            "fixed_term_increase_in_agreement": lease.fixed_term_increase_in_agreement,
        },
        "computed": {"fixed_term_under_2_years": under_2_years},
    }
    if under_2_years and in_term and lease.fixed_term_increase_in_agreement is not True:
        return (
            "red",
            (
                "Rent increase during a fixed term under 2 years without the agreement "
                "specifying the increase."
            ),
            evidence,
        )
    return "green", "No undisclosed rent increase during a fixed term under 2 years.", evidence


def _other_security_check(lease: LeaseInput) -> CheckResult:
    """s160(1): a person "must not require or receive from a tenant or another person
    anything other than a rental bond as security" for the tenant's compliance
    (corpus text as at 2026-07-24).
    """
    evidence = {"fields": {"other_security_amount": str(lease.other_security_amount)}}
    if lease.other_security_amount > 0:
        return (
            "red",
            f"Security of {lease.other_security_amount} besides the rental bond is not permitted.",
            evidence,
        )
    return "green", "No security besides the rental bond.", evidence


def _break_fee_check(lease: LeaseInput) -> CheckResult:
    """s107(4): for a fixed term of not more than 3 years the break fee is capped on a
    sliding scale from "an amount equal to 4 weeks rent" (less than 25% of the term
    expired) down to 1 week's rent (corpus text as at 2026-07-24; mandatory scale
    commenced 2020-03-23). Thirty-six months is reckoned in calendar months under
    the corresponding-date rule.
    """
    weekly = to_weekly_rent(lease.rent_amount, lease.rent_frequency)
    max_fee = (weekly * 4).quantize(Decimal("0.01"))
    term_days = (lease.end_date - lease.start_date).days
    scale_applies = lease.end_date <= add_months(lease.start_date, 36)
    evidence = {
        "fields": {"break_fee_amount": str(lease.break_fee_amount), "term_days": term_days},
        "computed": {"max_break_fee": str(max_fee), "scale_applies": scale_applies},
    }
    if scale_applies and lease.break_fee_amount > max_fee:
        return (
            "red",
            f"Break fee of {lease.break_fee_amount} exceeds the 4-week maximum of {max_fee}.",
            evidence,
        )
    return "green", "Break fee is within the statutory scale.", evidence


NSW_RULES = [
    Rule(
        rule_id="nsw.bond_max_4_weeks",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "159")],
        applies_from=COMMENCED,
        applies_to=None,
        required_inputs=["bond_amount"],
        check=_bond_check,
    ),
    Rule(
        rule_id="nsw.rent_in_advance_max",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "33")],
        applies_from=COMMENCED,
        applies_to=None,
        required_inputs=["rent_in_advance_amount"],
        check=_advance_check,
    ),
    Rule(
        rule_id="nsw.holding_fee_max_1_week",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "24")],
        applies_from=COMMENCED,
        applies_to=None,
        required_inputs=["holding_deposit_amount"],
        check=_holding_fee_check,
    ),
    Rule(
        rule_id="nsw.rent_increase_frequency",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "41")],
        applies_from=FREQ_COMMENCED,
        applies_to=None,
        required_inputs=["rent_increases"],
        check=_frequency_check,
    ),
    Rule(
        rule_id="nsw.rent_increase_first_year",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "41")],
        applies_from=FIRST_YEAR_COMMENCED,
        applies_to=None,
        required_inputs=["rent_increases"],
        check=_first_year_check,
    ),
    Rule(
        rule_id="nsw.rent_increase_notice",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "41")],
        applies_from=COMMENCED,
        applies_to=None,
        required_inputs=["rent_increases"],
        check=_notice_check,
    ),
    Rule(
        rule_id="nsw.fixed_term_increase_disclosure",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "42")],
        applies_from=COMMENCED,
        applies_to=S42_REPEALED,
        required_inputs=["end_date", "rent_increases"],
        check=_disclosure_check,
    ),
    Rule(
        rule_id="nsw.no_other_security",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "160")],
        applies_from=COMMENCED,
        applies_to=None,
        required_inputs=["other_security_amount"],
        check=_other_security_check,
    ),
    Rule(
        rule_id="nsw.break_fee_cap",
        jurisdiction="NSW",
        citations=[SectionRef(ACT, "107")],
        applies_from=FREQ_COMMENCED,
        applies_to=None,
        required_inputs=["break_fee_amount", "end_date"],
        check=_break_fee_check,
    ),
]
