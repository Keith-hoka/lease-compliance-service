from contextlib import asynccontextmanager
from datetime import date

from sqlalchemy import select

from app.ingest.fetcher_vic import VersionInfo
from app.models import Act, Section
from tests.test_parser_vic import HEAD, build_docx

DOCX = build_docx(
    [
        (None, "Part 1—Preliminary"),
        (HEAD, "1\tPurposes"),
        (None, "Fresh body."),
    ]
)


@asynccontextmanager
async def db_session_context(session):
    yield session


async def test_refresh_corpus_vic_ingests_only_missing(db_session, tmp_path, monkeypatch, capsys):
    from app.monitor import __main__ as monitor_main

    monkeypatch.setattr(
        monitor_main,
        "list_versions",
        lambda landing_url: [VersionInfo("001", date(2021, 1, 1), "In force")],
    )
    monkeypatch.setattr(monitor_main, "docx_url", lambda landing, number: "https://x/1.docx")
    monkeypatch.setattr(monitor_main, "fetch_docx", lambda url, cache_path: DOCX)
    monkeypatch.setattr(monitor_main, "VIC_CACHE_ROOT", tmp_path)

    await monitor_main.refresh_corpus_vic(session_factory=lambda: db_session_context(db_session))

    acts = (await db_session.execute(select(Act))).scalars().all()
    assert {a.slug for a in acts} == {
        "residential-tenancies-act-1997",
        "residential-tenancies-regulations-2021",
    }
    sections = (await db_session.execute(select(Section))).scalars().all()
    assert len(sections) == 2

    await monitor_main.refresh_corpus_vic(session_factory=lambda: db_session_context(db_session))
    out = capsys.readouterr().out
    assert out.count("no new versions") == 2
