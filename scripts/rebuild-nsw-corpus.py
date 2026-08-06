"""Wipe the two NSW instruments and re-ingest every cached version.

Run against the dev store by default; point DATABASE_URL elsewhere
(e.g. the production tunnel) to rebuild that store instead. The ingest
step is cache-first: all cached versions in data/raw/nsw are loaded;
only the landing-page version check touches the network.
"""

import asyncio

from sqlalchemy import delete, select

from app.core.db import async_session_factory
from app.models import Act, IngestedVersion, Section

NSW_SLUGS = ("act-2010-042", "sl-2019-0629")


async def wipe() -> None:
    async with async_session_factory() as session:
        for slug in NSW_SLUGS:
            act = (await session.execute(select(Act).where(Act.slug == slug))).scalar_one_or_none()
            if act is None:
                continue
            await session.execute(delete(Section).where(Section.act_id == act.id))
            await session.execute(delete(IngestedVersion).where(IngestedVersion.act_id == act.id))
            await session.execute(delete(Act).where(Act.id == act.id))
            print(f"wiped {slug}")
        await session.commit()


if __name__ == "__main__":
    asyncio.run(wipe())
