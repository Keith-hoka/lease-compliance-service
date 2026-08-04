import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingest.parser import ParsedSection
from app.models import IngestedVersion, Section


@dataclass(frozen=True)
class LoadStats:
    inserted: int
    closed: int
    skipped: bool


def content_hash(section: ParsedSection) -> str:
    return hashlib.sha256(f"{section.heading}\n{section.body_text}".encode()).hexdigest()


async def load_version(
    session: AsyncSession, act_id, version_date: date, sections: list[ParsedSection]
) -> LoadStats:
    """Apply one point-in-time version to the store (SCD-2, idempotent)."""
    already = await session.get(IngestedVersion, (act_id, version_date))
    if already is not None:
        return LoadStats(inserted=0, closed=0, skipped=True)

    if not sections:
        raise ValueError(f"act {act_id}: version {version_date} parsed zero sections")

    counts = Counter(s.section_no for s in sections)
    dupes = [n for n, c in counts.items() if c > 1]
    if dupes:
        raise ValueError(
            f"act {act_id}: version {version_date} has duplicate section numbers: {dupes}"
        )

    max_ingested = (
        await session.execute(
            select(func.max(IngestedVersion.version_date)).where(IngestedVersion.act_id == act_id)
        )
    ).scalar_one_or_none()
    if max_ingested is not None and version_date < max_ingested:
        raise ValueError(
            f"act {act_id}: out-of-order ingest - version {version_date} is before "
            f"latest ingested version {max_ingested}"
        )

    open_rows = (
        (
            await session.execute(
                select(Section).where(Section.act_id == act_id, Section.valid_to.is_(None))
            )
        )
        .scalars()
        .all()
    )
    current = {row.section_no: row for row in open_rows}
    incoming = {s.section_no: s for s in sections}

    inserted = closed = 0
    for no, row in current.items():
        replacement = incoming.get(no)
        if replacement is None or content_hash(replacement) != row.content_hash:
            row.valid_to = version_date
            closed += 1
    for no, parsed in incoming.items():
        existing = current.get(no)
        if existing is not None and content_hash(parsed) == existing.content_hash:
            continue
        session.add(
            Section(
                act_id=act_id,
                section_no=no,
                heading=parsed.heading,
                body_text=parsed.body_text,
                part=parsed.part,
                division=parsed.division,
                valid_from=version_date,
                valid_to=None,
                source_version_date=version_date,
                content_hash=content_hash(parsed),
            )
        )
        inserted += 1
    session.add(IngestedVersion(act_id=act_id, version_date=version_date))
    await session.flush()
    return LoadStats(inserted=inserted, closed=closed, skipped=False)
