# Standard-Form Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The clause audit's mandatory family becomes a true standard-form comparison: every prescribed term of the governing form (NSW Sch 1 standard form, VIC Form 1/Form 2) is checked for coverage and adverse alteration, with a deterministic verbatim screen ahead of the LLM, per-term eval gates, and human-readable citation labels.

**Architecture:** A new `app/clause_audit/standard_form.py` module fetches the governing form's terms point-in-time, screens verbatim terms green with a shingle-containment check (zero LLM cost), and batches the residual 8 terms per LLM call through the existing judge plumbing; `run_standard_form` replaces `run_mandatory` in the processor for both jurisdictions. A new `app/citations.py` formatter renders S-keys as human labels on every citation. Goldens are generated from the corpus itself; gates are per rule_id.

**Tech Stack:** FastAPI + async SQLAlchemy 2.0 + PostgreSQL corpus, existing LLM plumbing (`app/llm/`), pytest with `llm_eval` marker, stdlib-only text screening.

## Global Constraints

- `uv` only: `uv run ...`, `uv add ...` - never python3/pip. No new dependencies in this plan.
- TDD per step: write the failing test, watch it fail for the right reason, implement, watch it pass.
- Ruff sequence before every push, exact order: `uv run ruff format .` then `uv run ruff check --fix .` then `uv run ruff check .` then `uv run ruff format --check .`
- Commit trailer exactly: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`; push `origin main`; poll the CI run to conclusion matching by headSha (never `-L1` after a sleep).
- No emojis anywhere. Docstrings over comments. Short modules and functions. No defensive programming.
- Screen: 8-token shingles, containment threshold 0.9, terms under 12 normalised tokens (or prescribed body under 12 tokens) always go to the LLM.
- LLM batching: 8 residual terms per call. Model comes from `settings` as today (`CLAUSE_AUDIT_MODEL` env); do not hardcode a model id.
- Eval gates: per rule_id precision >= 0.9 and recall >= 0.8. LLM evals carry `pytestmark = pytest.mark.llm_eval`.
- Every finding carries citations with `as_at`; product copy stays general information, not legal advice.
- rule_id shapes: `nsw.clause.sf_t{n}`, `vic.clause.sf_f1_t{n}`, `vic.clause.sf_f2_t{n}` - `{n}` is the term number lowercased (`sf_t30a`).
- Corpus slugs: NSW Regulation `sl-2019-0629` (form terms `S1-T*`), VIC Regulations `residential-tenancies-regulations-2021` (form terms `S1-F1-T*` / `S1-F2-T*`).

---

### Task 1: Citation formatter and label plumb-through

**Files:**
- Create: `app/citations.py`
- Modify: `app/rules/base.py` (Citation model)
- Modify: `app/clause_audit/rules.py` (`resolve_rule` populates the label)
- Test: `tests/test_citations.py` (new), `tests/test_clause_rules.py` (extend)

**Interfaces:**
- Produces: `format_citation(section_no: str) -> str` in `app/citations.py`.
- Produces: `Citation` gains `label: str | None = None` (pydantic field, default None - stored findings from before this change still validate).
- Later tasks rely on: `resolve_rule(...)` returning `Citation` with `label` set; Task 3 builds term citations with `label=format_citation(term.section_no)`.

- [ ] **Step 1: Write the failing formatter tests**

```python
# tests/test_citations.py
"""The formatter derives the label from the section_no shape alone, so the
NSW/VIC part-division asymmetry never leaks to callers."""

from app.citations import format_citation


def test_plain_section_numbers():
    assert format_citation("52") == "s 52"
    assert format_citation("27B") == "s 27B"


def test_schedule_clauses():
    assert format_citation("S1-1") == "Sch 1 cl 1"
    assert format_citation("S1A-2") == "Sch 1A cl 2"
    assert format_citation("S4-1") == "Sch 4 cl 1"


def test_nsw_standard_form_terms():
    assert format_citation("S1-T5") == "Sch 1 term 5"
    assert format_citation("S1-T30A") == "Sch 1 term 30A"


def test_vic_form_terms():
    assert format_citation("S1-F1-T5") == "Sch 1 Form 1 term 5"
    assert format_citation("S1-F19-T7") == "Sch 1 Form 19 term 7"
    assert format_citation("S1-F16A-T3") == "Sch 1 Form 16A term 3"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_citations.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.citations'`

- [ ] **Step 3: Implement the formatter**

```python
# app/citations.py
"""Human-readable labels for corpus section keys.

The label derives from the section_no shape alone: plain numbers are Act or
Regulation sections, "S{sch}-{cl}" is a schedule clause, "S{sch}-T{n}" is an
NSW standard-form term, and "S{sch}-F{form}-T{n}" is a VIC prescribed-form
term. Jurisdictional part/division conventions never enter the label.
"""

import re

_FORM_TERM = re.compile(r"^S(\w+)-F(\w+)-T(\w+)$")
_SCHEDULE_TERM = re.compile(r"^S(\w+)-T(\w+)$")
_SCHEDULE_CLAUSE = re.compile(r"^S(\w+)-(\w+)$")


def format_citation(section_no: str) -> str:
    match = _FORM_TERM.match(section_no)
    if match:
        return f"Sch {match.group(1)} Form {match.group(2)} term {match.group(3)}"
    match = _SCHEDULE_TERM.match(section_no)
    if match:
        return f"Sch {match.group(1)} term {match.group(2)}"
    match = _SCHEDULE_CLAUSE.match(section_no)
    if match:
        return f"Sch {match.group(1)} cl {match.group(2)}"
    return f"s {section_no}"
```

Note the ordering: the form-term pattern must be tried before the schedule-term
pattern, and the two-part schedule-clause pattern last, because each earlier
shape is a special case of a later one. `S1-T5` must not parse as schedule
clause "T5".

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_citations.py -q`
Expected: 4 passed

- [ ] **Step 5: Add the label field and populate it in resolve_rule**

In `app/rules/base.py`, extend the Citation model:

```python
class Citation(BaseModel):
    act: str
    section_no: str
    as_at: date
    section_id: uuid.UUID
    label: str | None = None
```

In `app/clause_audit/rules.py`, find `resolve_rule` (it builds the Citation
from `section_at`) and set the label. Read the existing function first; the
change is one argument:

```python
from app.citations import format_citation
```

and in the Citation construction inside `resolve_rule`, add
`label=format_citation(rule.ref.section_no)`.

Add to `tests/test_clause_rules.py` (follow the file's existing fixture
style for a session and as_at - read the file first):

```python
async def test_resolve_rule_sets_citation_label(session):
    from app.clause_audit.rules import PROHIBITED_RULES, resolve_rule

    citation = await resolve_rule(session, PROHIBITED_RULES[0], date(2026, 7, 28))
    assert citation is not None
    assert citation.label == "s 19"
```

- [ ] **Step 6: Run the touched test files**

Run: `uv run pytest tests/test_citations.py tests/test_clause_rules.py -q`
Expected: all pass

- [ ] **Step 7: Full suite, ruff sequence, commit, push**

Run: `uv run pytest -q` (expect green; existing serialized findings tests are
unaffected because `label` defaults to None), then the ruff sequence, then:

```bash
git add app/citations.py app/rules/base.py app/clause_audit/rules.py tests/test_citations.py tests/test_clause_rules.py
git commit -m "Add citation labels derived from section key shapes"
git push origin main
```

Poll CI to conclusion by headSha.

---

### Task 2: Term source, deterministic screen, and Act-duty map

**Files:**
- Create: `app/clause_audit/standard_form.py` (term fetch + screen + duty map; the runner arrives in Task 3)
- Test: `tests/test_standard_form.py` (new)

**Interfaces:**
- Consumes: `format_citation` from Task 1; `section_at`-style corpus access via SQLAlchemy (this task queries the sections table directly, point-in-time).
- Produces, all in `app/clause_audit/standard_form.py`:
  - `@dataclass(frozen=True) FormTerm: rule_id: str; section_no: str; heading: str; body: str; section_id: uuid.UUID; act_slug: str; act_duty: str | None` (`act_duty` is the mapped Act section_no or None)
  - `async fetch_form_terms(session, jurisdiction: str, as_at: date, lease: ClauseLeaseInput | None) -> tuple[list[FormTerm], str | None]` - returns the terms and an optional note ("form selection defaulted to Form 1: lease term length unknown")
  - `normalize(text: str) -> str`
  - `containment(term_text: str, document_text: str) -> float`
  - `screen_terms(terms: list[FormTerm], document_text: str) -> tuple[list[tuple[FormTerm, float]], list[FormTerm]]` - (screened-green with ratio, residual)
  - `NSW_ACT_DUTIES: dict[str, str]` mapping NSW term number -> Act section_no
  - Constants: `SHINGLE_TOKENS = 8`, `CONTAINMENT_THRESHOLD = 0.9`, `MIN_SCREEN_TOKENS = 12`

- [ ] **Step 1: Pin the NSW Act-duty map with corpus evidence**

The six retired rules cited Act `act-2010-042` ss 33, 50, 51, 52, 63, 70.
Candidate standard-form terms by heading: rent -> T3 or T4; quiet enjoyment
-> T15; use of premises -> T16, T17 or T18; habitability and repairs -> T19
(LANDLORD'S GENERAL OBLIGATIONS covers both duties); locks -> T32, T33 or
T34. Probe the current bodies and pick the single term whose text states the
duty (run from the repo root; dev corpus):

```bash
uv run python - <<'EOF'
import asyncio

from sqlalchemy import text

from app.core.db import async_session_factory

CANDIDATES = ["S1-T3", "S1-T4", "S1-T15", "S1-T16", "S1-T17", "S1-T18",
              "S1-T19", "S1-T32", "S1-T33", "S1-T34"]


async def main() -> None:
    async with async_session_factory() as session:
        for no in CANDIDATES:
            body = (
                await session.execute(
                    text(
                        "select s.body_text from sections s join acts a on a.id=s.act_id "
                        "where a.slug='sl-2019-0629' and s.section_no=:no and s.valid_to is null"
                    ),
                    {"no": no},
                )
            ).scalar_one()
            print(f"=== {no}\n{body[:400]}\n")


asyncio.run(main())
EOF
```

Record the chosen mapping in `NSW_ACT_DUTIES` with term numbers as keys and
Act section numbers as values, e.g. (verify each against the probe output
before committing; adjust if the text says otherwise):

```python
NSW_ACT_DUTIES = {
    "3": "33",  # tenant must pay rent
    "15": "50",  # quiet enjoyment
    "16": "51",  # use of premises
    "19": "52",  # premises reasonably clean and fit for habitation
    "20": "63",  # repairs (if T19 carries repair, key that instead - probe decides)
    "32": "70",  # locks and security devices
}
```

The docstring of `NSW_ACT_DUTIES` must state the mapping was probe-verified
and give the date. s 52 and s 63 may both land on T19; two map entries may
share a term number only if the probe shows both duties in one term - in that
case use `dict[str, str]` keyed by term with comma-joined sections? No: keep
one Act section per term; if both duties sit in T19, map `"19": "52"` and
`"20": "63"` only when T20's text carries the repair duty, otherwise drop the
s 63 row and note in the docstring that T19's citation carries s 52 and the
repair duty had no distinct term. Dual-citation output (Task 3) reads this map.

- [ ] **Step 2: Write the failing tests for normalize/containment/screen**

```python
# tests/test_standard_form.py
"""Deterministic layer of the standard-form comparison: no DB, no LLM."""

import uuid
from datetime import date

from app.clause_audit.standard_form import (
    CONTAINMENT_THRESHOLD,
    FormTerm,
    containment,
    normalize,
    screen_terms,
)


def make_term(no: str, heading: str, body: str) -> FormTerm:
    return FormTerm(
        rule_id=f"nsw.clause.sf_t{no.lower()}",
        section_no=f"S1-T{no}",
        heading=heading,
        body=body,
        section_id=uuid.uuid4(),
        act_slug="sl-2019-0629",
        act_duty=None,
    )


def test_normalize_strips_placeholders_and_unifies_punctuation():
    raw = "The tenant agrees—to pay rent of [insert amount] “on time” *weekly *fortnightly"
    cleaned = normalize(raw)
    assert "[insert" not in cleaned
    assert "—" not in cleaned and "“" not in cleaned
    assert "*" not in cleaned
    assert cleaned == cleaned.lower()


def test_containment_full_copy_is_high_and_reordering_immune():
    term = (
        "The landlord agrees to provide the residential premises in a "
        "reasonable state of cleanliness and fit for habitation by the tenant."
    )
    lease = (
        "CLAUSE 40. Unrelated preamble text here. "
        + term
        + " CLAUSE 41. More unrelated text follows the copied term."
    )
    assert containment(term, lease) >= CONTAINMENT_THRESHOLD


def test_containment_drops_on_alteration():
    term = (
        "The landlord agrees to give the tenant at least 7 days written "
        "notice before entering the premises for a routine inspection of "
        "the premises during the tenancy period."
    )
    altered = term.replace("7 days", "no")
    assert containment(term, altered) < 1.0


def test_screen_partitions_verbatim_from_residual_and_short_terms():
    long_body = (
        "The tenant agrees to pay the rent on time and in the manner "
        "stated in this agreement for the duration of the tenancy period."
    )
    copied = make_term("1", "RENT", long_body)
    missing = make_term(
        "2",
        "POSSESSION",
        (
            "The landlord agrees to give the tenant vacant possession of the "
            "premises on the day the tenant is entitled to enter into occupation."
        ),
    )
    short = make_term("3", "TERMINATION", "See the Act.")
    document = f"1. {long_body} 2. Something entirely different about parking."
    green, residual = screen_terms([copied, missing, short], document)
    assert [t.section_no for t, _ in green] == ["S1-T1"]
    assert green[0][1] >= CONTAINMENT_THRESHOLD
    assert [t.section_no for t in residual] == ["S1-T2", "S1-T3"]
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_standard_form.py -q`
Expected: FAIL with `ModuleNotFoundError` / `ImportError` on `standard_form`

- [ ] **Step 4: Implement the module (fetch + screen + map)**

```python
# app/clause_audit/standard_form.py
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

from sqlalchemy import text as sql
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.clause_audit import ClauseLeaseInput

SHINGLE_TOKENS = 8
CONTAINMENT_THRESHOLD = 0.9
MIN_SCREEN_TOKENS = 12

NSW_REG_SLUG = "sl-2019-0629"
VIC_REGS_SLUG = "residential-tenancies-regulations-2021"

NSW_ACT_DUTIES = {
    # Probe-verified against the current corpus on YYYY-MM-DD (Task 2 Step 1).
    "3": "33",
    "15": "50",
    "16": "51",
    "19": "52",
    "20": "63",
    "32": "70",
}
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
    cleaned = _PLACEHOLDER_RE.sub(" ", text)
    cleaned = _STAR_OPTION_RE.sub(" ", cleaned)
    cleaned = (
        cleaned.replace("‘", "'")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("—", "-")
        .replace("–", "-")
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
    green: list[tuple[FormTerm, float]] = []
    residual: list[FormTerm] = []
    for term in terms:
        full = f"{term.heading} {term.body}"
        if len(_tokens(full)) < MIN_SCREEN_TOKENS or len(_tokens(term.body)) < MIN_SCREEN_TOKENS:
            residual.append(term)
            continue
        ratio = containment(full, document_text)
        if ratio >= CONTAINMENT_THRESHOLD:
            green.append((term, ratio))
        else:
            residual.append(term)
    return green, residual


def _term_no(section_no: str) -> str:
    return section_no.rsplit("-T", 1)[1]


def _vic_form(lease: ClauseLeaseInput | None) -> tuple[str, str | None]:
    """Form 2 for fixed terms over 5 years, else Form 1 (noting unknowns)."""
    if lease is None or lease.start_date is None or lease.end_date is None:
        return "1", "form selection defaulted to Form 1: lease term length unknown"
    from app.rules.base import add_months

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
    rows = (
        await session.execute(
            sql(
                "select s.id, s.section_no, s.heading, s.body_text from sections s "
                "join acts a on a.id=s.act_id where a.slug=:slug "
                "and s.section_no like :pattern and s.section_no not like :deeper "
                "and s.valid_from <= :as_at "
                "and (s.valid_to is null or s.valid_to > :as_at)"
            ),
            {"slug": slug, "pattern": pattern, "deeper": pattern + "-%", "as_at": as_at},
        )
    ).all()
    terms = [
        FormTerm(
            rule_id=f"{rule_prefix}{_term_no(no).lower()}",
            section_no=no,
            heading=heading,
            body=body,
            section_id=section_id,
            act_slug=slug,
            act_duty=(NSW_ACT_DUTIES.get(_term_no(no)) if jurisdiction != "VIC" else None),
        )
        for section_id, no, heading, body in rows
    ]
    terms.sort(key=lambda t: (len(_term_no(t.section_no)), _term_no(t.section_no)))
    return terms, note
```

Notes for the implementer:
- `add_months` already exists in `app/rules/base.py` (calendar-interval
  helper); 60 months = 5 years keeps the comparison calendar-correct.
- The `not like :deeper` guard excludes nothing today (term keys are leaves)
  but keeps NSW `S1-T%` from ever matching a future deeper key shape; if it
  reads as overengineering, drop it and rely on the leaf shape - your call,
  note the choice in the report.
- The VIC pattern `S1-F1-T%` must not match `S1-F16A-T%`: `S1-F1-T%` also
  matches nothing else because the form segment is delimited by `-T`.
  Verify with the Step 6 DB test which asserts exact counts.

- [ ] **Step 5: Run the unit tests**

Run: `uv run pytest tests/test_standard_form.py -q`
Expected: all pass

- [ ] **Step 6: Add the DB-backed fetch tests**

Append to `tests/test_standard_form.py` (the suite's DB fixtures: read
`tests/test_clause_rules.py` first and reuse its session fixture pattern):

```python
import pytest

from app.clause_audit.standard_form import fetch_form_terms
from app.schemas.clause_audit import ClauseLeaseInput


async def test_fetch_nsw_terms_today(session):
    terms, note = await fetch_form_terms(session, "NSW", date(2026, 8, 9), None)
    assert len(terms) == 59
    assert note is None
    assert terms[0].rule_id == "nsw.clause.sf_t1"
    assert terms[0].section_no == "S1-T1"
    by_no = {t.section_no: t for t in terms}
    assert by_no["S1-T19"].act_duty == "52"
    assert by_no["S1-T5"].act_duty is None


async def test_fetch_vic_form1_default_and_note(session):
    terms, note = await fetch_form_terms(session, "VIC", date(2026, 8, 9), None)
    assert len(terms) == 32
    assert terms[0].rule_id == "vic.clause.sf_f1_t1"
    assert note is not None and "Form 1" in note


async def test_fetch_vic_form2_for_long_lease(session):
    lease = ClauseLeaseInput(start_date=date(2020, 1, 1), end_date=date(2026, 1, 2))
    terms, note = await fetch_form_terms(session, "VIC", date(2026, 8, 9), lease)
    assert len(terms) == 40
    assert terms[0].rule_id == "vic.clause.sf_f2_t1"
    assert note is None


async def test_fetch_is_point_in_time(session):
    terms, _ = await fetch_form_terms(session, "VIC", date(2025, 11, 24), None)
    nos = {t.section_no for t in terms}
    assert "S1-F1-T30A" not in nos
    terms, _ = await fetch_form_terms(session, "VIC", date(2025, 11, 25), None)
    nos = {t.section_no for t in terms}
    assert "S1-F1-T30A" in nos
```

Adjust `ClauseLeaseInput` field names to the real schema (read
`app/schemas/clause_audit.py`); if start/end dates are named differently,
use the real names in `_vic_form` too.

- [ ] **Step 7: Run, then full suite, ruff, commit, push**

Run: `uv run pytest tests/test_standard_form.py -q` then `uv run pytest -q`,
ruff sequence, commit `Add standard-form term source and deterministic screen`,
push, poll CI by headSha.

---

### Task 3: Prompts, output model, and the standard-form runner

**Files:**
- Modify: `app/llm/prompts.py` (add `STANDARD_FORM_GUIDANCE` + `standard_form_instruction`)
- Modify: `app/llm/schemas.py` (add `standard_form_output_model`)
- Modify: `app/clause_audit/standard_form.py` (add `run_standard_form`)
- Test: `tests/test_standard_form.py` (extend with fake-judge runner tests)

**Interfaces:**
- Consumes: Task 2's `fetch_form_terms`, `screen_terms`, `FormTerm`, `NSW_ACT_SLUG`; Task 1's `format_citation`; existing `JudgeFn`, `DocumentInput`, `ClauseFinding`, `Citation`, `quote_matches`.
- Produces: `async run_standard_form(judge, session, doc, as_at, jurisdiction, lease) -> list[ClauseFinding]` - the processor (Task 4) calls exactly this signature.
- Produces: `standard_form_output_model(rule_ids: list[str]) -> type[BaseModel]` whose items have `rule_id: str`, `outcome: Literal["covered", "missing", "altered_adverse", "uncertain"]`, `reasoning: str`, `lease_quote: str | None`, `departure: str | None`.

- [ ] **Step 1: Write the failing prompt and output-model tests**

Append to `tests/test_standard_form.py`:

```python
from app.llm.prompts import STANDARD_FORM_GUIDANCE, standard_form_instruction
from app.llm.schemas import standard_form_output_model


def test_standard_form_instruction_contains_terms_and_rubric():
    terms = [
        make_term("1", "RENT", "The tenant agrees to pay rent."),
        make_term("2", "POSSESSION", "Vacant possession on entry."),
    ]
    instruction = standard_form_instruction(date(2026, 8, 9), terms)
    assert "S1-T1" in instruction and "RENT" in instruction
    assert "nsw.clause.sf_t1" in instruction and "nsw.clause.sf_t2" in instruction
    assert "covered" in instruction and "altered_adverse" in instruction
    assert STANDARD_FORM_GUIDANCE in instruction


def test_standard_form_output_model_validates_outcomes():
    model = standard_form_output_model(["nsw.clause.sf_t1"])
    parsed = model.model_validate(
        {
            "items": [
                {
                    "rule_id": "nsw.clause.sf_t1",
                    "outcome": "missing",
                    "reasoning": "not found",
                    "lease_quote": None,
                    "departure": None,
                }
            ]
        }
    )
    assert parsed.items[0].outcome == "missing"
```

Run: `uv run pytest tests/test_standard_form.py -q` - expect ImportError.

- [ ] **Step 2: Implement guidance, instruction, output model**

In `app/llm/prompts.py`, delete `MANDATORY_GUIDANCE` (Task 4 removes its
users; do the delete there if the suite would break here - keep this commit
green) and add:

```python
STANDARD_FORM_GUIDANCE = (
    "For each prescribed term, outcome covered means the document contains a "
    "term to that specific effect (quote it verbatim in lease_quote); missing "
    "means no term in the document covers it; altered_adverse means a "
    "corresponding term exists but departs from the prescribed text in a way "
    "that disadvantages the tenant (quote the document's term in lease_quote "
    "and state the departure in departure); uncertain means you cannot tell - "
    "prefer uncertain over guessing. Judge substance, not wording: a "
    "faithful paraphrase is covered. A related but different clause does not "
    "cover a different term."
)


def standard_form_instruction(as_at: date, terms) -> str:
    parts = [
        "Check family: standard form comparison. Compare the document against "
        f"each prescribed term of the standard form in force at {as_at.isoformat()}. "
        f"Return exactly one item per rule_id. {STANDARD_FORM_GUIDANCE}"
    ]
    for term in terms:
        parts.append(f"- {term.rule_id} ({term.section_no} {term.heading}):\n{term.body}")
    return "\n\n".join(parts)
```

In `app/llm/schemas.py`, next to `family_output_model` (read it first and
follow its construction pattern exactly - it builds a pydantic model with an
`items` list and a Literal over rule_ids):

```python
def standard_form_output_model(rule_ids: list[str]) -> type[BaseModel]:
    item = create_model(
        "StandardFormItem",
        rule_id=(Literal[tuple(rule_ids)], ...),
        outcome=(Literal["covered", "missing", "altered_adverse", "uncertain"], ...),
        reasoning=(str, ...),
        lease_quote=(str | None, None),
        departure=(str | None, None),
    )
    return create_model("StandardFormOutput", items=(list[item], ...))
```

Match the real `family_output_model` mechanics (if it uses a different
Literal/enum construction, mirror it).

- [ ] **Step 3: Run the two tests to green**

Run: `uv run pytest tests/test_standard_form.py -q`

- [ ] **Step 4: Write the failing runner tests (fake judge)**

Read `tests/test_clause_families.py` first for the fake-judge convention
(a `JudgeFn`-shaped async callable returning constructed output models).
Then append:

```python
from app.clause_audit.standard_form import run_standard_form
from app.clause_audit.document import DocumentInput


def fake_judge(outcomes: dict[str, dict]):
    async def judge(doc, instruction, output_model):
        items = []
        for rule_id, payload in outcomes.items():
            if rule_id in instruction:
                items.append({"rule_id": rule_id, **payload})
        return output_model.model_validate({"items": items})

    return judge


async def test_runner_screens_verbatim_and_judges_residual(session):
    terms, _ = await fetch_form_terms(session, "NSW", date(2026, 8, 9), None)
    t1 = terms[0]
    doc = DocumentInput(kind="text", text=f"1. {t1.heading} {t1.body} Nothing else.")
    judge = fake_judge(
        {
            t.rule_id: {
                "outcome": "missing",
                "reasoning": "absent",
                "lease_quote": None,
                "departure": None,
            }
            for t in terms[1:]
        }
    )
    findings = await run_standard_form(judge, session, doc, date(2026, 8, 9), "NSW", None)
    by_id = {f.rule_id: f for f in findings}
    assert len(findings) == 59
    assert by_id[t1.rule_id].verdict == "green"
    assert by_id[t1.rule_id].evidence["method"] == "verbatim"
    assert by_id[terms[1].rule_id].verdict == "red"
    assert by_id[terms[1].rule_id].evidence["outcome"] == "missing"
    assert all(f.citations and f.citations[0].label for f in findings)


async def test_runner_dual_citation_for_act_duties(session):
    doc = DocumentInput(kind="text", text="An empty lease.")
    judge = fake_judge({})
    findings = await run_standard_form(judge, session, doc, date(2026, 8, 9), "NSW", None)
    t19 = next(f for f in findings if f.rule_id == "nsw.clause.sf_t19")
    assert [c.section_no for c in t19.citations] == ["S1-T19", "52"]
    assert t19.citations[1].act == "act-2010-042"


async def test_runner_altered_and_uncertain_and_quote_downgrade(session):
    terms, _ = await fetch_form_terms(session, "VIC", date(2026, 8, 9), None)
    doc = DocumentInput(kind="text", text="A bespoke lease with its own words.")
    first, second, third = terms[0], terms[1], terms[2]
    judge = fake_judge(
        {
            first.rule_id: {
                "outcome": "altered_adverse",
                "reasoning": "notice cut",
                "lease_quote": "words not in the document",
                "departure": "notice period shortened",
            },
            second.rule_id: {
                "outcome": "uncertain",
                "reasoning": "cannot tell",
                "lease_quote": None,
                "departure": None,
            },
            third.rule_id: {
                "outcome": "covered",
                "reasoning": "found",
                "lease_quote": "own words",
                "departure": None,
            },
        }
    )
    findings = await run_standard_form(judge, session, doc, date(2026, 8, 9), "VIC", None)
    by_id = {f.rule_id: f for f in findings}
    assert by_id[first.rule_id].verdict == "yellow"
    assert "not found" in by_id[first.rule_id].summary
    assert by_id[second.rule_id].verdict == "yellow"
    assert by_id[third.rule_id].verdict == "green"
    assert by_id[third.rule_id].clause_quote == "own words"


async def test_runner_pdf_document_skips_screen(session):
    doc = DocumentInput(kind="pdf", pdf=b"%PDF-fake")
    terms, _ = await fetch_form_terms(session, "VIC", date(2026, 8, 9), None)
    judge = fake_judge(
        {
            t.rule_id: {
                "outcome": "covered",
                "reasoning": "in the pdf",
                "lease_quote": None,
                "departure": None,
            }
            for t in terms
        }
    )
    findings = await run_standard_form(judge, session, doc, date(2026, 8, 9), "VIC", None)
    assert all(f.verdict in {"green", "yellow"} for f in findings)
    assert not any(f.evidence.get("method") == "verbatim" for f in findings)
```

Behavioural notes encoded above, implement to match:
- covered with a quote that does NOT appear in the text -> stays green only
  if `quote_matches` passes; covered without a quote -> yellow (mirror of the
  mandatory family's green-quote discipline). In the pdf case `doc.text` is
  None: quote verification is skipped (nothing to match against), quotes are
  kept as reported.
- altered_adverse requires a lease_quote that matches the document; a failed
  match downgrades to yellow with a "not found" summary. missing requires no
  quote.
- The screen only runs when `doc.text` is not None.

Run: expect failures on missing `run_standard_form`.

- [ ] **Step 5: Implement run_standard_form**

Append to `app/clause_audit/standard_form.py`:

```python
from app.citations import format_citation
from app.clause_audit.document import DocumentInput
from app.clause_audit.verify import quote_matches
from app.llm.client import JudgeFn
from app.llm.prompts import standard_form_instruction
from app.llm.schemas import standard_form_output_model
from app.rules.base import Citation
from app.schemas.clause_audit import ClauseFinding

BATCH_SIZE = 8


def _citations(
    term: FormTerm, as_at: date, act_section_ids: dict[str, uuid.UUID]
) -> list[Citation]:
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
    rows = (
        await session.execute(
            sql(
                "select s.section_no, s.id from sections s join acts a on a.id=s.act_id "
                "where a.slug=:slug and s.section_no = any(:nos) "
                "and s.valid_from <= :as_at and (s.valid_to is null or s.valid_to > :as_at)"
            ),
            {"slug": NSW_ACT_SLUG, "nos": list(NSW_ACT_DUTIES.values()), "as_at": as_at},
        )
    ).all()
    return dict(rows)


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
                summary="Prescribed term present verbatim." + (f" ({note})" if note else ""),
                evidence={"method": "verbatim", "containment": round(ratio, 3)},
                citations=_citations(term, as_at, act_ids),
            )
        )

    await session.commit()
    for start in range(0, len(residual), BATCH_SIZE):
        batch = residual[start : start + BATCH_SIZE]
        instruction = standard_form_instruction(as_at, batch)
        output_model = standard_form_output_model([t.rule_id for t in batch])
        result = await judge(doc, instruction, output_model)
        by_id = {item.rule_id: item for item in result.items}
        for term in batch:
            item = by_id.get(term.rule_id)
            citations = _citations(term, as_at, act_ids)
            if item is None:
                findings.append(
                    ClauseFinding(
                        rule_id=term.rule_id,
                        verdict="yellow",
                        summary="The model did not report on this term.",
                        citations=citations,
                    )
                )
                continue
            verdict = _OUTCOME_VERDICTS[item.outcome]
            summary = item.reasoning
            quote = item.lease_quote
            if item.outcome in _QUOTE_REQUIRED and doc.text is not None:
                if quote is None:
                    verdict, summary = "yellow", f"Downgraded: {item.outcome} carried no quote."
                elif not quote_matches(quote, doc.text):
                    verdict, summary = (
                        "yellow",
                        "Downgraded: quoted text was not found in the document.",
                    )
            if item.outcome == "altered_adverse" and item.departure:
                summary = f"{summary} Departure: {item.departure}"
            if note:
                summary = f"{summary} ({note})"
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
```

Note: `await session.commit()` before the judge loop mirrors
`_run_clause_family`'s transaction-release comment; keep the comment style.
Check `ClauseFinding` field names against `app/schemas/clause_audit.py`
before writing (clause_quote, evidence, citations, skip_reason all exist for
the other families).

- [ ] **Step 6: Run to green, full suite, ruff, commit, push**

`uv run pytest tests/test_standard_form.py -q`, then `uv run pytest -q`,
ruff sequence, commit `Add the standard-form comparison runner`, push,
poll CI by headSha.

---

### Task 4: Processor wiring and mandatory retirement

**Files:**
- Modify: `app/clause_audit/processor.py` (dispatch)
- Modify: `app/clause_audit/families.py` (delete `run_mandatory`)
- Modify: `app/clause_audit/rules.py` (delete `MANDATORY_RULES`; narrow `ClauseRule.family` Literal to `"prohibited"`)
- Modify: `app/llm/prompts.py` (delete `MANDATORY_GUIDANCE` if still present)
- Test: `tests/test_clause_processor.py`, `tests/test_clause_families.py`, `tests/test_clause_rules.py`, `tests/test_golden.py` (update), plus every other reference `grep -rn "MANDATORY\|run_mandatory" app tests` finds

**Interfaces:**
- Consumes: `run_standard_form(judge, session, doc, as_at, jurisdiction, lease)` from Task 3.
- Produces: `process_job` runs prohibited + standard_form for BOTH jurisdictions (+ fields when lease present).

- [ ] **Step 1: Write the failing processor test**

Read `tests/test_clause_processor.py` first (it has a fake judge and job
fixtures). Add a test asserting a NSW job's findings contain
`nsw.clause.sf_t*` rule_ids and no `nsw.clause.states_rent_payment`, and a
VIC job's findings contain `vic.clause.sf_f1_t*` (VIC gets the family for
the first time):

```python
async def test_process_job_runs_standard_form_both_jurisdictions(session, ...):
    # follow the file's existing job-construction fixtures; assertions:
    nsw_ids = {f["rule_id"] for f in nsw_job.findings}
    assert any(r.startswith("nsw.clause.sf_t") for r in nsw_ids)
    assert "nsw.clause.states_rent_payment" not in nsw_ids
    vic_ids = {f["rule_id"] for f in vic_job.findings}
    assert any(r.startswith("vic.clause.sf_f1_t") for r in vic_ids)
```

Run: expect failure (no standard_form findings yet).

- [ ] **Step 2: Rewire process_job**

```python
# app/clause_audit/processor.py - the family block becomes:
from app.clause_audit.standard_form import run_standard_form

    lease = ClauseLeaseInput.model_validate(job.lease) if job.lease is not None else None
    if job.jurisdiction == "VIC":
        findings = await run_prohibited(
            judge, session, doc, job.as_at, rules_vic.VIC_PROHIBITED_RULES
        )
    else:
        findings = await run_prohibited(
            judge, session, doc, job.as_at, clause_rules.PROHIBITED_RULES
        )
    findings += await run_standard_form(
        judge, session, doc, job.as_at, job.jurisdiction, lease
    )
    discrepancies = []
    if lease is not None:
        discrepancies = await run_fields(judge, doc, lease)
```

(The `run_mandatory` import goes; `lease` is parsed once and reused.)

- [ ] **Step 3: Delete the retired code and update its tests**

- `app/clause_audit/families.py`: delete `run_mandatory` and the
  `MANDATORY_GUIDANCE` import.
- `app/clause_audit/rules.py`: delete the `MANDATORY_RULES` list and the
  module docstring's mandatory paragraph; narrow
  `family: Literal["prohibited", "mandatory"]` to `Literal["prohibited"]`.
- `app/llm/prompts.py`: delete `MANDATORY_GUIDANCE`.
- `grep -rn "MANDATORY\|run_mandatory\|mandatory" app tests docs/rule-candidates.md`
  and update every hit: `tests/test_clause_families.py` mandatory tests are
  deleted (their behaviours now live in Task 3's runner tests),
  `tests/test_golden.py` / `tests/golden/clauses.py` MANDATORY_CASES and
  their eval users are deleted (Task 5 replaces the eval), and
  `tests/test_llm_eval.py`'s mandatory eval function is deleted in the same
  commit so the file never references a missing symbol.
  `docs/rule-candidates.md` gains one line noting the six moved into the
  standard-form family with dual citations.

- [ ] **Step 4: Run to green, full suite, ruff, commit, push**

`uv run pytest -q` (the suite must be green with mandatory fully gone),
ruff sequence, commit `Replace the mandatory family with standard-form comparison`,
push, poll CI by headSha.

---

### Task 5: Corpus-driven goldens and per-term eval gates

**Files:**
- Create: `tests/golden/standard_form.py` (generator + alteration/paraphrase data + gates)
- Modify: `tests/test_llm_eval.py` (standard-form eval tests)
- Modify: `tests/golden/clauses_vic.py` (VIC enrichment carry-in)
- Modify: `tests/test_standard_form.py` (screen-calibration exact asserts, CI-free)

**Interfaces:**
- Consumes: `fetch_form_terms`, `screen_terms`, `normalize`, `run_standard_form`.
- Produces: `build_verbatim(terms) -> str`, `build_missing(terms, missing: set[str]) -> str`, `build_altered(terms, alterations: dict[str, str]) -> str` in `tests/golden/standard_form.py`; `ALTERATIONS: dict[str, str]` (rule_id -> altered text), `PARAPHRASES: dict[str, str]`, `SF_THRESHOLDS = {"precision": 0.9, "recall": 0.8}`.

- [ ] **Step 1: Implement the document builders (no LLM, TDD)**

`tests/golden/standard_form.py`:

```python
"""Corpus-driven golden documents for the standard-form eval.

Documents are assembled from the prescribed texts themselves; placeholders
are filled with fixture values so verbatim baselines screen green."""

import re

FILLERS = {
    "amount": "$550.00",
    "date": "1 March 2026",
    "name": "Alex Tenant",
    "address": "1 Example Street, Sydney NSW 2000",
}


def _fill(text: str) -> str:
    def repl(match: re.Match) -> str:
        inner = match.group(0).lower()
        if "amount" in inner or "$" in inner:
            return FILLERS["amount"]
        if "date" in inner:
            return FILLERS["date"]
        if "address" in inner:
            return FILLERS["address"]
        return FILLERS["name"]

    return re.sub(r"\[[^\]]*\]", repl, text).replace("*", "")


def render_term(term) -> str:
    return f"{term.section_no.rsplit('-T', 1)[1]}. {term.heading}\n{_fill(term.body)}"


def build_verbatim(terms) -> str:
    parts = ["RESIDENTIAL TENANCY AGREEMENT", "The parties agree as follows."]
    parts += [render_term(t) for t in terms]
    return "\n\n".join(parts)


def build_missing(terms, missing: set[str]) -> str:
    return build_verbatim([t for t in terms if t.rule_id not in missing])


def build_altered(terms, alterations: dict[str, str]) -> str:
    parts = ["RESIDENTIAL TENANCY AGREEMENT", "The parties agree as follows."]
    for t in terms:
        if t.rule_id in alterations:
            no = t.section_no.rsplit("-T", 1)[1]
            parts.append(f"{no}. {t.heading}\n{alterations[t.rule_id]}")
        else:
            parts.append(render_term(t))
    return "\n\n".join(parts)


SF_THRESHOLDS = {"precision": 0.9, "recall": 0.8}
```

Then the alteration data. Programmatic simple alterations cover most terms;
generate them once with a helper executed at authoring time and CURATE the
output by hand into the file (the file ships static data, not a generator
call, so evals are reproducible):

```python
def propose_alteration(body: str) -> str | None:
    """Authoring aid: crude adversarial edits - review before shipping."""
    for pattern, repl in [
        (r"\b(\d+) days\b", "24 hours"),
        (r"\bmust not\b", "may"),
        (r"\bthe landlord agrees\b", "the landlord may choose"),
        (r"\bat least\b", "at most"),
    ]:
        altered, n = re.subn(pattern, repl, body, count=1, flags=re.IGNORECASE)
        if n:
            return altered
    return None
```

`ALTERATIONS: dict[str, str]` must cover EVERY rule_id of NSW + VIC F1 + VIC
F2 (59 + 32 + 40 entries; a term whose text defeats every crude pattern gets
a hand-written adverse rewrite). `PARAPHRASES` covers at least the six
NSW Act-duty terms and VIC F1 terms 1-6: a faithful rewording that must stay
green.

Screen-calibration exact asserts (append to `tests/test_standard_form.py`,
runs in CI without LLM, needs the corpus DB like the Task 2 fetch tests):

```python
from tests.golden.standard_form import ALTERATIONS, build_altered, build_verbatim


async def test_verbatim_document_screens_all_screenable_terms_green(session):
    terms, _ = await fetch_form_terms(session, "NSW", date(2026, 8, 9), None)
    document = build_verbatim(terms)
    green, residual = screen_terms(terms, document)
    screenable = [
        t
        for t in terms
        if len(normalize(f"{t.heading} {t.body}").split()) >= 12
        and len(normalize(t.body).split()) >= 12
    ]
    assert {t.section_no for t, _ in green} == {t.section_no for t in screenable}


async def test_altered_terms_fall_out_of_the_screen(session):
    terms, _ = await fetch_form_terms(session, "NSW", date(2026, 8, 9), None)
    document = build_altered(terms, ALTERATIONS)
    green, _ = screen_terms(terms, document)
    altered = {t.section_no for t in terms if t.rule_id in ALTERATIONS}
    assert not ({t.section_no for t, _ in green} & altered)
```

An alteration that still screens green is a BAD alteration (too close to the
original): fix the alteration data, never weaken this assert. Zero altered
terms may screen green.

- [ ] **Step 2: Write the per-term eval**

Append to `tests/test_llm_eval.py` (mirror the existing per-rule
precision/recall table machinery; read the whole file first):

```python
async def test_standard_form_eval_nsw(eval_session, judge):
    terms, _ = await fetch_form_terms(eval_session, "NSW", AS_AT_SF, None)
    docs = plan_documents(terms)  # verbatim x2, missing matrix, altered matrix, paraphrases
    # For each document: run run_standard_form with the real judge, collect
    # per-rule_id (expected, actual) pairs, then assert per-rule precision
    # and recall against SF_THRESHOLDS, printing the per-term table first.
```

Write `plan_documents` in `tests/golden/standard_form.py` deterministically:
missing matrix = consecutive chunks of ~10 rule_ids so every term is missing
in exactly 2 documents (chunk i and a second pass offset by 5); altered
matrix likewise from `ALTERATIONS`; expected labels per document follow from
construction (missing -> red, altered -> red, everything else in a seeded
doc -> green, paraphrase docs -> green for the paraphrased terms). Keep the
scoring helper shared with the existing evals if one exists; otherwise a
20-line local tally. VIC: same test shape for Form 1 and Form 2 with
`AS_AT_SF = date(2026, 8, 9)`.

- [ ] **Step 3: VIC golden enrichment (carry-in)**

In `tests/golden/clauses_vic.py`: reword the `breach_penalty` question's
disambiguator per the backlog note, add the two giveaway `cleaningreq` red
cases, and add one extra red case per remaining VIC prohibited rule (recall
slack). Keep the existing case-tuple format (read the file first).

- [ ] **Step 4: Run the deterministic layer, then the paid eval**

Free: `uv run pytest tests/test_standard_form.py -q` - screen calibration
must be green before spending money.
Paid (needs `ANTHROPIC_API_KEY` in the environment and the dev corpus):

```bash
uv run pytest -m llm_eval tests/test_llm_eval.py -q
```

Iterate evidence-first on failures: read the per-term table, diagnose the
model's reasoning for each miss, adjust STANDARD_FORM_GUIDANCE wording or
the alteration/paraphrase data (never the thresholds), re-run. Every VIC
prohibited rule must still clear its existing gates after enrichment.
Record the final table in the task report.

- [ ] **Step 5: Full suite, ruff, commit, push**

Commit `Add corpus-driven standard-form goldens and per-term eval gates`,
push, poll CI by headSha (CI runs the free layer only; `llm_eval` stays
deselected there).

---

### Task 6: SaaS surfacing

Repo: `~/LLMProjects/rental_management_app` (work there; its own suite and
push discipline apply - read that repo's CLAUDE.md first).

**Files:**
- Modify: `frontend/src/app/app/leases/ClauseAuditSection.tsx`
- Modify: `frontend/src/lib/clauseAudit.ts` (types)
- Test: the repo's existing frontend test command (read `package.json` scripts; run what exists), plus the env-gated e2e touch below

**Interfaces:**
- Consumes: service findings whose rule_ids now include `nsw.clause.sf_t*` / `vic.clause.sf_f1_t*` / `vic.clause.sf_f2_t*`, each citation carrying optional `label`.

- [ ] **Step 1: Read the current section component and types**

Read `ClauseAuditSection.tsx` and `clauseAudit.ts` end to end. Establish:
how findings are grouped (by rule_id prefix or a family field), how
citations render today, and what an unknown rule_id prefix does (the answer
decides whether anything would have broken - record it in the report).

- [ ] **Step 2: Add the family label and citation labels**

- Grouping: findings with rule_ids matching `.clause.sf_` group under the
  label `標準表單比對` (the section's existing label language is Traditional
  Chinese - match the neighbouring family labels' exact style; if labels are
  English in code, use "Standard form comparison").
- Citations: where a citation renders, prefer `citation.label` when present,
  falling back to the current rendering. Extend the `Citation` type in
  `clauseAudit.ts` with `label?: string`.
- The retired mandatory labels/keys: grep the frontend for `mandatory` and
  remove or repoint every hit.

- [ ] **Step 3: e2e touch**

The repo has an env-gated live e2e for clause audits (from the tail
milestone). Extend its assertions minimally: a VIC clause audit result shows
at least one `sf_f` rule_id under the new family label. Run it only if its
env gate is configured locally; otherwise note "e2e deferred to rollout
acceptance" in the report.

- [ ] **Step 4: Repo gates, commit, push**

Run the SaaS repo's test/lint commands (per its CLAUDE.md), commit
`Show the standard-form comparison family with citation labels`, push, poll
that repo's CI by headSha.

---

### Task 7: Rollout, monitor-port split, and closure

Controller-inline task (production access, tunnels, launchd).

**Files:**
- Modify: `deploy/launchd/monitor-remote.sh` (port 15434)
- Modify: `~/Library/LaunchAgents/com.lease-monitor.plist` (DATABASE_URL port)
- Modify: `deploy/README.md` (runbook)

- [ ] **Step 1: Deploy the service**

Follow `deploy/README.md`: the CI publish job builds the image on the merge
commit; run the deploy script against production, verify `/health` and the
image sha match the pushed commit.

- [ ] **Step 2: Production acceptance**

With the SaaS tenant key (fetch pattern: `COMPLIANCE_API_KEY` from the SaaS
backend `.env`; delete any copies after): submit one NSW and one VIC text
clause audit against `https://api.leasekoala.com` using a lease assembled
from a handful of prescribed terms plus one deliberate omission, poll the
job to completion, and assert: `sf_` findings present, the omitted term is
red with outcome missing, at least one finding shows `citations[0].label`
of the `Sch 1 ...` shape, VIC shows the Form 1 default note when no lease
dates were sent. Then run one SaaS-side audit via the portal flow if the
env-gated e2e was deferred in Task 6.

- [ ] **Step 3: Monitor-port split**

- `deploy/launchd/monitor-remote.sh`: both `15433` occurrences become
  `15434` (`-L 15434:127.0.0.1:5432`).
- Plist: `DATABASE_URL` port `15433` -> `15434`, then
  `launchctl unload && launchctl load` (or bootout/bootstrap) to re-read it.
- `deploy/README.md`: the shared-port WARNING becomes a historical note; the
  monitor now owns 15434 exclusively and controller tunnels keep 15433.
- Verify end to end: `launchctl kickstart -k gui/$(id -u)/com.lease-monitor`
  with a controller tunnel deliberately open on 15433; the run must complete
  clean (fresh `monitor: checked=... changed=0` lines) while 15433 is held.
  Close the controller tunnel after: `pkill -f "15433:127.0.0.1:5432"`.

- [ ] **Step 4: Ledger, memory, tracker**

Ledger: Task-by-task completion is already recorded; add the rollout entry
(deploy sha, acceptance evidence, port split verification). Memory
(`milestone-roadmap.md`): milestone 4 complete - (c) shipped; note the six
dual-citation terms and that the eval wave included the VIC enrichment.

- [ ] **Step 5: Final whole-branch review**

Generate the package over the full (c) range in each touched repo
(`scripts/review-package <base> <head>`), dispatch the final reviewer on the
strongest available model with the ledger's Minor roll-up for triage, run
the fix loop to Yes, then report closure and stop for user approval.

---

## Self-review notes

- Spec coverage: pipeline (Tasks 2-4), screen (2), LLM residual (3),
  citations/formatter (1), retirement (4), per-term eval + VIC enrichment
  (5), SaaS (6), monitor-port + rollout (7). VIC Form 1 default note
  surfaces in finding summaries (Task 3) - the spec's "findings note the
  assumption".
- Type consistency: `run_standard_form(judge, session, doc, as_at,
  jurisdiction, lease)` is identical in Tasks 3 and 4;
  `fetch_form_terms(session, jurisdiction, as_at, lease)` identical in 2, 3
  and 5; `FormTerm` fields consistent throughout; `SF_THRESHOLDS` only in 5.
- Known judgment points left to implementers deliberately: exact
  `ClauseLeaseInput` date field names (checked against the schema in Task
  2), the s 63 mapping row (probe decides in Task 2 Step 1), fake-judge
  fixture mechanics (mirror the existing files), SaaS grouping mechanics
  (read first, Task 6 Step 1).
