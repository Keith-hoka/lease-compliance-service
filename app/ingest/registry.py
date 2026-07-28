from sqlalchemy import select

from app.ingest.fetcher import LANDING_URL_TEMPLATE
from app.models import Act

NSW_INSTRUMENTS = [
    {
        "jurisdiction": "NSW",
        "slug": "act-2010-042",
        "title": "Residential Tenancies Act 2010",
    },
    {
        "jurisdiction": "NSW",
        "slug": "sl-2019-0629",
        "title": "Residential Tenancies Regulation 2019",
    },
]


async def ensure_act(session, instrument: dict) -> Act:
    """The registered row for a legislative instrument, created on first use."""
    act = (
        await session.execute(select(Act).where(Act.slug == instrument["slug"]))
    ).scalar_one_or_none()
    if act is None:
        act = Act(**instrument, source_url=LANDING_URL_TEMPLATE.format(slug=instrument["slug"]))
        session.add(act)
        await session.flush()
    return act
