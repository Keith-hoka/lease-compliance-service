"""Real-model evals. Opt in with: uv run pytest -m llm_eval

Needs the dev corpus store and settings.anthropic_api_key. Every test prints
a per-rule precision/recall table; thresholds come from THRESHOLDS.
"""

import tempfile
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import asyncpg
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clause_audit.document import DocumentInput, document_input
from app.clause_audit.families import run_fields, run_prohibited
from app.clause_audit.rules import PROHIBITED_RULES
from app.clause_audit.standard_form import fetch_form_terms, run_standard_form
from app.core.config import settings
from app.llm.client import JudgeError, make_judge
from app.models import Act
from app.schemas.clause_audit import ClauseLeaseInput
from tests.fixtures.pdfs import make_scanned_pdf, make_text_pdf
from tests.golden.clauses import FIELD_CASES, PROHIBITED_CASES, THRESHOLDS
from tests.golden.standard_form import SF_THRESHOLDS, plan_documents

pytestmark = pytest.mark.llm_eval

AS_AT = date(2026, 7, 28)
AS_AT_SF = date(2026, 8, 9)
SF_DEFAULT_THRESHOLDS = {"default": (SF_THRESHOLDS["precision"], SF_THRESHOLDS["recall"])}

_FAILURE_DUMP_DIR = Path(tempfile.gettempdir()) / "lease-compliance-eval-failures"
_INFRA_RETRIES = 2


@pytest.fixture
async def eval_session():
    if not settings.anthropic_api_key:
        pytest.skip("anthropic_api_key not configured")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        try:
            act = (
                await session.execute(select(Act).where(Act.slug == "act-2010-042"))
            ).scalar_one_or_none()
            vic_act = (
                await session.execute(
                    select(Act).where(Act.slug == "residential-tenancies-act-1997")
                )
            ).scalar_one_or_none()
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("corpus store not reachable")
        if act is None or vic_act is None:
            pytest.skip("corpus not ingested")
        yield session
    await engine.dispose()


def _assert_thresholds(stats: dict, thresholds: dict = THRESHOLDS) -> None:
    failures = []
    print(f"\n{'rule':45} {'P':>6} {'R':>6} {'yellow':>7}")
    for rule_id, counts in sorted(stats.items()):
        tp, fp, misses, yellows = counts["tp"], counts["fp"], counts["miss"], counts["yellow"]
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + misses) if tp + misses else 1.0
        min_p, min_r = thresholds.get(rule_id, thresholds["default"])
        print(f"{rule_id:45} {precision:6.2f} {recall:6.2f} {yellows:7d}")
        if precision < min_p:
            failures.append(f"{rule_id} precision {precision:.2f} < {min_p}")
        if recall < min_r:
            failures.append(f"{rule_id} recall {recall:.2f} < {min_r}")
    assert not failures, "; ".join(failures)


def _assert_family_thresholds(stats: dict, min_p: float = 0.9, min_r: float = 0.8) -> None:
    """Owner decisions 2026-08-11: both prohibited families gate on pooled
    precision/recall across their rules, with the per-rule table retained
    for diagnosis. Evidence: VIC runs 16-22 rotated five distinct
    single-rule failure combinations, and NSW showed the same shape
    (utility_provider single-run FP, zero on full rescan) - per-rule
    denominators of 3-9 cases sit inside model judgment noise. The three
    standard-form families keep per-term gates (n=6, stable)."""
    print(f"\n{'rule':45} {'P':>6} {'R':>6} {'yellow':>7}")
    pooled = {"tp": 0, "fp": 0, "miss": 0}
    for rule_id, counts in sorted(stats.items()):
        tp, fp, misses, yellows = counts["tp"], counts["fp"], counts["miss"], counts["yellow"]
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + misses) if tp + misses else 1.0
        print(f"{rule_id:45} {precision:6.2f} {recall:6.2f} {yellows:7d}")
        for key in pooled:
            pooled[key] += counts[key]
    family_p = pooled["tp"] / (pooled["tp"] + pooled["fp"]) if pooled["tp"] + pooled["fp"] else 1.0
    family_r = (
        pooled["tp"] / (pooled["tp"] + pooled["miss"]) if pooled["tp"] + pooled["miss"] else 1.0
    )
    print(f"{'FAMILY (pooled)':45} {family_p:6.2f} {family_r:6.2f}")
    assert family_p >= min_p, f"family precision {family_p:.2f} < {min_p}"
    assert family_r >= min_r, f"family recall {family_r:.2f} < {min_r}"


async def _score_family(session, runner, rules, cases):
    judge = make_judge()
    rule_ids = {r.rule_id for r in rules}
    stats: dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "miss": 0, "yellow": 0})
    for case in cases:
        findings = await runner(
            judge, session, DocumentInput(kind="text", text=case.text), AS_AT, rules
        )
        for finding in findings:
            if finding.rule_id not in rule_ids or finding.verdict == "skipped":
                continue
            is_target = finding.rule_id == case.rule_id and case.expected == "red"
            expected = "red" if is_target else "green"
            counts = stats[finding.rule_id]
            if finding.verdict == "yellow":
                counts["yellow"] += 1
                if expected == "red":
                    counts["miss"] += 1
            elif finding.verdict == "red" and expected == "red":
                counts["tp"] += 1
            elif finding.verdict == "red" and expected == "green":
                counts["fp"] += 1
            elif finding.verdict == "green" and expected == "red":
                counts["miss"] += 1
    return stats


async def test_prohibited_golden(eval_session):
    stats = await _score_family(eval_session, run_prohibited, PROHIBITED_RULES, PROHIBITED_CASES)
    _assert_family_thresholds(stats)


async def test_vic_prohibited_golden(eval_session):
    from app.clause_audit.rules_vic import VIC_PROHIBITED_RULES
    from tests.golden.clauses_vic import VIC_PROHIBITED_CASES

    stats = await _score_family(
        eval_session, run_prohibited, VIC_PROHIBITED_RULES, VIC_PROHIBITED_CASES
    )
    _assert_family_thresholds(stats)


async def _run_standard_form_resilient(judge, session, doc, jurisdiction, lease, doc_id):
    """run_standard_form with up to _INFRA_RETRIES retries on infrastructure failure.

    Retries only JudgeError (the judge declined, or the SDK's own structured-
    output parse returned nothing) and pydantic ValidationError (a parse
    failure raised from inside the Anthropic SDK's parse path itself, which
    does not retry on its own - see the task report: on that path the SDK
    discards the raw response's usage and stop_reason before the exception
    reaches this code, so a call that keeps failing is dumped here for later
    inspection rather than silently lost). Scoring is untouched either way -
    a retried document's verdicts count exactly as a first-try success
    would; only genuine infrastructure failures are retried, never a
    verdict/content disagreement (those are not exceptions at all).
    """
    last_exc: JudgeError | ValidationError | None = None
    for _attempt in range(_INFRA_RETRIES + 1):
        try:
            return await run_standard_form(judge, session, doc, AS_AT_SF, jurisdiction, lease)
        except (JudgeError, ValidationError) as exc:
            last_exc = exc
    _FAILURE_DUMP_DIR.mkdir(parents=True, exist_ok=True)
    dump_path = _FAILURE_DUMP_DIR / f"{doc_id}-{int(time.time())}.txt"
    detail = last_exc.errors() if isinstance(last_exc, ValidationError) else str(last_exc)
    dump_path.write_text(f"doc_id={doc_id}\n{type(last_exc).__name__}\n{detail}\n")
    pytest.fail(
        f"run_standard_form failed {_INFRA_RETRIES + 1}x for doc {doc_id}: "
        f"{type(last_exc).__name__}. Dumped to {dump_path}"
    )


async def _score_standard_form(session, judge, jurisdiction, lease, docs):
    stats: dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "miss": 0, "yellow": 0})
    for doc in docs:
        findings = await _run_standard_form_resilient(
            judge,
            session,
            DocumentInput(kind="text", text=doc.text),
            jurisdiction,
            lease,
            doc.doc_id,
        )
        by_id = {f.rule_id: f for f in findings}
        for rule_id, expected in doc.expected.items():
            finding = by_id.get(rule_id)
            verdict = finding.verdict if finding is not None else "yellow"
            counts = stats[rule_id]
            if verdict == "yellow":
                counts["yellow"] += 1
                if expected == "red":
                    counts["miss"] += 1
            elif verdict == "red" and expected == "red":
                counts["tp"] += 1
            elif verdict == "red" and expected == "green":
                counts["fp"] += 1
            elif verdict == "green" and expected == "red":
                counts["miss"] += 1
    return stats


async def test_standard_form_eval_nsw(eval_session):
    terms, _ = await fetch_form_terms(eval_session, "NSW", AS_AT_SF, None)
    docs = plan_documents(terms)
    stats = await _score_standard_form(eval_session, make_judge(), "NSW", None, docs)
    _assert_thresholds(stats, SF_DEFAULT_THRESHOLDS)


async def test_standard_form_eval_vic_f1(eval_session):
    terms, _ = await fetch_form_terms(eval_session, "VIC", AS_AT_SF, None)
    docs = plan_documents(terms)
    stats = await _score_standard_form(eval_session, make_judge(), "VIC", None, docs)
    _assert_thresholds(stats, SF_DEFAULT_THRESHOLDS)


async def test_standard_form_eval_vic_f2(eval_session):
    lease = ClauseLeaseInput(start_date=date(2020, 1, 1), end_date=date(2026, 1, 2))
    terms, _ = await fetch_form_terms(eval_session, "VIC", AS_AT_SF, lease)
    docs = plan_documents(terms)
    stats = await _score_standard_form(eval_session, make_judge(), "VIC", lease, docs)
    _assert_thresholds(stats, SF_DEFAULT_THRESHOLDS)


async def test_fields_golden(eval_session):
    judge = make_judge()
    wrong = []
    for case in FIELD_CASES:
        lease = ClauseLeaseInput.model_validate(case.lease)
        found = await run_fields(judge, DocumentInput(kind="text", text=case.text), lease)
        got = {d.field for d in found}
        if got != case.expected:
            wrong.append(f"{case.case_id}: expected {case.expected}, got {got}")
    assert not wrong, "; ".join(wrong)


SEEDED = (
    "RESIDENTIAL TENANCY AGREEMENT. The rent is $560 per week payable weekly. "
    "The tenant must have all carpets professionally steam cleaned at the "
    "conclusion of the tenancy and provide a receipt to the landlord. "
    "The tenant must keep the premises reasonably clean."
)


async def test_pdf_smoke_text_layer(eval_session):
    doc = document_input("pdf", make_text_pdf(SEEDED))
    assert doc.kind == "text"
    findings = await run_prohibited(make_judge(), eval_session, doc, AS_AT, PROHIBITED_RULES)
    carpet = next(f for f in findings if f.rule_id == "nsw.clause.carpet_cleaning")
    assert carpet.verdict == "red"


async def test_pdf_smoke_scanned(eval_session):
    doc = document_input("pdf", make_scanned_pdf(SEEDED))
    assert doc.kind == "pdf"
    findings = await run_prohibited(make_judge(), eval_session, doc, AS_AT, PROHIBITED_RULES)
    carpet = next(f for f in findings if f.rule_id == "nsw.clause.carpet_cleaning")
    assert carpet.verdict == "red"
