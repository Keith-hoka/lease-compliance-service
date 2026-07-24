"""Ingest an act's full point-in-time history: fetch, parse, load.

Usage: uv run python -m app.ingest nsw [--limit-versions N]
"""

import argparse
import asyncio
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.db import async_session_factory
from app.ingest.fetcher import (
    LANDING_URL_TEMPLATE,
    fetch_landing,
    fetch_versions,
    parse_version_dates,
)
from app.ingest.loader import load_version
from app.ingest.parser import parse_whole_act
from app.models import Act

NSW_ACT = {
    "jurisdiction": "NSW",
    "slug": "act-2010-042",
    "title": "Residential Tenancies Act 2010",
}


async def ensure_act(session) -> Act:
    act = (
        await session.execute(select(Act).where(Act.slug == NSW_ACT["slug"]))
    ).scalar_one_or_none()
    if act is None:
        act = Act(**NSW_ACT, source_url=LANDING_URL_TEMPLATE.format(slug=NSW_ACT["slug"]))
        session.add(act)
        await session.flush()
    return act


async def load_all(paths) -> None:
    async with async_session_factory() as session:
        act = await ensure_act(session)
        for path in paths:
            version_date = date.fromisoformat(path.stem)
            sections = parse_whole_act(path.read_text())
            stats = await load_version(session, act.id, version_date, sections)
            print(f"{version_date}: sections={len(sections)} {stats}")
        await session.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jurisdiction", choices=["nsw"])
    parser.add_argument("--limit-versions", type=int, default=None)
    args = parser.parse_args()

    landing = fetch_landing(NSW_ACT["slug"])
    dates = parse_version_dates(landing)
    if args.limit_versions:
        dates = dates[: args.limit_versions]
    cache = Path("data/raw/nsw") / NSW_ACT["slug"]
    paths = fetch_versions(NSW_ACT["slug"], dates, cache)
    asyncio.run(load_all(paths))


main()
