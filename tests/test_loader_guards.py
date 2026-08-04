from datetime import date

import pytest
from sqlalchemy import func, select

from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act, Section


def _ps(no, heading, body):
    return ParsedSection(no, heading, body, part=None, division=None)


async def _act(db_session) -> Act:
    act = Act(jurisdiction="NSW", slug="act-test", title="Test Act", source_url="http://x")
    db_session.add(act)
    await db_session.flush()
    return act


async def _section_count(db_session, act_id) -> int:
    return (
        await db_session.execute(
            select(func.count()).select_from(Section).where(Section.act_id == act_id)
        )
    ).scalar_one()


V1 = date(2010, 6, 17)
V2 = date(2020, 3, 23)


async def test_empty_sections_raises(db_session):
    act = await _act(db_session)
    with pytest.raises(ValueError, match=r"parsed zero sections"):
        await load_version(db_session, act.id, V1, [])
    assert await _section_count(db_session, act.id) == 0


async def test_duplicate_section_numbers_raises(db_session):
    act = await _act(db_session)
    with pytest.raises(ValueError, match=r"duplicate section numbers"):
        await load_version(db_session, act.id, V1, [_ps("1", "Name", "A"), _ps("1", "Name", "B")])
    assert await _section_count(db_session, act.id) == 0


async def test_out_of_order_version_raises(db_session):
    act = await _act(db_session)
    await load_version(db_session, act.id, V2, [_ps("1", "Name", "A")])
    before = await _section_count(db_session, act.id)

    with pytest.raises(ValueError, match=r"out-of-order ingest"):
        await load_version(db_session, act.id, V1, [_ps("1", "Name", "A")])

    assert await _section_count(db_session, act.id) == before
