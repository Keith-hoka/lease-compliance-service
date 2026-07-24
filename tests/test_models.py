import uuid
from datetime import date

from sqlalchemy import select

from app.models import Act, Audit, IngestedVersion, Section


async def test_legislation_round_trip(db_session):
    act = Act(
        jurisdiction="NSW",
        slug="act-2010-042",
        title="Residential Tenancies Act 2010",
        source_url="https://legislation.nsw.gov.au/view/html/inforce/current/act-2010-042",
    )
    db_session.add(act)
    await db_session.flush()
    db_session.add(
        Section(
            act_id=act.id,
            section_no="159",
            heading="Payment of bonds",
            body_text="A landlord must not require a bond exceeding 4 weeks rent.",
            part="Part 8 Rental bonds",
            division="Division 1 Payment of bonds",
            valid_from=date(2011, 1, 31),
            valid_to=None,
            source_version_date=date(2011, 1, 31),
            content_hash="abc123",
        )
    )
    db_session.add(IngestedVersion(act_id=act.id, version_date=date(2011, 1, 31)))
    await db_session.commit()

    stored = (
        await db_session.execute(select(Section).where(Section.section_no == "159"))
    ).scalar_one()
    assert stored.valid_to is None
    assert stored.part == "Part 8 Rental bonds"


async def test_audit_round_trip(db_session):
    audit = Audit(
        jurisdiction="NSW",
        as_at=date(2026, 7, 24),
        input={"rent_amount": "600"},
        findings=[{"rule_id": "nsw.bond_max_4_weeks", "verdict": "green"}],
        engine_version="1.0.0",
    )
    db_session.add(audit)
    await db_session.commit()
    stored = (await db_session.execute(select(Audit))).scalar_one()
    assert stored.findings[0]["verdict"] == "green"
    assert isinstance(stored.id, uuid.UUID)
