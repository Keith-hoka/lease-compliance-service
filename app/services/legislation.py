from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Act, Section


async def section_at(
    session: AsyncSession, act_slug: str, section_no: str, as_at: date
) -> Section | None:
    """The section text in force at as_at, or None."""
    query = (
        select(Section)
        .join(Act, Act.id == Section.act_id)
        .where(
            Act.slug == act_slug,
            Section.section_no == section_no,
            Section.valid_from <= as_at,
            (Section.valid_to.is_(None)) | (Section.valid_to > as_at),
        )
    )
    return (await session.execute(query)).scalar_one_or_none()
