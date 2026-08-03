from sqlalchemy import select

from app.ingest.registry import INSTRUMENTS, NSW_INSTRUMENTS, VIC_INSTRUMENTS, ensure_act
from app.models import Act


def test_instrument_map_covers_both_jurisdictions():
    assert INSTRUMENTS["nsw"] is NSW_INSTRUMENTS
    assert INSTRUMENTS["vic"] is VIC_INSTRUMENTS


def test_every_instrument_carries_a_landing_url():
    for instruments in INSTRUMENTS.values():
        for instrument in instruments:
            assert instrument["landing_url"].startswith("https://")


def test_vic_instruments_pinned():
    slugs = [i["slug"] for i in VIC_INSTRUMENTS]
    assert slugs == [
        "residential-tenancies-act-1997",
        "residential-tenancies-regulations-2021",
    ]
    assert all(i["jurisdiction"] == "VIC" for i in VIC_INSTRUMENTS)


async def test_ensure_act_uses_landing_url(db_session):
    instrument = VIC_INSTRUMENTS[0]
    act = await ensure_act(db_session, instrument)
    await db_session.commit()
    stored = (
        await db_session.execute(select(Act).where(Act.slug == instrument["slug"]))
    ).scalar_one()
    assert stored.source_url == instrument["landing_url"]
    assert stored.jurisdiction == "VIC"
    again = await ensure_act(db_session, instrument)
    assert again.id == act.id
