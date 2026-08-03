from sqlalchemy import select

from app.ingest.fetcher import LANDING_URL_TEMPLATE
from app.models import Act

NSW_INSTRUMENTS = [
    {
        "jurisdiction": "NSW",
        "slug": "act-2010-042",
        "title": "Residential Tenancies Act 2010",
        "landing_url": LANDING_URL_TEMPLATE.format(slug="act-2010-042"),
    },
    {
        "jurisdiction": "NSW",
        "slug": "sl-2019-0629",
        "title": "Residential Tenancies Regulation 2019",
        "landing_url": LANDING_URL_TEMPLATE.format(slug="sl-2019-0629"),
    },
]

VIC_INSTRUMENTS = [
    {
        "jurisdiction": "VIC",
        "slug": "residential-tenancies-act-1997",
        "title": "Residential Tenancies Act 1997",
        "landing_url": (
            "https://www.legislation.vic.gov.au/in-force/acts/residential-tenancies-act-1997"
        ),
    },
    {
        "jurisdiction": "VIC",
        "slug": "residential-tenancies-regulations-2021",
        "title": "Residential Tenancies Regulations 2021",
        "landing_url": (
            "https://www.legislation.vic.gov.au/in-force/statutory-rules/"
            "residential-tenancies-regulations-2021"
        ),
    },
]

INSTRUMENTS = {"nsw": NSW_INSTRUMENTS, "vic": VIC_INSTRUMENTS}


async def ensure_act(session, instrument: dict) -> Act:
    """The registered row for a legislative instrument, created on first use."""
    act = (
        await session.execute(select(Act).where(Act.slug == instrument["slug"]))
    ).scalar_one_or_none()
    if act is None:
        act = Act(
            jurisdiction=instrument["jurisdiction"],
            slug=instrument["slug"],
            title=instrument["title"],
            source_url=instrument["landing_url"],
        )
        session.add(act)
        await session.flush()
    return act
