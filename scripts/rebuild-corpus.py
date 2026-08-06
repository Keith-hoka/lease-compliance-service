"""Wipe the given instruments and re-ingest from cache.

Usage: PYTHONPATH=. uv run python scripts/rebuild-corpus.py <slug> [<slug> ...]
Point DATABASE_URL elsewhere (e.g. the production tunnel) to rebuild
that store. Follow with the matching ingest command
(python -m app.ingest nsw / vic) - it is cache-first.
"""

import asyncio
import sys

from sqlalchemy import delete, select

from app.core.db import async_session_factory
from app.models import Act, IngestedVersion, Section


async def wipe(slugs: list[str]) -> None:
    async with async_session_factory() as session:
        for slug in slugs:
            act = (await session.execute(select(Act).where(Act.slug == slug))).scalar_one_or_none()
            if act is None:
                print(f"absent {slug}")
                continue
            await session.execute(delete(Section).where(Section.act_id == act.id))
            await session.execute(delete(IngestedVersion).where(IngestedVersion.act_id == act.id))
            await session.execute(delete(Act).where(Act.id == act.id))
            print(f"wiped {slug}")
        await session.commit()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("usage: rebuild-corpus.py <slug> [<slug> ...]")
    asyncio.run(wipe(sys.argv[1:]))
