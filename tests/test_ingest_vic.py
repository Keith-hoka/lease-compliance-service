from datetime import date

import pytest
from sqlalchemy import select

from app.ingest.__main__ import load_all_vic
from app.ingest.fetcher_vic import VersionInfo
from app.models import Act, Section
from tests.test_parser_vic import HEAD, build_docx

V1 = build_docx(
    [
        (None, "Part 1—Preliminary"),
        (HEAD, "1\tPurposes"),
        (None, "Original body."),
    ]
)
V2 = build_docx(
    [
        (None, "Part 1—Preliminary"),
        (HEAD, "1\tPurposes"),
        (None, "Amended body."),
    ]
)
NO_PART_HEADING = build_docx(
    [
        (None, "1 Purposes"),
        (None, "Body with no Part heading."),
    ]
)


async def test_load_all_vic_builds_the_timeline(db_session, tmp_path, monkeypatch):
    from app.ingest import __main__ as ingest_main

    bytes_by_number = {"001": V1, "002": V2}
    monkeypatch.setattr(ingest_main, "docx_url", lambda landing, number: f"https://x/{number}.docx")
    monkeypatch.setattr(
        ingest_main,
        "fetch_docx",
        lambda url, cache_path: bytes_by_number[url.split("/")[-1].removesuffix(".docx")],
    )

    instrument = {
        "jurisdiction": "VIC",
        "slug": "residential-tenancies-act-1997",
        "title": "Residential Tenancies Act 1997",
        "landing_url": "https://x/landing",
    }
    versions = [
        VersionInfo("001", date(2021, 1, 1), "Superseded"),
        VersionInfo("002", date(2022, 1, 1), "In force"),
    ]
    await load_all_vic(db_session, instrument, versions, tmp_path)

    act = (
        await db_session.execute(select(Act).where(Act.slug == "residential-tenancies-act-1997"))
    ).scalar_one()
    rows = (
        (await db_session.execute(select(Section).where(Section.act_id == act.id))).scalars().all()
    )
    assert len(rows) == 2
    closed = next(r for r in rows if r.valid_to is not None)
    open_row = next(r for r in rows if r.valid_to is None)
    assert closed.valid_from == date(2021, 1, 1)
    assert closed.valid_to == date(2022, 1, 1)
    assert "Original" in closed.body_text
    assert open_row.valid_from == date(2022, 1, 1)
    assert "Amended" in open_row.body_text


async def test_load_all_vic_aborts_loudly_on_zero_sections(db_session, tmp_path, monkeypatch):
    from app.ingest import __main__ as ingest_main

    monkeypatch.setattr(ingest_main, "docx_url", lambda landing, number: f"https://x/{number}.docx")
    monkeypatch.setattr(ingest_main, "fetch_docx", lambda url, cache_path: NO_PART_HEADING)

    instrument = {
        "jurisdiction": "VIC",
        "slug": "residential-tenancies-act-1997",
        "title": "Residential Tenancies Act 1997",
        "landing_url": "https://x/landing",
    }
    versions = [VersionInfo("001", date(2021, 1, 1), "Superseded")]

    with pytest.raises(ValueError, match="parsed zero sections"):
        await load_all_vic(db_session, instrument, versions, tmp_path)
