"""Real-model evals. Opt in with: uv run pytest -m llm_eval

Needs the dev corpus store and settings.anthropic_api_key. Every test prints
a per-rule precision/recall table; thresholds come from THRESHOLDS.
"""

from collections import defaultdict
from datetime import date

import asyncpg
import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.clause_audit.document import DocumentInput, document_input
from app.clause_audit.families import run_fields, run_mandatory, run_prohibited
from app.clause_audit.rules import MANDATORY_RULES, PROHIBITED_RULES
from app.core.config import settings
from app.llm.client import make_judge
from app.models import Act
from app.schemas.clause_audit import ClauseLeaseInput
from tests.fixtures.pdfs import make_scanned_pdf, make_text_pdf
from tests.golden.clauses import FIELD_CASES, MANDATORY_CASES, PROHIBITED_CASES, THRESHOLDS

pytestmark = pytest.mark.llm_eval

AS_AT = date(2026, 7, 28)


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
        except (OSError, SQLAlchemyError, asyncpg.PostgresError):
            pytest.skip("corpus store not reachable")
        if act is None:
            pytest.skip("corpus not ingested")
        yield session
    await engine.dispose()


def _assert_thresholds(stats: dict) -> None:
    failures = []
    print(f"\n{'rule':45} {'P':>6} {'R':>6} {'yellow':>7}")
    for rule_id, counts in sorted(stats.items()):
        tp, fp, misses, yellows = counts["tp"], counts["fp"], counts["miss"], counts["yellow"]
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + misses) if tp + misses else 1.0
        min_p, min_r = THRESHOLDS.get(rule_id, THRESHOLDS["default"])
        print(f"{rule_id:45} {precision:6.2f} {recall:6.2f} {yellows:7d}")
        if precision < min_p:
            failures.append(f"{rule_id} precision {precision:.2f} < {min_p}")
        if recall < min_r:
            failures.append(f"{rule_id} recall {recall:.2f} < {min_r}")
    assert not failures, "; ".join(failures)


async def _score_family(session, runner, rules, cases):
    judge = make_judge()
    rule_ids = {r.rule_id for r in rules}
    stats: dict[str, dict] = defaultdict(lambda: {"tp": 0, "fp": 0, "miss": 0, "yellow": 0})
    for case in cases:
        findings = await runner(judge, session, DocumentInput(kind="text", text=case.text), AS_AT)
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
    _assert_thresholds(stats)


async def test_mandatory_golden(eval_session):
    stats = await _score_family(eval_session, run_mandatory, MANDATORY_RULES, MANDATORY_CASES)
    _assert_thresholds(stats)


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
    findings = await run_prohibited(make_judge(), eval_session, doc, AS_AT)
    carpet = next(f for f in findings if f.rule_id == "nsw.clause.carpet_cleaning")
    assert carpet.verdict == "red"


async def test_pdf_smoke_scanned(eval_session):
    doc = document_input("pdf", make_scanned_pdf(SEEDED))
    assert doc.kind == "pdf"
    findings = await run_prohibited(make_judge(), eval_session, doc, AS_AT)
    carpet = next(f for f in findings if f.rule_id == "nsw.clause.carpet_cleaning")
    assert carpet.verdict == "red"
