"""Seeded golden sets for the LLM clause audit, one entry per rule per case.

Scoring contract (prohibited family): for every case, the target rule's
expected verdict is case.expected and every other rule's expected verdict is
green — each case doubles as a hard negative for the other seven rules.
yellow on a red case is a recall miss; red on a green case is a precision
hit against the judging rule.

Mandatory family: one complete lease (every mandatory term present, all
rules expected green) plus one variant per rule omitting exactly that
rule's clause — the same cross-scoring then holds.
"""

from dataclasses import dataclass, field
from typing import Literal

THRESHOLDS: dict[str, tuple[float, float]] = {"default": (0.9, 0.8)}


@dataclass(frozen=True)
class ClauseCase:
    case_id: str
    rule_id: str
    text: str
    expected: Literal["red", "green"]


@dataclass(frozen=True)
class FieldCase:
    case_id: str
    text: str
    lease: dict
    expected: set[str] = field(default_factory=set)


_PREAMBLE = "RESIDENTIAL TENANCY AGREEMENT between landlord and tenant. "

PROHIBITED_CASES = [
    ClauseCase(
        "carpet-red-plain",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE + "The tenant must have all carpets professionally steam cleaned at the "
        "conclusion of the tenancy and provide a receipt to the landlord.",
        "red",
    ),
    ClauseCase(
        "carpet-red-cost",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE + "On termination of this agreement the tenant agrees to pay the cost of "
        "professional carpet cleaning of the premises.",
        "red",
    ),
    ClauseCase(
        "carpet-red-paraphrase",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE + "Upon vacating, the floor coverings are to be cleaned by an accredited "
        "professional cleaning company engaged and paid for by the tenant, "
        "receipts to be produced on request.",
        "red",
    ),
    ClauseCase(
        "carpet-red-buried",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE + "The tenant must keep the premises reasonably clean. The rent is payable "
        "fortnightly in advance. The tenant shall arrange professional carpet "
        "cleaning at the end of the tenancy at the tenant's expense. Keys must be "
        "returned on the final day.",
        "red",
    ),
    ClauseCase(
        "carpet-green-ordinary-cleaning",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE + "The tenant must keep the carpets clean and vacuum them regularly during "
        "the tenancy.",
        "green",
    ),
    ClauseCase(
        "carpet-green-reasonably-clean",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE + "At the end of the tenancy the tenant must leave the premises reasonably "
        "clean, having regard to their condition at the commencement of the "
        "tenancy.",
        "green",
    ),
    ClauseCase(
        "carpet-green-landlord-pays",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE + "The landlord will arrange and pay for professional carpet cleaning "
        "before the tenant takes possession.",
        "green",
    ),
    ClauseCase(
        "carpet-green-animal-carveout",
        "nsw.clause.carpet_cleaning",
        _PREAMBLE + "If the tenant keeps an animal on the premises with the landlord's "
        "consent, the tenant agrees to have the carpets professionally cleaned at "
        "the end of the tenancy.",
        "green",
    ),
    ClauseCase(
        "fumigation-red-plain",
        "nsw.clause.fumigation",
        _PREAMBLE + "On vacating the premises the tenant must have the premises "
        "professionally fumigated at the tenant's cost.",
        "red",
    ),
    ClauseCase(
        "fumigation-red-paraphrase",
        "nsw.clause.fumigation",
        _PREAMBLE + "The tenant shall engage a licensed pest control operator to fumigate "
        "the property at the end of the tenancy and bear the expense of doing so.",
        "red",
    ),
    ClauseCase(
        "fumigation-red-buried",
        "nsw.clause.fumigation",
        _PREAMBLE + "The rent is payable weekly in advance. Keys must be returned on the "
        "final day. Prior to returning possession the tenant will arrange, at "
        "the tenant's own cost, fumigation of the premises by a professional "
        "operator.",
        "red",
    ),
    ClauseCase(
        "fumigation-green-notify",
        "nsw.clause.fumigation",
        _PREAMBLE + "The tenant must promptly notify the landlord of any pest or vermin "
        "infestation observed at the premises.",
        "green",
    ),
    ClauseCase(
        "fumigation-green-landlord-arranges",
        "nsw.clause.fumigation",
        _PREAMBLE + "The landlord will arrange and pay for pest treatment of the premises "
        "before the commencement of the tenancy.",
        "green",
    ),
    ClauseCase(
        "fumigation-green-animal-carveout",
        "nsw.clause.fumigation",
        _PREAMBLE + "As a condition of the landlord's consent to keep a dog at the "
        "premises, the tenant agrees to have the premises professionally "
        "fumigated at the end of the tenancy.",
        "green",
    ),
    ClauseCase(
        "insurance-red-specified",
        "nsw.clause.specified_insurance",
        _PREAMBLE + "The tenant must take out and maintain contents insurance with AAMI for "
        "the duration of the tenancy.",
        "red",
    ),
    ClauseCase(
        "insurance-red-any",
        "nsw.clause.specified_insurance",
        _PREAMBLE + "The tenant is required to obtain public liability insurance from an "
        "insurer nominated by the landlord before taking possession.",
        "red",
    ),
    ClauseCase(
        "insurance-red-maintain",
        "nsw.clause.specified_insurance",
        _PREAMBLE + "Throughout the term the tenant shall maintain a home contents policy "
        "of insurance and provide the certificate of currency to the agent "
        "annually.",
        "red",
    ),
    ClauseCase(
        "insurance-green-encouraged",
        "nsw.clause.specified_insurance",
        _PREAMBLE + "The tenant is encouraged to consider taking out contents insurance for "
        "their own belongings.",
        "green",
    ),
    ClauseCase(
        "insurance-green-landlord-holds",
        "nsw.clause.specified_insurance",
        _PREAMBLE + "The landlord holds building and landlord insurance in respect of the "
        "premises.",
        "green",
    ),
    ClauseCase(
        "insurance-green-not-covered-notice",
        "nsw.clause.specified_insurance",
        _PREAMBLE + "The tenant acknowledges that the tenant's personal belongings are not "
        "covered by the landlord's insurance policies.",
        "green",
    ),
    ClauseCase(
        "liability-red-release",
        "nsw.clause.landlord_liability_exemption",
        _PREAMBLE + "The tenant releases the landlord and the landlord's agent from all "
        "liability for loss, damage or injury suffered in connection with the "
        "premises, howsoever caused.",
        "red",
    ),
    ClauseCase(
        "liability-red-negligence",
        "nsw.clause.landlord_liability_exemption",
        _PREAMBLE + "Under no circumstances will the property owner or the managing agent "
        "be responsible for damage to the tenant's property, even where caused "
        "by their own act or omission.",
        "red",
    ),
    ClauseCase(
        "liability-green-tenant-own",
        "nsw.clause.landlord_liability_exemption",
        _PREAMBLE + "The tenant is responsible for damage to the premises caused by the "
        "tenant or the tenant's guests.",
        "green",
    ),
    ClauseCase(
        "liability-green-notify",
        "nsw.clause.landlord_liability_exemption",
        _PREAMBLE + "The tenant must notify the landlord of any damage to the premises as "
        "soon as practicable.",
        "green",
    ),
    ClauseCase(
        "penalty-red-remaining-rent",
        "nsw.clause.breach_penalty",
        _PREAMBLE + "If the tenant breaches this agreement, the tenant must pay the rent "
        "for the whole of the remainder of the term.",
        "red",
    ),
    ClauseCase(
        "penalty-red-fixed-penalty",
        "nsw.clause.breach_penalty",
        _PREAMBLE + "In the event of any default by the tenant under this agreement, a "
        "penalty of $500 is payable to the landlord, in addition to liquidated "
        "damages of $2,000.",
        "red",
    ),
    ClauseCase(
        "penalty-green-break-fee",
        "nsw.clause.breach_penalty",
        _PREAMBLE + "If the tenant ends the agreement before the end of the fixed term, "
        "other than for a breach by the landlord, the tenant must pay a break "
        "fee of two weeks rent.",
        "green",
    ),
    ClauseCase(
        "penalty-green-termination-notice",
        "nsw.clause.breach_penalty",
        _PREAMBLE + "If the rent remains unpaid for 14 days, the landlord may issue a "
        "termination notice in accordance with the Act.",
        "green",
    ),
    ClauseCase(
        "inducement-red-conditional-discount",
        "nsw.clause.no_breach_rent_inducement",
        _PREAMBLE + "Provided the tenant does not breach this agreement, the weekly rent "
        "will be reduced by $20 per week.",
        "red",
    ),
    ClauseCase(
        "inducement-red-compliance-rebate",
        "nsw.clause.no_breach_rent_inducement",
        _PREAMBLE + "If the tenant complies with all terms of this agreement for twelve "
        "months, the tenant will receive a rebate of one week's rent.",
        "red",
    ),
    ClauseCase(
        "inducement-green-plain-rent",
        "nsw.clause.no_breach_rent_inducement",
        _PREAMBLE + "The rent is $560 per week payable weekly in advance.",
        "green",
    ),
    ClauseCase(
        "inducement-green-moving-incentive",
        "nsw.clause.no_breach_rent_inducement",
        _PREAMBLE + "The first two weeks of the tenancy are rent-free as a moving-in incentive.",
        "green",
    ),
    ClauseCase(
        "contractor-red-cleaning",
        "nsw.clause.specified_contractor",
        _PREAMBLE + "The tenant must use Jim's Cleaning Services for all cleaning the "
        "tenant is required to carry out under this agreement.",
        "red",
    ),
    ClauseCase(
        "contractor-red-lawn",
        "nsw.clause.specified_contractor",
        _PREAMBLE + "Lawn mowing and garden upkeep, which are the tenant's responsibility, "
        "are to be performed exclusively by GreenCare Gardening Pty Ltd at the "
        "tenant's cost.",
        "red",
    ),
    ClauseCase(
        "contractor-green-obligation-only",
        "nsw.clause.specified_contractor",
        _PREAMBLE + "The tenant must keep the lawn mowed and the garden tidy.",
        "green",
    ),
    ClauseCase(
        "contractor-green-licensed",
        "nsw.clause.specified_contractor",
        _PREAMBLE + "Any repairs arranged by the tenant must be carried out by "
        "appropriately licensed tradespersons.",
        "green",
    ),
    ClauseCase(
        "utility-red-named-provider",
        "nsw.clause.utility_provider",
        _PREAMBLE + "The tenant must obtain electricity for the premises from Energy "
        "Australia for the duration of the tenancy.",
        "red",
    ),
    ClauseCase(
        "utility-red-nominated",
        "nsw.clause.utility_provider",
        _PREAMBLE + "Gas for the premises must be supplied by the retailer nominated by "
        "the landlord from time to time, currently AGL.",
        "red",
    ),
    ClauseCase(
        "utility-green-embedded-network",
        "nsw.clause.utility_provider",
        _PREAMBLE + "The premises form part of an embedded electricity network and the "
        "landlord is required to use PowerNet as the supplier for the building; "
        "the tenant must use PowerNet for electricity to the premises.",
        "green",
    ),
    ClauseCase(
        "utility-green-tenant-arranges",
        "nsw.clause.utility_provider",
        _PREAMBLE + "The tenant is responsible for arranging electricity and gas accounts "
        "for the premises in the tenant's own name.",
        "green",
    ),
]

_MANDATORY_CLAUSES = {
    "nsw.clause.states_rent_payment": (
        "The rent is $560 per week, payable weekly in advance by bank transfer."
    ),
    "nsw.clause.quiet_enjoyment_term": (
        "The landlord agrees that the tenant will have quiet enjoyment of the "
        "premises without interference by the landlord or the landlord's agent."
    ),
    "nsw.clause.tenant_use_term": (
        "The tenant must not use the premises for an illegal purpose, cause a "
        "nuisance, or interfere with the peace, comfort or privacy of "
        "neighbours."
    ),
    "nsw.clause.habitability_term": (
        "The landlord will provide the premises in a reasonably clean condition, "
        "fit for habitation."
    ),
    "nsw.clause.repairs_term": (
        "The landlord will provide and maintain the premises in a reasonable state of repair."
    ),
    "nsw.clause.locks_security_term": (
        "The landlord will provide and maintain locks and other security "
        "devices to keep the premises reasonably secure."
    ),
}


def _mandatory_lease(omit: str | None = None) -> str:
    clauses = [c for rid, c in _MANDATORY_CLAUSES.items() if rid != omit]
    return _PREAMBLE + " ".join(clauses) + " Keys must be returned at the end of the tenancy."


MANDATORY_CASES = [
    ClauseCase(
        "mandatory-complete",
        "nsw.clause.states_rent_payment",
        _mandatory_lease(),
        "green",
    ),
    *[
        ClauseCase(
            f"mandatory-missing-{rid.rsplit('.', 1)[1]}",
            rid,
            _mandatory_lease(omit=rid),
            "red",
        )
        for rid in _MANDATORY_CLAUSES
    ],
]

FIELD_CASES = [
    FieldCase(
        "fields-rent-mismatch",
        _PREAMBLE + "The rent is $520 per week payable weekly in advance. The bond "
        "is $2,240. The term commences on 1 February 2026.",
        {"rent_amount": "560", "rent_frequency": "weekly", "bond_amount": "2240"},
        {"rent_amount"},
    ),
    FieldCase(
        "fields-all-match",
        _PREAMBLE + "The rent is $560 per week payable weekly in advance. The bond "
        "is $2,240. The term commences on 1 February 2026 and ends on 31 January "
        "2027.",
        {
            "rent_amount": "560",
            "rent_frequency": "weekly",
            "bond_amount": "2240",
            "start_date": "2026-02-01",
            "end_date": "2027-01-31",
        },
        set(),
    ),
    FieldCase(
        "fields-frequency-mismatch",
        _PREAMBLE + "The rent is $1,120 payable per fortnight in advance.",
        {"rent_amount": "1120", "rent_frequency": "weekly"},
        {"rent_frequency"},
    ),
    FieldCase(
        "fields-date-mismatch",
        _PREAMBLE + "The tenancy commences on 15 March 2026. The rent is $560 per week.",
        {"rent_amount": "560", "start_date": "2026-02-01"},
        {"start_date"},
    ),
    FieldCase(
        "fields-absent-field-silent",
        _PREAMBLE + "The rent is $560 per week payable weekly.",
        {"rent_amount": "560", "holding_deposit_amount": "560"},
        set(),
    ),
]
