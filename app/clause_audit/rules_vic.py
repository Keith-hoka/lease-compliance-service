"""VIC clause rules judged by the LLM.

Statutory basis pinned from the corpus on 2026-08-05. Everything here
commenced with the 2021-03-29 reform package (corpus-verified: s 27B,
s 27C and reg 11 are all absent at 2021-03-28 and present at
2021-03-29).

Act s 27B(1): "A residential rental agreement must not include any of
the following terms- (a) a term that requires the renter to take out
any form of insurance; (b) a term that exempts the residential rental
provider from liability for an act of- (i) the residential rental
provider or that person's agent; or (ii) a person acting on behalf of
the residential rental provider or that person's agent; (c) a term that
provides that if the renter contravenes the residential rental
agreement, the renter is liable to pay- (i) all or part of the
remaining rent under the residential rental agreement; or (ii)
increased rent; or (iii) a penalty; or (iv) liquidated damages; (d) a
term that requires all or part of the rented premises to be
professionally cleaned at the end of the tenancy, unless that term is
contained in the standard form; (e) a term that requires the renter to
pay the cost of having all or part of the rented premises
professionally cleaned at the end of the tenancy, unless that term is
contained in the standard form; (f) a term that provides that, if the
renter does not contravene the residential rental agreement- (i) the
rent is reduced; or (ii) the rent may be reduced; or (iii) the renter
is to be paid a rebate or other benefit; or (iv) the renter may be paid
a rebate or other benefit; (g) any other prescribed prohibited term."
s 27B(2) adds: a term must not require a party "to bear any fees, costs
or charges incurred by the other party in connection with the
preparation of the residential rental agreement". s 27C describes the
standard form's permitted conditional cleaning terms: professional
cleaning only where it "becomes required to restore the premises to the
condition they were in immediately before the start of the tenancy,
taking into account fair wear and tear".

Regulations reg 11 prescribes nine further prohibited terms for
s 27B(1)(g); each rule below quotes its effect in its question.

Excluded and recorded in docs/rule-candidates.md: s 27 invalid
additional terms, s 28 harsh and unconscionable terms, regs 39/53/73
(other tenure types), standard-form comparison (a later milestone).
"""

from datetime import date

from app.clause_audit.rules import ClauseRule
from app.rules.base import SectionRef

ACT = "residential-tenancies-act-1997"
REGS = "residential-tenancies-regulations-2021"

VIC_COMMENCED = date(2021, 3, 29)

_CLEANING_CARVE_OUT = (
    " Not breached where the term requires cleaning only if professional "
    "cleaning becomes required to restore the premises to the condition "
    "they were in immediately before the start of the tenancy, taking "
    "into account fair wear and tear (the standard form's s 27C shape)."
)


def _act_rule(rule_id: str, question: str) -> ClauseRule:
    return ClauseRule(
        rule_id=rule_id,
        jurisdiction="VIC",
        family="prohibited",
        ref=SectionRef(ACT, "27B"),
        applies_from=VIC_COMMENCED,
        applies_to=None,
        question=question,
    )


def _reg_rule(rule_id: str, question: str) -> ClauseRule:
    return ClauseRule(
        rule_id=rule_id,
        jurisdiction="VIC",
        family="prohibited",
        ref=SectionRef(REGS, "11"),
        applies_from=VIC_COMMENCED,
        applies_to=None,
        question=question,
    )


VIC_PROHIBITED_RULES = [
    _act_rule(
        "vic.clause.renter_insurance",
        "A term with the effect that the renter must take out any form of insurance (s 27B(1)(a)).",
    ),
    _act_rule(
        "vic.clause.provider_liability_exemption",
        "A term that exempts the residential rental provider from liability "
        "for an act of the provider, the provider's agent, or a person "
        "acting on behalf of either (s 27B(1)(b)).",
    ),
    _act_rule(
        "vic.clause.breach_penalty",
        "A term with the effect that, if the renter contravenes the "
        "agreement, the renter is liable to pay all or part of the "
        "remaining rent, increased rent, a penalty or liquidated damages "
        "(s 27B(1)(c)). A fixed early-termination fee whose calculation "
        "basis is set out in the agreement is judged under a separate rule "
        "and is not by itself this effect.",
    ),
    _act_rule(
        "vic.clause.professional_cleaning_required",
        "A term with the effect that all or part of the premises must be "
        "professionally cleaned at the end of the tenancy (s 27B(1)(d)). "
        "Judge only terms that oblige the cleaning itself; a term that "
        "only allocates the cost of cleaning to the renter is s 27B(1)(e), "
        "not this effect." + _CLEANING_CARVE_OUT,
    ),
    _act_rule(
        "vic.clause.professional_cleaning_cost",
        "A term with the effect that the renter must pay the cost of "
        "having all or part of the premises professionally cleaned at the "
        "end of the tenancy (s 27B(1)(e))." + _CLEANING_CARVE_OUT,
    ),
    _act_rule(
        "vic.clause.no_breach_rent_inducement",
        "A term with the effect that, if the renter does not contravene "
        "the agreement, the rent is or may be reduced, or the renter is to "
        "be or may be paid a rebate or other benefit (s 27B(1)(f)).",
    ),
    _act_rule(
        "vic.clause.preparation_costs",
        "A term that requires a party to bear any fees, costs or charges "
        "incurred by the other party in connection with the preparation of "
        "the agreement (s 27B(2)).",
    ),
    _reg_rule(
        "vic.clause.unreviewed_contract",
        "A term which binds the renter to a contract that the renter did "
        "not agree to in writing, after having an opportunity to review "
        "it, before entering into the rental agreement (reg 11(a)).",
    ),
    _reg_rule(
        "vic.clause.renter_indemnity",
        "A term which requires the renter to indemnify the residential "
        "rental provider (reg 11(b)).",
    ),
    _reg_rule(
        "vic.clause.late_availability_claim_waiver",
        "A term which prevents the renter from making a claim for "
        "compensation because the premises are not available on the "
        "commencement date of the agreement (reg 11(c)).",
    ),
    _reg_rule(
        "vic.clause.costly_payment_method",
        "A term which requires rent to be paid in advance by a payment "
        "method that carries additional costs (reg 11(d)). Bank fees or "
        "account fees payable on the renter's own bank account are not "
        "such costs.",
    ),
    _reg_rule(
        "vic.clause.third_party_services",
        "A term which requires the renter to use the services of a third "
        "party service provider nominated by the residential rental "
        "provider (reg 11(e)). Only services of a person other than the "
        "rental provider count: a payment portal, app or facility operated "
        "by the rental provider itself is not this effect. Not breached "
        "where the nominated service is an embedded network.",
    ),
    _reg_rule(
        "vic.clause.safety_maintenance_transfer",
        "A term which imposes fees for, or delegates to the renter, "
        "safety-related maintenance that is the responsibility of the "
        "residential rental provider (reg 11(f)).",
    ),
    _reg_rule(
        "vic.clause.tribunal_costs_transfer",
        "A term which makes the renter liable for the residential rental "
        "provider's costs of filing an application at the Tribunal "
        "(reg 11(g)).",
    ),
    _reg_rule(
        "vic.clause.insurance_excess_transfer",
        "A term which makes the renter liable by default for an insurance "
        "excess to be paid under an insurance policy of the rental "
        "provider (reg 11(h)).",
    ),
    _reg_rule(
        "vic.clause.fixed_break_fees",
        "A term which imposes fixed fees for terminating the agreement "
        "early (reg 11(i)). Not breached where the basis for calculating "
        "the fixed fees has been set out in the agreement.",
    ),
]
