from datetime import date
from decimal import Decimal

from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act
from app.rules.base import Rule, SectionRef
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput


def lease() -> LeaseInput:
    return LeaseInput(
        rent_amount=Decimal(500), rent_frequency="weekly", start_date=date(2026, 1, 1)
    )


async def test_rule_with_section_not_in_force_is_skipped(db_session, monkeypatch):
    act = Act(jurisdiction="NSW", slug="act-2010-042", title="T", source_url="x")
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session, act.id, date(2020, 1, 1), [ParsedSection("1", "One", "Body", None, None)]
    )

    fake = Rule(
        rule_id="nsw.fake",
        jurisdiction="NSW",
        citations=[SectionRef("act-2010-042", "999")],
        applies_from=None,
        applies_to=None,
        required_inputs=[],
        check=lambda lease: ("green", "ok", {}),
    )
    monkeypatch.setattr("app.rules.engine.ALL_RULES", [fake])
    findings = await run_audit(db_session, "NSW", date(2026, 1, 1), lease())
    assert findings[0].verdict == "skipped"
    assert "not in force" in findings[0].skip_reason


async def test_rule_outside_applies_window_is_skipped(db_session, monkeypatch):
    act = Act(jurisdiction="NSW", slug="act-2010-042", title="T", source_url="x")
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session, act.id, date(2020, 1, 1), [ParsedSection("1", "One", "Body", None, None)]
    )
    fake = Rule(
        rule_id="nsw.fake",
        jurisdiction="NSW",
        citations=[SectionRef("act-2010-042", "1")],
        applies_from=date(2024, 1, 1),
        applies_to=None,
        required_inputs=[],
        check=lambda lease: ("green", "ok", {}),
    )
    monkeypatch.setattr("app.rules.engine.ALL_RULES", [fake])
    findings = await run_audit(db_session, "NSW", date(2022, 1, 1), lease())
    assert findings[0].verdict == "skipped"
    assert "not active" in findings[0].skip_reason
