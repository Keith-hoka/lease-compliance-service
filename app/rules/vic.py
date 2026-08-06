from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from itertools import pairwise

from app.rules.base import CheckResult, Rule, SectionRef, add_months, to_weekly_rent
from app.schemas.lease import LeaseInput

ACT = "residential-tenancies-act-1997"
REGS = "residential-tenancies-regulations-2021"

RENT_THRESHOLD_WEEKLY = Decimal(900)
S44_CORPUS_FLOOR = date(2020, 4, 6)


def _monthly_rent(lease: LeaseInput) -> Decimal:
    """One month's rent derived from the lease's own frequency, rounded once.

    Monthly leases use the stated amount as-is; fortnightly and weekly amounts
    are scaled straight to a monthly figure rather than round-tripping through
    a cent-rounded weekly equivalent, which would round twice.
    """
    if lease.rent_frequency == "weekly":
        monthly = lease.rent_amount * 52 / 12
    elif lease.rent_frequency == "fortnightly":
        monthly = lease.rent_amount * 26 / 12
    else:
        monthly = lease.rent_amount
    return monthly.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _bond_check(lease: LeaseInput) -> CheckResult:
    """s 31(1)(a): a person "must not demand or accept ... a bond the total of which
    exceeds the amount of rent payable under the residential rental agreement for
    one month"; s 31(3): subsection (1) "does not apply ... if the weekly amount of
    rent payable under the agreement exceeds the prescribed amount"; reg 17: "the
    prescribed amount is $900" (corpus text as at 2026-08-04).
    """
    weekly = to_weekly_rent(lease.rent_amount, lease.rent_frequency)
    monthly = _monthly_rent(lease)
    evidence = {
        "fields": {"bond_amount": str(lease.bond_amount)},
        "computed": {"weekly_rent": str(weekly), "max_bond": str(monthly)},
    }
    if weekly > RENT_THRESHOLD_WEEKLY:
        return (
            "skipped",
            (
                f"The one-month bond cap does not apply: weekly rent {weekly} exceeds "
                f"the prescribed {RENT_THRESHOLD_WEEKLY}."
            ),
            evidence,
        )
    if lease.bond_amount > monthly:
        return (
            "red",
            f"Bond of {lease.bond_amount} exceeds the one-month maximum of {monthly}.",
            evidence,
        )
    return (
        "green",
        f"Bond of {lease.bond_amount} is within the one-month maximum of {monthly}.",
        evidence,
    )


def _advance_check(lease: LeaseInput) -> CheckResult:
    """s 40(1): a residential rental provider "must not solicit or otherwise invite
    a renter to pay rent ... more than 1 month in advance"; s 40(2) disapplies
    subsection (1) only above the reg 17 prescribed weekly amount. s 40(3)
    (inserted by No. 6/2025, in force 2025-11-25) separately prohibits accepting
    unsolicited payment of rent more than one month in advance and is not
    disapplied by s 40(2); whether a payment was solicited cannot be decided from
    structured lease input, so s 40(3) is not modelled here (corpus text as at
    2026-08-04).
    """
    weekly = to_weekly_rent(lease.rent_amount, lease.rent_frequency)
    monthly = _monthly_rent(lease)
    evidence = {
        "fields": {"rent_in_advance_amount": str(lease.rent_in_advance_amount)},
        "computed": {"weekly_rent": str(weekly), "max_advance": str(monthly)},
    }
    if weekly > RENT_THRESHOLD_WEEKLY:
        return (
            "skipped",
            (
                f"The one-month advance cap in s 40(1) does not apply: weekly rent "
                f"{weekly} exceeds the prescribed {RENT_THRESHOLD_WEEKLY}."
            ),
            evidence,
        )
    if lease.rent_in_advance_amount > monthly:
        return (
            "red",
            (
                f"Rent in advance of {lease.rent_in_advance_amount} exceeds the one-month "
                f"maximum of {monthly}."
            ),
            evidence,
        )
    return (
        "green",
        (
            f"Rent in advance of {lease.rent_in_advance_amount} is within the one-month "
            f"maximum of {monthly}."
        ),
        evidence,
    )


def _frequency_check(lease: LeaseInput) -> CheckResult:
    """s 44(4A): a residential rental provider "must not increase the rent payable
    under a residential rental agreement at intervals of less than 12 months"
    (corpus text as at 2026-08-04). S44_CORPUS_FLOOR names the corpus's
    ingestion floor for s 44, 2020-04-06, not a legal commencement: that is the
    earliest version the corpus carries, it already states the 12-month
    interval, and its own amendment note records (4A) as inserted by No.
    45/2002 s 12(2), predating the corpus's coverage. The corpus shows no
    in-force / not-in-force flip at 2021-03-29; that boundary only renames
    "landlord" and "tenant" to "residential rental provider" and "renter". The
    pre-reform 6-month interval era predates the corpus and is not modelled;
    applies_from is pinned to this floor as a deliberate safety net so that if
    that earlier text were ever backfilled, citation gating alone would not run
    this 12-month check against the 6-month era. Twelve months is reckoned in
    calendar months under the corresponding-date rule.
    """
    pairs = list(pairwise(sorted(i.effective_on for i in lease.rent_increases)))
    gaps = [(later - earlier).days for earlier, later in pairs]
    evidence = {
        "fields": {"rent_increases": [str(i.effective_on) for i in lease.rent_increases]},
        "computed": {"gaps_days": gaps},
    }
    short = [(later - earlier).days for earlier, later in pairs if later < add_months(earlier, 12)]
    if short:
        return (
            "red",
            f"Rent increases less than 12 months apart (shortest gap {min(short)} days).",
            evidence,
        )
    return ("green", "All rent increases are at least 12 months apart.", evidence)


def _fixed_term_check(lease: LeaseInput) -> CheckResult:
    """s 44(4): under a fixed term agreement the rent must not be increased before
    the term ends unless the agreement provides for the increase (a specified
    amount or method) (corpus text as at 2026-08-04). end_date is treated as the
    last day of the term, so an increase effective on end_date counts as in-term
    (NSW's disclosure check uses an exclusive bound, following different
    statutory text). s 44(4)'s second limb - that the increase must also not
    exceed the amount or method the agreement specifies - is not decidable from
    structured lease input and is not modelled here; that belongs to
    clause-audit territory.
    """
    in_term = sorted(
        i.effective_on
        for i in lease.rent_increases
        if lease.start_date <= i.effective_on <= lease.end_date
    )
    evidence = {
        "fields": {
            "fixed_term_increase_in_agreement": str(lease.fixed_term_increase_in_agreement),
            "in_term_increases": [str(d) for d in in_term],
        }
    }
    if in_term and not lease.fixed_term_increase_in_agreement:
        return (
            "red",
            (
                f"Rent increased during the fixed term ({in_term[0]}) without a provision "
                "in the agreement."
            ),
            evidence,
        )
    if in_term:
        return ("green", "In-term rent increases are provided for in the agreement.", evidence)
    return ("green", "No rent increases fall inside the fixed term.", evidence)


VIC_RULES = [
    Rule(
        rule_id="vic.bond_max_1_month",
        jurisdiction="VIC",
        citations=[SectionRef(ACT, "31"), SectionRef(REGS, "17")],
        applies_from=None,
        applies_to=None,
        required_inputs=["bond_amount"],
        check=_bond_check,
    ),
    Rule(
        rule_id="vic.advance_max_1_month",
        jurisdiction="VIC",
        citations=[SectionRef(ACT, "40"), SectionRef(REGS, "17")],
        applies_from=None,
        applies_to=None,
        required_inputs=["rent_in_advance_amount"],
        check=_advance_check,
    ),
    Rule(
        rule_id="vic.rent_increase_frequency",
        jurisdiction="VIC",
        citations=[SectionRef(ACT, "44")],
        applies_from=S44_CORPUS_FLOOR,
        applies_to=None,
        required_inputs=["rent_increases"],
        check=_frequency_check,
    ),
    Rule(
        rule_id="vic.fixed_term_increase_provision",
        jurisdiction="VIC",
        citations=[SectionRef(ACT, "44")],
        applies_from=None,
        applies_to=None,
        required_inputs=["rent_increases", "fixed_term_increase_in_agreement", "end_date"],
        check=_fixed_term_check,
    ),
]
