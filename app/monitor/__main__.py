"""Monitor legislation changes and re-audit monitored leases.

Usage: uv run python -m app.monitor nsw [--skip-fetch]
"""

import argparse
import asyncio
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.dates import sydney_today
from app.core.db import async_session_factory
from app.ingest.fetcher import fetch_landing, fetch_versions, parse_version_dates
from app.ingest.loader import load_version
from app.ingest.parser import parse_whole_act
from app.ingest.registry import NSW_ACT, ensure_act
from app.models import IngestedVersion
from app.monitor.runner import new_version_dates, run_monitor


async def refresh_corpus() -> None:
    """Fetch and load any legislation versions published since the last run.

    The sync Playwright fetchers run in a worker thread: the sync API refuses
    to run inside the event loop, and threads carry no loop of their own.
    """
    landing = await asyncio.to_thread(fetch_landing, NSW_ACT["slug"])
    timeline = parse_version_dates(landing)
    async with async_session_factory() as session:
        act = await ensure_act(session)
        ingested = set(
            (
                await session.execute(
                    select(IngestedVersion.version_date).where(IngestedVersion.act_id == act.id)
                )
            )
            .scalars()
            .all()
        )
        missing = new_version_dates(timeline, ingested)
        if not missing:
            print("corpus: no new versions")
            await session.commit()
            return
        cache = Path("data/raw/nsw") / NSW_ACT["slug"]
        paths = await asyncio.to_thread(fetch_versions, NSW_ACT["slug"], missing, cache)
        for path in paths:
            version_date = date.fromisoformat(path.stem)
            if version_date not in missing:
                continue
            stats = await load_version(
                session, act.id, version_date, parse_whole_act(path.read_text())
            )
            print(f"corpus: {version_date} {stats}")
        await session.commit()


async def run(skip_fetch: bool) -> None:
    if not skip_fetch:
        await refresh_corpus()
    async with async_session_factory() as session:
        result = await run_monitor(session, NSW_ACT["jurisdiction"], sydney_today())
    print(f"monitor: checked={result.checked} changed={len(result.changes)}")
    for change in result.changes:
        print(f"  {change.client_ref}: {change.changes}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jurisdiction", choices=["nsw"])
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.skip_fetch))


if __name__ == "__main__":
    main()
