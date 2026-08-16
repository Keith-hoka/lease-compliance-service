from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select

from app.models import RentBondLodgement, RentStatistic
from app.rent_stats.loader import load_nsw_file, load_vic_file

FIXTURES = Path(__file__).parent / "fixtures" / "rent_stats"
NSW = (FIXTURES / "nsw_lodgements_sample.xlsx").read_bytes()
VIC = (FIXTURES / "vic_moving_annual_sample.xlsx").read_bytes()
URL = "https://example.test/src.xlsx"


async def _count(session, model):
    return (await session.execute(select(func.count()).select_from(model))).scalar_one()


async def _stat(session, **where):
    stmt = select(RentStatistic).filter_by(**where)
    return (await session.execute(stmt)).scalar_one()


async def test_nsw_load_inserts_detail_and_aggregates(db_session):
    result = await load_nsw_file(db_session, "nsw_july_2026.xlsx", NSW, URL)
    await db_session.commit()
    assert (result.loaded_rows, result.skipped_rows, result.unknown_dwelling) == (20, 2, 1)
    assert result.periods == ["2026-07"] and result.unchanged is False
    assert await _count(db_session, RentBondLodgement) == 20

    unit_2000 = await _stat(
        db_session,
        jurisdiction="NSW",
        period="2026-07",
        area_code="2000",
        dwelling_type="unit",
        bedrooms=None,
    )
    assert unit_2000.sample_size == 6
    assert unit_2000.median == Decimal(760)
    assert unit_2000.p25 == Decimal("697.5") and unit_2000.p75 == Decimal("886.25")

    house_2150 = await _stat(
        db_session,
        jurisdiction="NSW",
        period="2026-07",
        area_code="2150",
        dwelling_type="house",
        bedrooms=None,
    )
    assert house_2150.sample_size == 5 and house_2150.median == Decimal(660)

    all_2000 = await _stat(
        db_session,
        jurisdiction="NSW",
        period="2026-07",
        area_code="2000",
        dwelling_type="all",
        bedrooms=None,
    )
    assert all_2000.sample_size == 9


async def test_nsw_reload_same_hash_is_noop(db_session):
    await load_nsw_file(db_session, "nsw_july_2026.xlsx", NSW, URL)
    await db_session.commit()
    again = await load_nsw_file(db_session, "nsw_july_2026.xlsx", NSW, URL)
    await db_session.commit()
    assert again.unchanged is True
    assert await _count(db_session, RentBondLodgement) == 20


async def test_nsw_changed_hash_replaces_file_rows(db_session):
    await load_nsw_file(db_session, "nsw_july_2026.xlsx", NSW, URL)
    await db_session.commit()
    trimmed = _drop_last_row(NSW)
    result = await load_nsw_file(db_session, "nsw_july_2026.xlsx", trimmed, URL)
    await db_session.commit()
    assert result.unchanged is False and result.loaded_rows == 19
    assert await _count(db_session, RentBondLodgement) == 19


async def test_vic_load_upserts_published_medians(db_session):
    result = await load_vic_file(db_session, "vic_sep_2025.xlsx", VIC, URL)
    await db_session.commit()
    assert result.unchanged is False and result.loaded_rows > 0
    stat = await _stat(
        db_session,
        jurisdiction="VIC",
        period="2025-Q3",
        area_code="Albert Park-Middle Park-West St Kilda",
        dwelling_type="unit",
        bedrooms=2,
    )
    assert (stat.median, stat.sample_size, stat.p25, stat.p75) == (Decimal(643), 144, None, None)
    again = await load_vic_file(db_session, "vic_sep_2025.xlsx", VIC, URL)
    assert again.unchanged is True


def _drop_last_row(data: bytes) -> bytes:
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data))
    ws = wb.worksheets[0]
    ws.delete_rows(ws.max_row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
