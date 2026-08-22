"""Resolve a consumer's area key to the area_code the statistics carry."""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RentStatistic

logger = logging.getLogger(__name__)


def normalise(key: str) -> str:
    """Collapse whitespace and casefold: ' albert  park ' -> 'albert park'."""
    return " ".join(key.split()).casefold()


async def published_areas(session: AsyncSession, jurisdiction: str) -> list[str]:
    """Distinct area_code values for the jurisdiction, sorted."""
    stmt = (
        select(RentStatistic.area_code).where(RentStatistic.jurisdiction == jurisdiction).distinct()
    )
    rows = (await session.execute(stmt)).scalars().all()
    return sorted(rows)


def match_label(key: str, labels: list[str]) -> str | None:
    """First label equal to the key or whose '-'-separated parts contain it.

    Comparison uses normalise() on both sides. More than one match is
    logged as a warning and the first (sorted) label wins.
    """
    target = normalise(key)
    matches = sorted(
        label
        for label in labels
        if target == normalise(label) or target in (normalise(part) for part in label.split("-"))
    )
    if not matches:
        return None
    if len(matches) > 1:
        logger.warning("area key %r matches more than one label: %s", key, matches)
    return matches[0]


async def resolve_area(session: AsyncSession, jurisdiction: str, area_key: str) -> str | None:
    """NSW: the stripped key unchanged (postcodes are exact).
    VIC: match_label(area_key, published_areas(session, 'VIC')) or None."""
    if jurisdiction == "NSW":
        return area_key.strip()
    labels = await published_areas(session, jurisdiction)
    return match_label(area_key, labels)
