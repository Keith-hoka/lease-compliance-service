# VIC Clause Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sixteen VIC prohibited-terms clause rules (RTA 1997 s 27B + Regs 2021 reg 11) judged by the existing LLM stage, with jurisdiction dispatch, `ClauseAuditCreate` opened to VIC, and a golden-set eval gating rollout.

**Architecture:** `ClauseRule` gains a `jurisdiction` field; VIC rules live in `app/clause_audit/rules_vic.py`; `run_prohibited`/`run_mandatory` take the rules list and `process_job` dispatches on `job.jurisdiction` (VIC = prohibited + fields only). The one NSW-specific prompt string (`SYSTEM`) becomes jurisdiction-neutral, gated by the NSW eval.

**Tech Stack:** existing only - no new dependencies, no migrations.

**Spec:** `docs/superpowers/specs/2026-08-05-vic-clause-audit-design.md`

## Global Constraints

- Python 3.12+, `uv` only. TDD: failing test first, watch it fail for the right reason.
- Every task ends: full suite -> ruff sequence (`uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`) -> commit -> push origin main -> CI green.
- No emojis. Docstrings over comments; the rules module docstring quotes the corpus text it enforces with an as-at date.
- Constants exactly: `ACT = "residential-tenancies-act-1997"`, `REGS = "residential-tenancies-regulations-2021"`, `VIC_COMMENCED = date(2021, 3, 29)`.
- Rule ids exactly (all `vic.clause.` prefixed): `renter_insurance`, `provider_liability_exemption`, `breach_penalty`, `professional_cleaning_required`, `professional_cleaning_cost`, `no_breach_rent_inducement`, `preparation_costs`, `unreviewed_contract`, `renter_indemnity`, `late_availability_claim_waiver`, `costly_payment_method`, `third_party_services`, `safety_maintenance_transfer`, `tribunal_costs_transfer`, `insurance_excess_transfer`, `fixed_break_fees`.
- `ENGINE_VERSION = "1.4.0"`. `ClauseAuditCreate.jurisdiction` becomes `Literal["NSW", "VIC"]`.
- `SYSTEM` opens "You are a compliance checker for Australian residential tenancy documents." - the only wording change to it.
- `THRESHOLDS` default stays `(0.9, 0.8)`; no per-rule overrides in this plan.
- No NSW clause-rule change beyond the `jurisdiction="NSW"` stamp; no deterministic-rules or engine change.

---

### Task 1: VIC clause rules module and structure tests

**Files:**
- Create: `app/clause_audit/rules_vic.py`
- Modify: `app/clause_audit/rules.py` (add `jurisdiction` field; stamp the 15 NSW rules)
- Modify: `docs/rule-candidates.md` (append VIC exclusions)
- Test: `tests/test_clause_rules_vic.py`
- Test (touch only): `tests/test_clause_processor.py` (its module-level `RULE` constructor gains `jurisdiction="NSW"`)

**Interfaces:**
- Consumes: `ClauseRule`, `rule_active`, `resolve_rule` from `app.clause_audit.rules`; `SectionRef` from `app.rules.base`.
- Produces: `VIC_PROHIBITED_RULES: list[ClauseRule]` (16 rules) in `app.clause_audit.rules_vic`; `ClauseRule` with a `jurisdiction: Literal["NSW", "VIC"]` field. Task 2's processor dispatch and Task 3's golden sets rely on both.

- [ ] **Step 0: Verify the statutory anchors from the corpus**

```bash
uv run python - <<'EOF'
import asyncio
from datetime import date
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.core.config import settings
from app.services.legislation import section_at

async def check():
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for slug, no in (
            ("residential-tenancies-act-1997", "27B"),
            ("residential-tenancies-act-1997", "27C"),
            ("residential-tenancies-regulations-2021", "11"),
        ):
            pre = await section_at(s, slug, no, date(2021, 3, 28))
            post = await section_at(s, slug, no, date(2021, 3, 29))
            print(slug, no, "pre-reform:", pre is not None, "post:", post is not None)
        sec = await section_at(s, "residential-tenancies-act-1997", "27C", date(2021, 3, 29))
        print(sec.body_text[:400])
    await engine.dispose()

asyncio.run(check())
EOF
```

Expected: all three sections absent at 2021-03-28 and present at 2021-03-29,
confirming `VIC_COMMENCED = date(2021, 3, 29)`; the s 27C print shows the
"restore ... condition ... fair wear and tear" carve-out shape used in the
two cleaning questions. If any date differs, use what the corpus shows and
note it in the module docstring.

- [ ] **Step 1: Write the failing tests**

`tests/test_clause_rules_vic.py`:

```python
from datetime import date

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clause_audit.rules import resolve_rule, rule_active
from app.clause_audit.rules_vic import VIC_COMMENCED, VIC_PROHIBITED_RULES
from app.models import Act

AS_AT = date(2026, 8, 5)


@pytest.fixture
async def corpus_session():
    """A session against the dev store; skip when the VIC corpus is absent."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            act = (
                await session.execute(
                    select(Act).where(Act.slug == "residential-tenancies-act-1997")
                )
            ).scalar_one_or_none()
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("VIC corpus store not reachable")
        if act is None:
            pytest.skip("VIC corpus not ingested")
        yield session
    await engine.dispose()


def test_sixteen_unique_vic_rules():
    ids = [r.rule_id for r in VIC_PROHIBITED_RULES]
    assert len(ids) == 16
    assert len(set(ids)) == 16
    assert all(rule_id.startswith("vic.clause.") for rule_id in ids)
    assert all(r.jurisdiction == "VIC" for r in VIC_PROHIBITED_RULES)
    assert all(r.family == "prohibited" for r in VIC_PROHIBITED_RULES)


def test_vic_rules_inactive_before_commencement():
    assert all(not rule_active(r, date(2021, 3, 28)) for r in VIC_PROHIBITED_RULES)
    assert all(rule_active(r, VIC_COMMENCED) for r in VIC_PROHIBITED_RULES)


def test_nsw_rules_carry_their_jurisdiction():
    from app.clause_audit.rules import MANDATORY_RULES, PROHIBITED_RULES

    assert all(r.jurisdiction == "NSW" for r in PROHIBITED_RULES + MANDATORY_RULES)


async def test_every_vic_rule_resolves_on_the_corpus(corpus_session):
    for rule in VIC_PROHIBITED_RULES:
        citation = await resolve_rule(corpus_session, rule, AS_AT)
        assert citation is not None, rule.rule_id
        assert citation.section_no == rule.ref.section_no


async def test_vic_rules_do_not_resolve_before_commencement(corpus_session):
    citation = await resolve_rule(corpus_session, VIC_PROHIBITED_RULES[0], date(2021, 3, 28))
    assert citation is None
```

- [ ] **Step 2: Watch them fail**

Run: `uv run pytest tests/test_clause_rules_vic.py -v`
Expected: collection error - `ModuleNotFoundError`/`ImportError` on
`app.clause_audit.rules_vic` (module does not exist yet).

- [ ] **Step 3: Add the `jurisdiction` field and stamp the NSW rules**

In `app/clause_audit/rules.py`, add the field to the dataclass right
after `rule_id`:

```python
@dataclass(frozen=True)
class ClauseRule:
    rule_id: str
    jurisdiction: Literal["NSW", "VIC"]
    family: Literal["prohibited", "mandatory"]
    ref: SectionRef
    applies_from: date | None
    applies_to: date | None
    question: str
```

Add `jurisdiction="NSW",` to each of the 15 existing rule constructors
(all use keyword arguments, so this is a mechanical insertion). Also add
`jurisdiction="NSW",` to the module-level `RULE` in
`tests/test_clause_processor.py` - it constructs a `ClauseRule` directly
and will not compile otherwise.

- [ ] **Step 4: Implement `app/clause_audit/rules_vic.py`**

```python
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
        "professionally cleaned at the end of the tenancy (s 27B(1)(d))." + _CLEANING_CARVE_OUT,
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
        "provider (reg 11(e)). Not breached where the nominated service is "
        "an embedded network.",
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
```

- [ ] **Step 5: Append the VIC exclusions to `docs/rule-candidates.md`**

Append this section verbatim:

```markdown
## VIC clause-rule exclusions (corpus as at 2026-08-05)

Surveyed for the VIC clause audit (s 27B + reg 11 shipped as
`vic.clause.*` rules). Excluded:

| Source | Why excluded |
|---|---|
| s 27 (invalid additional terms) | Deciding "additional to the standard form" requires the standard form's own terms - the form-comparison milestone |
| s 28 (harsh and unconscionable terms) | Tribunal discretion on application, not a clause-readable effect |
| regs 39 / 53 / 73 (prohibited terms) | Rooming houses, caravan parks and site agreements - other tenure types outside the residential rental audit |
| Standard-form presence/comparison | Regulation schedules milestone |
```

- [ ] **Step 6: Run the tests, then the full suite**

```bash
uv run pytest tests/test_clause_rules_vic.py -v
uv run pytest
```

Expected: 5 passed in the new file (corpus tests run against the dev
store; in CI they self-skip). Full suite green - the only pre-existing
tests touched are `test_clause_processor.py` (RULE stamp) and anything
constructing `ClauseRule` (grep `ClauseRule(` to confirm no other
constructor exists outside `rules.py`, `rules_vic.py`, and that test).

- [ ] **Step 7: Ruff, commit, push, CI**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/clause_audit/rules.py app/clause_audit/rules_vic.py tests/test_clause_rules_vic.py tests/test_clause_processor.py docs/rule-candidates.md
git commit -m "Add VIC clause rules with jurisdiction-stamped ClauseRule"
git push origin main
```

---

### Task 2: Jurisdiction dispatch, neutral system prompt, API opening

**Files:**
- Modify: `app/clause_audit/families.py` (runners take the rules list)
- Modify: `app/clause_audit/processor.py` (dispatch on `job.jurisdiction`)
- Modify: `app/llm/prompts.py:8` (SYSTEM wording)
- Modify: `app/schemas/clause_audit.py:42` (`Literal["NSW", "VIC"]`)
- Modify: `app/rules/__init__.py` (`ENGINE_VERSION = "1.4.0"`)
- Modify: `tests/test_clause_families.py`, `tests/test_llm_eval.py` (runner call sites gain the rules argument)
- Test: `tests/test_clause_processor.py` (append dispatch test), `tests/test_clause_schemas.py` (flip VIC rejection), `tests/test_clause_api.py` (append VIC acceptance)

**Interfaces:**
- Consumes: `VIC_PROHIBITED_RULES` from Task 1.
- Produces: `run_prohibited(judge, session, doc, as_at, rules)` and `run_mandatory(judge, session, doc, as_at, rules)`; `process_job` dispatching NSW -> prohibited + mandatory + fields, VIC -> prohibited + fields. Task 3's eval harness calls the new runner signatures.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_clause_processor.py` (mirror the file's existing
job-construction and fake-judge idioms - read it first; the snippet
below names the essentials):

```python
VIC_RULE = ClauseRule(
    rule_id="vic.clause.renter_insurance",
    jurisdiction="VIC",
    family="prohibited",
    ref=SectionRef("residential-tenancies-act-1997", "27B"),
    applies_from=date(2021, 3, 29),
    applies_to=None,
    question="A term requiring the renter to take out insurance.",
)


@pytest.fixture
async def seeded_s27b(db_session):
    act = Act(
        jurisdiction="VIC",
        slug="residential-tenancies-act-1997",
        title="Residential Tenancies Act 1997",
        source_url="x",
    )
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2021, 3, 29),
        [ParsedSection("27B", "Prohibited terms-general", "must not include", "Part 2", None)],
    )
    await db_session.commit()


async def test_vic_job_runs_prohibited_and_fields_only(db_session, seeded_s27b, monkeypatch):
    from app.clause_audit import rules_vic

    monkeypatch.setattr(rules_vic, "VIC_PROHIBITED_RULES", [VIC_RULE])
    called = []

    async def judge(doc, instruction, output_model):
        called.append(output_model.__name__)
        if output_model.__name__ == "FieldsOutput":
            return output_model(fields=[])
        return output_model(
            items=[
                {
                    "rule_id": "vic.clause.renter_insurance",
                    "verdict": "green",
                    "reasoning": "no such term",
                    "clause_quote": None,
                }
            ]
        )

    job = ClauseAuditJob(
        jurisdiction="VIC",
        as_at=date(2026, 8, 5),
        engine_version="1.4.0",
        model="m",
        status="running",
        document_kind="text",
        document=b"RESIDENTIAL RENTAL AGREEMENT. Rent is payable monthly.",
        lease={"rent_amount": "2000"},
    )
    db_session.add(job)
    await db_session.flush()

    await process_job(db_session, job, judge)

    assert [f["rule_id"] for f in job.findings] == ["vic.clause.renter_insurance"]
    assert called == ["ProhibitedOutput", "FieldsOutput"]
```

In `tests/test_clause_schemas.py`, replace
`test_create_rejects_other_jurisdiction` (line 33) with:

```python
def test_create_accepts_vic_and_rejects_unsupported():
    body = ClauseAuditCreate.model_validate({"jurisdiction": "VIC"})
    assert body.jurisdiction == "VIC"
    with pytest.raises(ValidationError):
        ClauseAuditCreate.model_validate({"jurisdiction": "QLD"})
```

Append to `tests/test_clause_api.py` (mirror its existing POST helper
and auth headers - read it first):

```python
async def test_vic_clause_audit_accepted(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "unit-test-key")
    response = await client.post(
        "/v1/clause-audits",
        data={"payload": '{"jurisdiction": "VIC"}'},
        files={"file": ("l.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers=KEY,
    )
    assert response.status_code == 201
    assert response.json()["jurisdiction"] == "VIC"
```

- [ ] **Step 2: Watch them fail**

Run: `uv run pytest tests/test_clause_processor.py tests/test_clause_schemas.py tests/test_clause_api.py -v`
Expected: the dispatch test fails (`run_prohibited` signature has no
rules parameter yet / VIC path runs mandatory too), the schema test
fails with `ValidationError` on "VIC", the API test fails with 422.

- [ ] **Step 3: Implement**

`app/clause_audit/families.py` - the two runners take the rules list;
`_run_clause_family` and `run_fields` are untouched:

```python
async def run_prohibited(
    judge: JudgeFn,
    session: AsyncSession,
    doc: DocumentInput,
    as_at: date,
    rules: list[clause_rules.ClauseRule],
) -> list[ClauseFinding]:
    return await _run_clause_family(
        judge,
        session,
        doc,
        as_at,
        rules,
        "prohibited terms",
        "ProhibitedOutput",
        quote_verdict="red",
        verdict_guidance=PROHIBITED_GUIDANCE,
    )


async def run_mandatory(
    judge: JudgeFn,
    session: AsyncSession,
    doc: DocumentInput,
    as_at: date,
    rules: list[clause_rules.ClauseRule],
) -> list[ClauseFinding]:
    return await _run_clause_family(
        judge,
        session,
        doc,
        as_at,
        rules,
        "mandatory terms",
        "MandatoryOutput",
        quote_verdict="green",
        verdict_guidance=MANDATORY_GUIDANCE,
    )
```

`app/clause_audit/processor.py`:

```python
"""Run one claimed job end to end and wipe the document. Caller commits."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.clause_audit import rules as clause_rules
from app.clause_audit import rules_vic
from app.clause_audit.document import document_input
from app.clause_audit.families import run_fields, run_mandatory, run_prohibited
from app.llm.client import JudgeFn
from app.models import ClauseAuditJob
from app.schemas.clause_audit import ClauseLeaseInput


async def process_job(session: AsyncSession, job: ClauseAuditJob, judge: JudgeFn) -> None:
    doc = document_input(job.document_kind, job.document)
    if job.jurisdiction == "VIC":
        findings = await run_prohibited(
            judge, session, doc, job.as_at, rules_vic.VIC_PROHIBITED_RULES
        )
    else:
        findings = await run_prohibited(
            judge, session, doc, job.as_at, clause_rules.PROHIBITED_RULES
        )
        findings += await run_mandatory(
            judge, session, doc, job.as_at, clause_rules.MANDATORY_RULES
        )
    discrepancies = []
    if job.lease is not None:
        lease = ClauseLeaseInput.model_validate(job.lease)
        discrepancies = await run_fields(judge, doc, lease)
    job.findings = [f.model_dump(mode="json") for f in findings]
    job.discrepancies = [d.model_dump(mode="json") for d in discrepancies]
    job.status = "succeeded"
    job.completed_at = datetime.now(UTC)
    job.document = None
```

`app/llm/prompts.py:8` - change only the first sentence's jurisdiction
words: `"You are a compliance checker for Australian residential "
"tenancy documents. "` (rest of SYSTEM byte-identical).

`app/schemas/clause_audit.py:42`: `jurisdiction: Literal["NSW", "VIC"]`.

`app/rules/__init__.py`: `ENGINE_VERSION = "1.4.0"`.

Update every existing runner call site to pass the rules list - grep
`run_prohibited\|run_mandatory` across `tests/`; expected call sites:
`tests/test_clause_families.py` (pass the monkeypatched/inline rules it
already builds) and `tests/test_llm_eval.py`
(`run_prohibited(..., PROHIBITED_RULES)` via `_score_family` - pass
`rules` through where it invokes `runner(judge, session, doc, AS_AT)`,
becoming `runner(judge, session, doc, AS_AT, rules)`).

- [ ] **Step 4: Run the file set, then the full suite**

```bash
uv run pytest tests/test_clause_processor.py tests/test_clause_schemas.py tests/test_clause_api.py tests/test_clause_families.py -v
uv run pytest
```

Expected: all green. The llm_eval tests are deselected by default; their
signature update is exercised in Task 4's eval run.

- [ ] **Step 5: Ruff, commit, push, CI**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add -A
git commit -m "Dispatch clause audit by jurisdiction and open the API to VIC"
git push origin main
```

---

### Task 3: VIC golden set and eval harness

**Files:**
- Create: `tests/golden/clauses_vic.py`
- Modify: `tests/test_llm_eval.py` (VIC prohibited eval test; eval fixture gates both corpora)
- Test: `tests/test_clause_rules_vic.py` (append the golden-coverage test)

**Interfaces:**
- Consumes: `VIC_PROHIBITED_RULES` (Task 1), `run_prohibited(judge, session, doc, as_at, rules)` (Task 2), `_score_family`, `THRESHOLDS` and the `ClauseCase` dataclass from `tests/golden/clauses.py`.
- Produces: `VIC_PROHIBITED_CASES: list[ClauseCase]` in `tests.golden.clauses_vic`; eval test `test_vic_prohibited_golden`.

- [ ] **Step 1: Write the coverage test first (it pins the golden set's shape)**

Append to `tests/test_clause_rules_vic.py`:

```python
def test_vic_golden_covers_every_rule():
    from tests.golden.clauses_vic import VIC_PROHIBITED_CASES

    by_rule: dict[str, list] = {}
    for case in VIC_PROHIBITED_CASES:
        by_rule.setdefault(case.rule_id, []).append(case)
    rule_ids = {r.rule_id for r in VIC_PROHIBITED_RULES}
    assert set(by_rule) == rule_ids
    for rule_id, cases in by_rule.items():
        reds = [c for c in cases if c.expected == "red"]
        assert len(reds) >= 3, rule_id
    greens_required = {
        "vic.clause.professional_cleaning_required",
        "vic.clause.professional_cleaning_cost",
        "vic.clause.third_party_services",
        "vic.clause.costly_payment_method",
        "vic.clause.fixed_break_fees",
        "vic.clause.breach_penalty",
    }
    for rule_id in greens_required:
        assert any(c.expected == "green" for c in by_rule[rule_id]), rule_id
    case_ids = [c.case_id for c in VIC_PROHIBITED_CASES]
    assert len(case_ids) == len(set(case_ids))
```

Run: `uv run pytest tests/test_clause_rules_vic.py::test_vic_golden_covers_every_rule -v`
Expected: FAIL - `ModuleNotFoundError: tests.golden.clauses_vic`.

- [ ] **Step 2: Author `tests/golden/clauses_vic.py`**

Module skeleton, contract docstring, and exemplar cases below; author
the remaining cases to the same shape until the coverage test passes.
Every case reads like a real lease clause (no meta-language), uses the
shared `_PREAMBLE` idiom, and post-2021 VIC vocabulary ("residential
rental provider"/"renter") except where marked.

```python
"""Seeded VIC golden set for the LLM clause audit.

Scoring contract (identical to the NSW set): every case's target rule
expects case.expected and every other rule expects green - each case is
a hard negative for the other fifteen rules. yellow on a red case is a
recall miss; red on a green case is a precision hit against the judging
rule.

Terminology: mostly post-2021 "residential rental provider"/"renter";
cases suffixed "-oldstyle" deliberately use "landlord"/"tenant" - real
VIC templates blend both and recall must hold on either.
"""

from tests.golden.clauses import ClauseCase

_PREAMBLE = "RESIDENTIAL RENTAL AGREEMENT between rental provider and renter. "

VIC_PROHIBITED_CASES = [
    # --- vic.clause.renter_insurance ---
    ClauseCase(
        "vic-insurance-red-plain",
        "vic.clause.renter_insurance",
        _PREAMBLE + "The renter must take out and maintain contents insurance "
        "for the duration of the tenancy.",
        "red",
    ),
    ClauseCase(
        "vic-insurance-red-specified",
        "vic.clause.renter_insurance",
        _PREAMBLE + "The renter shall obtain public liability insurance of no "
        "less than $10 million from an insurer approved by the rental "
        "provider and provide a certificate of currency on request.",
        "red",
    ),
    ClauseCase(
        "vic-insurance-red-oldstyle",
        "vic.clause.renter_insurance",
        "RESIDENTIAL TENANCY AGREEMENT. The tenant agrees to effect an "
        "insurance policy covering the tenant's possessions and any glass "
        "breakage at the property for the term of the lease.",
        "red",
    ),
    # --- vic.clause.fixed_break_fees ---
    ClauseCase(
        "vic-breakfee-red-flat",
        "vic.clause.fixed_break_fees",
        _PREAMBLE + "If the renter ends this agreement before the end of the "
        "fixed term, the renter must pay a lease break fee of $1,500.",
        "red",
    ),
    ClauseCase(
        "vic-breakfee-red-schedule",
        "vic.clause.fixed_break_fees",
        _PREAMBLE + "Early termination attracts a fixed administration charge "
        "of two weeks rent plus a $250 advertising levy, payable on vacating.",
        "red",
    ),
    ClauseCase(
        "vic-breakfee-red-paraphrase",
        "vic.clause.fixed_break_fees",
        _PREAMBLE + "Should the renter vacate prior to the expiry date a "
        "set amount of $990 becomes due to cover reletting, regardless of "
        "when a replacement renter is found.",
        "red",
    ),
    ClauseCase(
        "vic-breakfee-green-basis",
        "vic.clause.fixed_break_fees",
        _PREAMBLE + "If the renter terminates early, a reletting fee applies "
        "calculated as: the rental provider's actual advertising costs plus "
        "a pro rata portion of the letting fee, being one week's rent "
        "multiplied by the fraction of the fixed term remaining.",
        "green",
    ),
    # --- hard greens for the other carve-out rules ---
    ClauseCase(
        "vic-cleaningreq-green-27c-shape",
        "vic.clause.professional_cleaning_required",
        _PREAMBLE + "The premises must be professionally cleaned at the end "
        "of the tenancy only if professional cleaning becomes required to "
        "restore the premises to the condition they were in immediately "
        "before the start of the tenancy, taking into account fair wear "
        "and tear.",
        "green",
    ),
    ClauseCase(
        "vic-cleaningcost-green-27c-shape",
        "vic.clause.professional_cleaning_cost",
        _PREAMBLE + "The renter must pay the cost of professional cleaning "
        "only where such cleaning becomes required to restore the premises "
        "to their condition immediately before the start of the tenancy, "
        "fair wear and tear excepted.",
        "green",
    ),
    ClauseCase(
        "vic-thirdparty-green-embedded",
        "vic.clause.third_party_services",
        _PREAMBLE + "Electricity to the premises is supplied through the "
        "building's embedded network operated by OnPower Pty Ltd and the "
        "renter must acquire electricity from that embedded network "
        "supplier.",
        "green",
    ),
    ClauseCase(
        "vic-payment-green-bankfees",
        "vic.clause.costly_payment_method",
        _PREAMBLE + "Rent is payable by direct debit from the renter's "
        "nominated bank account; any bank or account fees charged by the "
        "renter's own financial institution are the renter's "
        "responsibility.",
        "green",
    ),
    ClauseCase(
        "vic-breach-green-lawful-breakfee",
        "vic.clause.breach_penalty",
        _PREAMBLE + "If the renter ends the agreement early, the renter is "
        "liable for the reasonable costs of reletting, calculated as the "
        "advertising actually incurred plus a letting fee prorated to the "
        "unexpired portion of the term.",
        "green",
    ),
    # ... author the remaining red cases here: three per rule for every
    # rule not yet at three (plain wording, a cost/variant form, and a
    # paraphrase; include a couple of "-oldstyle" landlord/tenant cases
    # spread across different rules), until
    # test_vic_golden_covers_every_rule passes.
]
```

The comment marker above is scaffolding for authoring, not shipped
content - the finished module contains only complete cases and no
ellipsis comment. Aim for roughly 54 cases (48 reds + 6 greens).

Author guidance for specific rules: `provider_liability_exemption` reds
include one exempting the provider's *agent*; `preparation_costs` reds
include a "renter pays the cost of preparing this agreement" and a
"each party bears the other's legal costs of preparation" variant;
`unreviewed_contract` reds bind the renter to a body-corporate or
utility contract "as amended from time to time" sight unseen;
`safety_maintenance_transfer` reds delegate smoke-alarm or gas-check
duties to the renter at the renter's cost; `insurance_excess_transfer`
reds make the renter pay the provider's policy excess for any claim;
`tribunal_costs_transfer` reds pass on VCAT application fees;
`late_availability_claim_waiver` reds bar compensation if the premises
are not ready on the commencement date; `no_breach_rent_inducement`
reds offer a rent rebate for a contravention-free term;
`costly_payment_method` reds mandate a card-payment portal with a
surcharge or a rent-tech app with a service fee.

- [ ] **Step 3: Run the coverage test until green**

Run: `uv run pytest tests/test_clause_rules_vic.py -v`
Expected: all tests green, including `test_vic_golden_covers_every_rule`.

- [ ] **Step 4: Wire the VIC eval test**

In `tests/test_llm_eval.py`: extend the eval fixture's corpus gate to
also skip when the VIC act is absent (second `select(Act)` on
`residential-tenancies-act-1997`), and append:

```python
async def test_vic_prohibited_golden(eval_session):
    from app.clause_audit.rules_vic import VIC_PROHIBITED_RULES
    from tests.golden.clauses_vic import VIC_PROHIBITED_CASES

    stats = await _score_family(
        eval_session, run_prohibited, VIC_PROHIBITED_RULES, VIC_PROHIBITED_CASES
    )
    _assert_thresholds(stats)
```

Match `_score_family`'s current signature exactly as refactored in
Task 2 (it receives the rules list and passes it to the runner). Do not
run the eval in this task - it costs real model calls and is Task 4's
gate. Confirm collection only:

```bash
uv run pytest -m llm_eval --collect-only -q
```

Expected: the VIC test collects alongside the NSW ones.

- [ ] **Step 5: Full suite, ruff, commit, push, CI**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add tests/golden/clauses_vic.py tests/test_llm_eval.py tests/test_clause_rules_vic.py
git commit -m "Add the VIC clause golden set and eval harness"
git push origin main
```

---

### Task 4: Eval gate and rollout (interactive)

No repo changes except eval-driven question tweaks (if any), the ledger,
and memory. Run by the controller.

- [ ] **Step 1: Eval gate**

```bash
uv run pytest -m llm_eval -k "prohibited or mandatory" -v
```

Both NSW families (regression for the SYSTEM wording change) and the
VIC family must print per-rule tables with every rule at precision >=
0.9 and recall >= 0.8. If a VIC rule misses, iterate its question text
in `rules_vic.py` (or the offending golden case if it is genuinely
ambiguous), re-run that family only, and record each iteration in the
ledger. Never lower a threshold. If an NSW rule regresses, the SYSTEM
wording change is the prime suspect - stop and investigate before
touching anything else. Commit any question-text iterations with the
full task-end sequence.

- [ ] **Step 2: Deploy**

```bash
LEASE_DEPLOY_SERVER=deploy@168.144.169.66 LEASE_DEPLOY_DOMAIN=api.leasekoala.com ./deploy/deploy.sh
```

Migrationless. If the script's final health probe 502s (its retry
budget is shorter than uvicorn's boot), verify directly: `/health`
returns 200 and `docker compose logs api` shows a clean startup.

- [ ] **Step 3: Production acceptance**

```bash
uv run python - <<'EOF'
from tests.fixtures.pdfs import make_text_pdf
open(".superpowers/sdd/vic-clause-acceptance.pdf", "wb").write(
    make_text_pdf(
        "RESIDENTIAL RENTAL AGREEMENT between rental provider and renter. "
        "The renter must take out and maintain contents insurance for the "
        "duration of the tenancy. Rent is $2,000 per month payable in advance."
    )
)
EOF
APIKEY=$(grep '^COMPLIANCE_API_KEY=' /Users/keithho/LLMProjects/rental_management_app/backend/.env | cut -d= -f2-)
curl -s -X POST https://api.leasekoala.com/v1/clause-audits \
  -H "X-API-Key: ${APIKEY}" \
  -F 'payload={"jurisdiction":"VIC"}' \
  -F "file=@.superpowers/sdd/vic-clause-acceptance.pdf;type=application/pdf"
```

Poll `GET /v1/clause-audits/{id}` until `succeeded`, then verify:
`vic.clause.renter_insurance` is red, cites Residential Tenancies Act
1997 s 27B with a section id, and carries a clause_quote found in the
document; every other finding is green/yellow, none skipped. Run the
same POST with an NSW control document (the NSW live-e2e carpet text)
and confirm `nsw.clause.carpet_cleaning` red as before. Check usage:

```bash
ssh deploy@168.144.169.66 'cd /opt/lease-compliance && docker compose exec api uv run --no-sync python -m app.tenants usage rentalapp'
```

Expected: the clause counter incremented by the two acceptance jobs.

- [ ] **Step 4: Ledger and memory**

Append to `.superpowers/sdd/progress.md`: completion, commits, the eval
precision/recall tables (NSW + VIC), production acceptance results.
Update the milestone memory: VIC sub-project (c) done. Note for
sub-project (d): both clause-audit jurisdictions now open.

---

## Self-review

- Spec coverage: 16 rules with exact ids and carve-out questions
  (Task 1); jurisdiction field + NSW stamp (Task 1); dispatch with no
  empty family calls (Task 2); SYSTEM neutralisation with NSW eval as
  regression gate (Tasks 2+4); ClauseAuditCreate opening + unsupported
  422 (Task 2); ENGINE_VERSION 1.4.0 (Task 2); golden cross-scoring
  contract, three reds per rule, six hard greens, terminology mix,
  coverage-enforcing test (Task 3); thresholds unchanged (global);
  eval-gated rollout with production acceptance and usage check
  (Task 4); rule-candidates exclusions (Task 1).
- Placeholders: the Task 3 ellipsis is explicitly authoring scaffolding
  with a machine-checked completion condition
  (`test_vic_golden_covers_every_rule`), not unfinished plan content.
- Type consistency: runner signatures `(judge, session, doc, as_at,
  rules)` match between Task 2's implementation, Task 2's test call
  sites, and Task 3's `_score_family` usage; `ClauseRule` field order
  (`rule_id, jurisdiction, family, ref, applies_from, applies_to,
  question`) is identical in Task 1's dataclass, Task 1's helpers, and
  Task 2's test constructor.
