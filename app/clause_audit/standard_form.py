"""Standard-form comparison: term source, deterministic screen, duty map.

Terms are fetched point-in-time from the corpus. The screen shingles a
term's prescribed text into 8-token windows and computes the fraction found
in the lease text; verbatim or near-verbatim terms (containment >= 0.9) are
green without any LLM call. Terms with fewer than 12 usable tokens - or a
prescribed body under 12 tokens, the VIC table-content limitation - always
go to the LLM.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Act, Section
from app.rules.base import add_months
from app.schemas.clause_audit import ClauseLeaseInput

SHINGLE_TOKENS = 8
CONTAINMENT_THRESHOLD = 0.9
MIN_SCREEN_TOKENS = 12

NSW_REG_SLUG = "sl-2019-0629"
VIC_REGS_SLUG = "residential-tenancies-regulations-2021"

NSW_ACT_DUTIES = {
    "3": "33",
    "15": "50",
    "16": "51",
    "19": "52",
    "32": "70",
}
"""Term number -> Act `act-2010-042` section_no, for dual-citation findings.

Probe-verified against the current corpus on 2026-08-09 (Task 2 Step 1):
each term's body text was read in full and compared word-for-word against
its candidate Act section.

- "3": T3 ("RENT", tenant's promises) cl 3.1 "to pay rent on time" mirrors
  Act s 33(1) "A tenant must pay the rent ... on or before the day set out
  in the agreement." T4 ("RENT", landlord's promises) is a different duty
  (s 33(2), advance-rent limits) so is not mapped.
- "15": T15 ("TENANT'S RIGHT TO QUIET ENJOYMENT") cl 15.1 reproduces s
  50(1) almost verbatim, down to "having superior title to that of the
  landlord (such as a head landlord)".
- "16": T16 ("USE OF THE PREMISES BY TENANT") cl 16.1-16.5 restate s
  51(1)(a)-(e) (illegal purpose, nuisance, interference with neighbours,
  damage, occupant numbers) one-for-one. T17/T18 carry other USE OF THE
  PREMISES BY TENANT subsections (cleanliness, end-of-tenancy) and are not
  mapped.
- "19": T19 ("LANDLORD'S GENERAL OBLIGATIONS FOR RESIDENTIAL PREMISES",
  the same heading as s 52) cl 19.1 states the habitability duty and its
  own Note reads "Section 52 of the Residential Tenancies Act 2010
  specifies the minimum requirements ..." - an explicit statutory
  cross-reference. T19 also carries the repair duty at cl 19.3 ("to keep
  the residential premises in a reasonable state of repair, considering
  the age of, the rent paid for and the prospective life of the
  premises"), which paraphrases s 63(1) ("reasonable state of repair,
  having regard to the age of, rent payable for and prospective life of
  the premises"). Since only one Act section maps per term and T19's own
  Note points at s 52, s 63 is not mapped here: T20 is "URGENT REPAIRS" (a
  landlord reimbursement mechanism for tenant-arranged emergency repairs,
  capped at $1,000), a distinct duty from s 63's general
  reasonable-state-of-repair obligation, so it is not a match either. The
  repair duty (s 63) therefore has no distinct dual-citation term.
- "32": T32 ("LOCKS AND SECURITY DEVICES", landlord's promises) cl
  32.1-32.3 restate s 70(1)-(3) (provide/maintain locks, give key copies,
  no charge except replacement cost) one-for-one. T33 (tenant's reciprocal
  promises) and T34 (a narrow key-copy carve-out) are different duties and
  are not mapped.
"""
NSW_ACT_SLUG = "act-2010-042"

_PLACEHOLDER_RE = re.compile(r"\[[^\]]*\]")
_STAR_OPTION_RE = re.compile(r"\*\w*")


@dataclass(frozen=True)
class FormTerm:
    rule_id: str
    section_no: str
    heading: str
    body: str
    section_id: uuid.UUID
    act_slug: str
    act_duty: str | None


def normalize(text: str) -> str:
    """Lowercase, unify punctuation, and strip form-filling placeholders.

    Em and en dashes become spaces (they separate words, e.g. a numbered
    clause lead-in like "agrees— 16.1"), not hyphens - a real hyphen
    typed in the source text (e.g. "co-tenant") is left alone so it stays
    part of one token.
    """
    cleaned = _PLACEHOLDER_RE.sub(" ", text)
    cleaned = _STAR_OPTION_RE.sub(" ", cleaned)
    cleaned = (
        cleaned.replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("—", " ")
        .replace("–", " ")
    )
    cleaned = re.sub(r"[^\w\s'\"-]", " ", cleaned.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _tokens(text: str) -> list[str]:
    return normalize(text).split()


def _shingles(tokens: list[str]) -> set[tuple[str, ...]]:
    if len(tokens) < SHINGLE_TOKENS:
        return set()
    return {tuple(tokens[i : i + SHINGLE_TOKENS]) for i in range(len(tokens) - SHINGLE_TOKENS + 1)}


def containment(term_text: str, document_text: str) -> float:
    term_shingles = _shingles(_tokens(term_text))
    if not term_shingles:
        return 0.0
    document_shingles = _shingles(_tokens(document_text))
    return len(term_shingles & document_shingles) / len(term_shingles)


def screen_terms(
    terms: list[FormTerm], document_text: str
) -> tuple[list[tuple[FormTerm, float]], list[FormTerm]]:
    """Partition terms into screened-green (with containment ratio) and residual.

    Only the body length is checked against MIN_SCREEN_TOKENS: heading+body
    is always at least as long as body alone, so a heading+body check would
    never fire on a term the body check hasn't already caught.
    """
    green: list[tuple[FormTerm, float]] = []
    residual: list[FormTerm] = []
    for term in terms:
        if len(_tokens(term.body)) < MIN_SCREEN_TOKENS:
            residual.append(term)
            continue
        full = f"{term.heading} {term.body}"
        ratio = containment(full, document_text)
        if ratio >= CONTAINMENT_THRESHOLD:
            green.append((term, ratio))
        else:
            residual.append(term)
    return green, residual


def _term_no(section_no: str) -> str:
    return section_no.rsplit("-T", 1)[1]


_TERM_KEY_RE = re.compile(r"(\d+)(\D*)")


def _term_sort_key(section_no: str) -> tuple[int, str]:
    """Numeric term order with a letter suffix sorting next to its base (30, 30A, 31)."""
    digits, suffix = _TERM_KEY_RE.match(_term_no(section_no)).groups()
    return int(digits), suffix


def _vic_form(lease: ClauseLeaseInput | None) -> tuple[str, str | None]:
    """Form 2 for fixed terms over 5 years, else Form 1 (noting unknowns)."""
    if lease is None or lease.start_date is None or lease.end_date is None:
        return "1", "form selection defaulted to Form 1: lease term length unknown"
    if lease.end_date > add_months(lease.start_date, 60):
        return "2", None
    return "1", None


async def fetch_form_terms(
    session: AsyncSession,
    jurisdiction: str,
    as_at: date,
    lease: ClauseLeaseInput | None,
) -> tuple[list[FormTerm], str | None]:
    note: str | None = None
    if jurisdiction == "VIC":
        form, note = _vic_form(lease)
        slug, pattern = VIC_REGS_SLUG, f"S1-F{form}-T%"
        rule_prefix = f"vic.clause.sf_f{form}_t"
    else:
        slug, pattern = NSW_REG_SLUG, "S1-T%"
        rule_prefix = "nsw.clause.sf_t"
    query = (
        select(Section)
        .join(Act, Act.id == Section.act_id)
        .where(
            Act.slug == slug,
            Section.section_no.like(pattern),
            Section.section_no.not_like(f"{pattern}-%"),
            Section.valid_from <= as_at,
            (Section.valid_to.is_(None)) | (Section.valid_to > as_at),
        )
    )
    sections = (await session.execute(query)).scalars().all()
    terms = [
        FormTerm(
            rule_id=f"{rule_prefix}{_term_no(section.section_no).lower()}",
            section_no=section.section_no,
            heading=section.heading,
            body=section.body_text,
            section_id=section.id,
            act_slug=slug,
            act_duty=(
                NSW_ACT_DUTIES.get(_term_no(section.section_no)) if jurisdiction != "VIC" else None
            ),
        )
        for section in sections
    ]
    terms.sort(key=lambda t: _term_sort_key(t.section_no))
    return terms, note
