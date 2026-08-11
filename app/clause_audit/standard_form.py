"""Standard-form comparison: term source, deterministic screen, duty map.

Terms are fetched point-in-time from the corpus. The screen shingles a
term's prescribed text into 8-token windows and computes the fraction found
in the lease text; near-verbatim terms (containment >= 0.9 AND at most
MAX_MISSING_SHINGLES missing windows) are green without any LLM call. Terms with fewer than 12 usable tokens - or a
prescribed body under 12 tokens, the VIC table-content limitation - always
go to the LLM. run_standard_form orchestrates both passes: it screens,
then judges the residual terms in batches, citing each against the
prescribed form and, for NSW terms with a mapped Act duty, that section too.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.citations import format_citation
from app.clause_audit.document import DocumentInput
from app.clause_audit.verify import quote_matches
from app.llm.client import JudgeFn
from app.llm.prompts import standard_form_instruction
from app.llm.schemas import standard_form_output_model
from app.models import Act, Section
from app.rules.base import Citation, add_months
from app.schemas.clause_audit import ClauseFinding, ClauseLeaseInput

SHINGLE_TOKENS = 8
CONTAINMENT_THRESHOLD = 0.9
MAX_MISSING_SHINGLES = 7
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


def containment(term_text: str, document_text: str) -> tuple[float, int]:
    """Containment ratio and the absolute count of missing shingles."""
    term_shingles = _shingles(_tokens(term_text))
    if not term_shingles:
        return 0.0, 0
    document_shingles = _shingles(_tokens(document_text))
    missing = len(term_shingles - document_shingles)
    return 1 - missing / len(term_shingles), missing


def screen_terms(
    terms: list[FormTerm], document_text: str
) -> tuple[list[tuple[FormTerm, float]], list[FormTerm]]:
    """Partition terms into screened-green (with containment ratio) and residual.

    Only the body length is checked against MIN_SCREEN_TOKENS: heading+body
    is always at least as long as body alone, so a heading+body check would
    never fire on a term the body check hasn't already caught.

    Green needs the ratio AND an absolute missing-shingle budget: a fixed
    ratio alone tolerates single-word adverse edits once a term passes ~87
    tokens, which would green-light over half the prescribed terms against
    the family's headline capability. An interior one-word edit breaks all
    8 windows spanning it, so MAX_MISSING_SHINGLES = 7 pushes any such
    edit to the LLM regardless of term length (an edit within 7 tokens of
    a term's edge breaks fewer windows and can still pass - the honest
    residual). Placeholder-dense terms measure 8-30 missing shingles even
    on verbatim leases (their bracketed fill values never match), so those
    few terms are permanently judged rather than screened - the measured
    cost is 4 Form 1 and 7 Form 2 terms, zero NSW.
    """
    green: list[tuple[FormTerm, float]] = []
    residual: list[FormTerm] = []
    for term in terms:
        if len(_tokens(term.body)) < MIN_SCREEN_TOKENS:
            residual.append(term)
            continue
        full = f"{term.heading} {term.body}"
        ratio, missing = containment(full, document_text)
        if ratio >= CONTAINMENT_THRESHOLD and missing <= MAX_MISSING_SHINGLES:
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


BATCH_SIZE = 8


def _citations(
    term: FormTerm, as_at: date, act_section_ids: dict[str, uuid.UUID]
) -> list[Citation]:
    """Cite the prescribed-form term, plus its mapped Act duty section if any."""
    cites = [
        Citation(
            act=term.act_slug,
            section_no=term.section_no,
            as_at=as_at,
            section_id=term.section_id,
            label=format_citation(term.section_no),
        )
    ]
    if term.act_duty is not None and term.act_duty in act_section_ids:
        cites.append(
            Citation(
                act=NSW_ACT_SLUG,
                section_no=term.act_duty,
                as_at=as_at,
                section_id=act_section_ids[term.act_duty],
                label=format_citation(term.act_duty),
            )
        )
    return cites


async def _act_duty_section_ids(session: AsyncSession, as_at: date) -> dict[str, uuid.UUID]:
    """Point-in-time section ids for the Act duties NSW terms are dual-cited against."""
    query = (
        select(Section)
        .join(Act, Act.id == Section.act_id)
        .where(
            Act.slug == NSW_ACT_SLUG,
            Section.section_no.in_(NSW_ACT_DUTIES.values()),
            Section.valid_from <= as_at,
            (Section.valid_to.is_(None)) | (Section.valid_to > as_at),
        )
    )
    sections = (await session.execute(query)).scalars().all()
    return {s.section_no: s.id for s in sections}


def _append_note(summary: str, note: str | None) -> str:
    """Append the VIC form-selection caveat (if any) to a finding's summary."""
    return f"{summary} ({note})" if note else summary


_OUTCOME_VERDICTS = {
    "covered": "green",
    "missing": "red",
    "altered_adverse": "red",
    "uncertain": "yellow",
}
_QUOTE_REQUIRED = {"covered", "altered_adverse"}


async def run_standard_form(
    judge: JudgeFn,
    session: AsyncSession,
    doc: DocumentInput,
    as_at: date,
    jurisdiction: str,
    lease: ClauseLeaseInput | None,
) -> list[ClauseFinding]:
    """Screen prescribed terms deterministically, then judge the residual in batches.

    The judge assigns each residual term one of four outcomes: covered (a
    term to that effect is present), missing (no term covers it),
    altered_adverse (a corresponding term exists but departs from the
    prescribed text against the tenant), or uncertain. covered and
    altered_adverse must carry a lease_quote that verifies against the
    document text; a missing or unverifiable quote downgrades the finding
    to yellow (skipped on the PDF path, where there is no document text to
    verify against). Residual terms are judged BATCH_SIZE at a time so each
    structured-output call's rule_id enum stays small.
    """
    terms, note = await fetch_form_terms(session, jurisdiction, as_at, lease)
    act_ids = await _act_duty_section_ids(session, as_at) if jurisdiction != "VIC" else {}
    findings: list[ClauseFinding] = []

    if doc.text is not None:
        green, residual = screen_terms(terms, doc.text)
    else:
        green, residual = [], list(terms)
    for term, ratio in green:
        findings.append(
            ClauseFinding(
                rule_id=term.rule_id,
                verdict="green",
                summary=_append_note(f"Prescribed term present (containment {ratio:.2f}).", note),
                evidence={"method": "verbatim", "containment": round(ratio, 3)},
                citations=_citations(term, as_at, act_ids),
            )
        )

    # Release the read-only transaction so it does not idle across the model await.
    await session.commit()
    for start in range(0, len(residual), BATCH_SIZE):
        batch = residual[start : start + BATCH_SIZE]
        batch_no = start // BATCH_SIZE + 1
        instruction = standard_form_instruction(as_at, batch)
        output_model = standard_form_output_model(
            [t.rule_id for t in batch], name=f"StandardFormOutput{batch_no}"
        )
        result = await judge(doc, instruction, output_model)
        by_id = {str(item.rule_id): item for item in result.items}
        for term in batch:
            item = by_id.get(term.rule_id)
            citations = _citations(term, as_at, act_ids)
            if item is None:
                findings.append(
                    ClauseFinding(
                        rule_id=term.rule_id,
                        verdict="yellow",
                        summary=_append_note("The model did not report on this term.", note),
                        citations=citations,
                    )
                )
                continue
            verdict = _OUTCOME_VERDICTS[item.outcome]
            summary = item.reasoning
            quote = item.lease_quote
            if item.outcome in _QUOTE_REQUIRED and doc.text is not None:
                if quote is None:
                    verdict = "yellow"
                    summary = f"Downgraded: {item.outcome} outcome carried no quote."
                elif not quote_matches(quote, doc.text):
                    verdict, summary = (
                        "yellow",
                        "Downgraded: quoted text was not found in the document.",
                    )
            if item.outcome == "altered_adverse" and item.departure:
                summary = f"{summary} Departure: {item.departure}"
            summary = _append_note(summary, note)
            findings.append(
                ClauseFinding(
                    rule_id=term.rule_id,
                    verdict=verdict,
                    summary=summary,
                    evidence={"outcome": item.outcome, "reasoning": item.reasoning},
                    citations=citations,
                    clause_quote=quote,
                )
            )
    return findings
