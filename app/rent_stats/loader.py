"""Load parsed workbooks idempotently and aggregate NSW detail into statistics."""

import hashlib
from dataclasses import dataclass

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RentBondLodgement, RentSourceFile, RentStatistic
from app.rent_stats.parser import parse_nsw_lodgements, parse_vic_moving_annual

NSW_SOURCE_NAME = "NSW Fair Trading rental bond lodgements"
VIC_SOURCE_NAME = "Homes Victoria Rental Report (moving annual median rents by suburb)"


@dataclass(frozen=True)
class LoadResult:
    loaded_rows: int
    skipped_rows: int
    unknown_dwelling: int
    periods: list[str]
    unchanged: bool


async def _find_source_file(session, jurisdiction, source_file, data):
    """Return (existing_row_or_None, digest, unchanged). Never inserts or deletes -
    callers that need to replace a changed file get the old row back to read its
    detail before it is gone."""
    digest = hashlib.sha256(data).hexdigest()
    existing = (
        await session.execute(
            select(RentSourceFile).where(
                RentSourceFile.jurisdiction == jurisdiction,
                RentSourceFile.source_file == source_file,
            )
        )
    ).scalar_one_or_none()
    unchanged = existing is not None and existing.content_hash == digest
    return existing, digest, unchanged


async def _replace_source_file(session, existing, jurisdiction, source_file, digest, source_url):
    """Delete `existing` (cascading its detail rows) if given, then insert the new row."""
    if existing is not None:
        await session.delete(existing)
        await session.flush()
    row = RentSourceFile(
        jurisdiction=jurisdiction,
        source_file=source_file,
        content_hash=digest,
        source_url=source_url,
    )
    session.add(row)
    await session.flush()
    return row


async def load_nsw_file(
    session: AsyncSession, source_file: str, data: bytes, source_url: str
) -> LoadResult:
    existing, digest, unchanged = await _find_source_file(session, "NSW", source_file, data)
    if unchanged:
        return LoadResult(0, 0, 0, [], True)
    old_periods: list[str] = []
    if existing is not None:
        old_periods = (
            (
                await session.execute(
                    select(RentBondLodgement.period)
                    .where(RentBondLodgement.source_file_id == existing.id)
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
    row = await _replace_source_file(session, existing, "NSW", source_file, digest, source_url)
    parsed = parse_nsw_lodgements(data)
    session.add_all(
        RentBondLodgement(
            source_file_id=row.id,
            jurisdiction="NSW",
            period=r.period,
            postcode=r.postcode,
            dwelling_type=r.dwelling_type,
            bedrooms=r.bedrooms,
            weekly_rent=r.weekly_rent,
        )
        for r in parsed.rows
    )
    await session.flush()
    new_periods = sorted({r.period for r in parsed.rows})
    await aggregate_nsw(session, sorted(set(old_periods) | set(new_periods)), source_url)
    return LoadResult(
        len(parsed.rows), parsed.skipped_rows, parsed.unknown_dwelling, new_periods, False
    )


_NSW_AGGREGATE = text(
    """
    WITH base AS (
        SELECT period, postcode, dwelling_type, bedrooms, weekly_rent
        FROM rent_bond_lodgements
        WHERE jurisdiction = 'NSW' AND period = ANY(:periods)
    ),
    grouped AS (
        SELECT period, postcode, dwelling_type, bedrooms, weekly_rent FROM base
        UNION ALL SELECT period, postcode, dwelling_type, NULL, weekly_rent FROM base
        UNION ALL SELECT period, postcode, 'all', NULL, weekly_rent FROM base
    )
    INSERT INTO rent_statistics
        (id, jurisdiction, period, area_code, dwelling_type, bedrooms, median, p25, p75, sample_size, source_url)
    SELECT gen_random_uuid(), 'NSW', period, postcode, dwelling_type, bedrooms,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY weekly_rent),
           percentile_cont(0.25) WITHIN GROUP (ORDER BY weekly_rent),
           percentile_cont(0.75) WITHIN GROUP (ORDER BY weekly_rent),
           count(*), :source_url
    FROM grouped
    GROUP BY period, postcode, dwelling_type, bedrooms
    """
)


async def aggregate_nsw(session: AsyncSession, periods: list[str], source_url: str) -> int:
    """Recompute NSW statistics for the given periods from the detail table."""
    if not periods:
        return 0
    await session.execute(
        delete(RentStatistic).where(
            RentStatistic.jurisdiction == "NSW", RentStatistic.period.in_(periods)
        )
    )
    result = await session.execute(_NSW_AGGREGATE, {"periods": periods, "source_url": source_url})
    return result.rowcount


async def load_vic_file(
    session: AsyncSession, source_file: str, data: bytes, source_url: str
) -> LoadResult:
    existing, digest, unchanged = await _find_source_file(session, "VIC", source_file, data)
    if unchanged:
        return LoadResult(0, 0, 0, [], True)
    await _replace_source_file(session, existing, "VIC", source_file, digest, source_url)
    stats = parse_vic_moving_annual(data)
    periods = sorted({s.period for s in stats})
    await session.execute(
        delete(RentStatistic).where(
            RentStatistic.jurisdiction == "VIC", RentStatistic.period.in_(periods)
        )
    )
    session.add_all(
        RentStatistic(
            jurisdiction="VIC",
            period=s.period,
            area_code=s.area_code,
            dwelling_type=s.dwelling_type,
            bedrooms=s.bedrooms,
            median=s.median,
            p25=None,
            p75=None,
            sample_size=s.sample_size,
            source_url=source_url,
        )
        for s in stats
    )
    await session.flush()
    return LoadResult(len(stats), 0, 0, periods, False)
