"""Clause rules judged by the LLM.

Statutory basis pinned from the corpus on 2026-07-28.

Prohibited terms. Act s 19(2) (in force 2025-08-15 window): "(a) that the
tenant must, at the end of the tenancy—(i) have the carpet professionally
cleaned or pay for the carpet to be professionally cleaned, or (ii) have the
premises, or part of the premises, professionally fumigated or pay for the
premises ... to be professionally fumigated, (b) that the tenant must take
out a specified, or any, form of insurance, (c) exempting the landlord from
liability for any act or omission ..., (d) that, if the tenant breaches the
agreement, the tenant is liable to pay all or any part of the remaining rent
under the agreement, increased rent, a penalty or liquidated damages,
(e) that, if the tenant does not breach the agreement, the rent is or may be
reduced or the tenant is to be or may be paid a rebate of rent or other
benefit, (f) that the tenant must use the services of a specified person or
business to carry out any of the tenant's obligations". s 19(3) allows a
reasonable condition in an animal-consent under Part 3 Division 8 (the
original 2010 text allowed a carpet-cleaning term "if the landlord permits
the tenant to keep an animal"). Corpus windows: (a)(ii) fumigation and (f)
first appear in the 2025-05-19 version; the other paragraphs are original
(Act commenced 2011-01-31). Regulation cl 5 (2019-12-16 version) prescribed
the specified-person effect and the specific-utility-provider effect; the
current cl 5 (from 2025-05-19) keeps only the utility-provider term, "unless
the landlord must use a specific utility provider for the premises".
"""

from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.citations import format_citation
from app.models import Act
from app.rules.base import Citation, SectionRef
from app.rules.nsw import COMMENCED
from app.services.legislation import section_at

PETS_COMMENCED = date(2025, 5, 19)
REG_COMMENCED = date(2019, 12, 16)


@dataclass(frozen=True)
class ClauseRule:
    rule_id: str
    jurisdiction: Literal["NSW", "VIC"]
    family: Literal["prohibited"]
    ref: SectionRef
    applies_from: date | None
    applies_to: date | None
    question: str


PROHIBITED_RULES = [
    ClauseRule(
        rule_id="nsw.clause.carpet_cleaning",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=COMMENCED,
        applies_to=None,
        question=(
            "A term with the effect that the tenant must, at the end of the "
            "tenancy, have the carpet professionally cleaned or pay for the "
            "carpet to be professionally cleaned (s 19(2)(a)(i)). Not breached "
            "where the requirement is tied to the landlord permitting the "
            "tenant to keep an animal on the premises (s 19(3))."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.fumigation",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=PETS_COMMENCED,
        applies_to=None,
        question=(
            "A term with the effect that the tenant must have the premises, or "
            "part of the premises, professionally fumigated, or pay for that "
            "fumigation, at the end of the tenancy (s 19(2)(a)(ii)). Not "
            "breached where the requirement is a reasonable condition of a "
            "consent to keep an animal (s 19(3))."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.specified_insurance",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=COMMENCED,
        applies_to=None,
        question=(
            "A term with the effect that the tenant must take out a specified, "
            "or any, form of insurance (s 19(2)(b))."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.landlord_liability_exemption",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=COMMENCED,
        applies_to=None,
        question=(
            "A term exempting the landlord from liability for any act or "
            "omission by the landlord, the landlord's agent or any person "
            "acting on behalf of the landlord or agent (s 19(2)(c))."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.breach_penalty",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=COMMENCED,
        applies_to=None,
        question=(
            "A term with the effect that, if the tenant breaches the "
            "agreement, the tenant is liable to pay all or any part of the "
            "remaining rent under the agreement, increased rent, a penalty or "
            "liquidated damages (s 19(2)(d)). A fixed-term break fee provided "
            "for by the Act is a separate matter and not by itself this "
            "effect."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.no_breach_rent_inducement",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=COMMENCED,
        applies_to=None,
        question=(
            "A term with the effect that, if the tenant does not breach the "
            "agreement, the rent is or may be reduced, or the tenant is to be "
            "or may be paid a rebate of rent or other benefit (s 19(2)(e))."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.specified_contractor",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=PETS_COMMENCED,
        applies_to=None,
        question=(
            "A term with the effect that the tenant must use the services of a "
            "specified person or business to carry out any of the tenant's "
            "obligations under the agreement (s 19(2)(f))."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.specified_contractor_reg",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("sl-2019-0629", "5"),
        applies_from=REG_COMMENCED,
        applies_to=PETS_COMMENCED,
        question=(
            "A term with the effect that the tenant must use the services of a "
            "specified person or business to carry out any of the tenant's "
            "obligations under the agreement (Regulation cl 5(a) as in force "
            "before 19 May 2025, when the effect moved into the Act as "
            "s 19(2)(f))."
        ),
    ),
    ClauseRule(
        rule_id="nsw.clause.utility_provider",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("sl-2019-0629", "5"),
        applies_from=REG_COMMENCED,
        applies_to=None,
        question=(
            "A term requiring the tenant to use a specific utility provider "
            "(Regulation cl 5). Not breached where the landlord is required to "
            "use a specific utility provider for the premises."
        ),
    ),
]


def rule_active(rule: ClauseRule, as_at: date) -> bool:
    if rule.applies_from and as_at < rule.applies_from:
        return False
    return not (rule.applies_to and as_at >= rule.applies_to)


async def resolve_rule(session: AsyncSession, rule: ClauseRule, as_at: date) -> Citation | None:
    section = await section_at(session, rule.ref.act_slug, rule.ref.section_no, as_at)
    if section is None:
        return None
    act = await session.get(Act, section.act_id)
    return Citation(
        act=act.title,
        section_no=rule.ref.section_no,
        as_at=as_at,
        section_id=section.id,
        label=format_citation(rule.ref.section_no),
    )


async def statutory_texts(
    session: AsyncSession, rules: list[ClauseRule], as_at: date
) -> dict[tuple[str, str], str]:
    texts: dict[tuple[str, str], str] = {}
    for rule in rules:
        key = (rule.ref.act_slug, rule.ref.section_no)
        if key in texts:
            continue
        section = await section_at(session, key[0], key[1], as_at)
        if section is not None:
            texts[key] = f"{section.heading}\n{section.body_text}"
    return texts
