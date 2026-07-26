from datetime import date

from app.ingest.loader import LoadStats, load_version
from app.ingest.parser import ParsedSection
from app.models import Act
from app.services.legislation import section_at


def _ps(no, heading, body):
    return ParsedSection(no, heading, body, part=None, division=None)


async def _act(db_session) -> Act:
    act = Act(jurisdiction="NSW", slug="act-test", title="Test Act", source_url="http://x")
    db_session.add(act)
    await db_session.flush()
    return act


V1 = date(2010, 6, 17)
V2 = date(2020, 3, 23)
V3 = date(2025, 5, 19)


async def test_scd2_windows(db_session):
    act = await _act(db_session)
    s1 = await load_version(
        db_session, act.id, V1, [_ps("1", "Name", "Old body"), _ps("2", "Two", "B")]
    )
    assert s1 == LoadStats(inserted=2, closed=0, skipped=False)
    s2 = await load_version(
        db_session, act.id, V2, [_ps("1", "Name", "New body"), _ps("2", "Two", "B")]
    )
    assert s2 == LoadStats(inserted=1, closed=1, skipped=False)
    await db_session.commit()

    old = await section_at(db_session, "act-test", "1", date(2015, 1, 1))
    new = await section_at(db_session, "act-test", "1", date(2024, 1, 1))
    assert old.body_text == "Old body" and old.valid_to == V2
    assert new.body_text == "New body" and new.valid_to is None
    unchanged = await section_at(db_session, "act-test", "2", date(2024, 1, 1))
    assert unchanged.valid_from == V1 and unchanged.valid_to is None


async def test_removed_and_added_sections(db_session):
    act = await _act(db_session)
    await load_version(db_session, act.id, V1, [_ps("1", "Name", "A"), _ps("9", "Gone", "X")])
    await load_version(db_session, act.id, V2, [_ps("1", "Name", "A"), _ps("10", "New", "Y")])
    await db_session.commit()
    assert (await section_at(db_session, "act-test", "9", date(2024, 1, 1))) is None
    assert (await section_at(db_session, "act-test", "9", date(2015, 1, 1))).valid_to == V2
    assert (await section_at(db_session, "act-test", "10", date(2024, 1, 1))).valid_from == V2


async def test_idempotent_rerun(db_session):
    act = await _act(db_session)
    await load_version(db_session, act.id, V1, [_ps("1", "Name", "A")])
    again = await load_version(db_session, act.id, V1, [_ps("1", "Name", "A")])
    assert again.skipped is True


async def test_before_first_version_is_none(db_session):
    act = await _act(db_session)
    await load_version(db_session, act.id, V1, [_ps("1", "Name", "A")])
    assert (await section_at(db_session, "act-test", "1", date(2009, 1, 1))) is None


async def test_ensure_act_creates_then_reuses(db_session):
    from app.ingest.registry import NSW_ACT, ensure_act

    created = await ensure_act(db_session)
    assert created.slug == NSW_ACT["slug"]
    again = await ensure_act(db_session)
    assert again.id == created.id
