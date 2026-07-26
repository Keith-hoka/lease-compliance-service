from sqlalchemy import select

from app.ingest.fetcher import LANDING_URL_TEMPLATE
from app.models import Act

NSW_ACT = {
    "jurisdiction": "NSW",
    "slug": "act-2010-042",
    "title": "Residential Tenancies Act 2010",
}


async def ensure_act(session) -> Act:
    """The registered NSW act row, created on first use."""
    act = (
        await session.execute(select(Act).where(Act.slug == NSW_ACT["slug"]))
    ).scalar_one_or_none()
    if act is None:
        act = Act(**NSW_ACT, source_url=LANDING_URL_TEMPLATE.format(slug=NSW_ACT["slug"]))
        session.add(act)
        await session.flush()
    return act
