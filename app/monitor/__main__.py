"""Monitor legislation changes and re-audit monitored leases.

Usage: uv run python -m app.monitor {nsw|vic} [--skip-fetch]
"""

import argparse
import asyncio
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.dates import sydney_today
from app.core.db import async_session_factory
from app.ingest.fetcher import fetch_landing, fetch_versions, parse_version_dates
from app.ingest.fetcher_vic import VIC_CACHE_ROOT, docx_url, fetch_docx, list_versions
from app.ingest.loader import load_version
from app.ingest.parser import parse_whole_act
from app.ingest.parser_vic import parse_docx
from app.ingest.registry import INSTRUMENTS, NSW_INSTRUMENTS, ensure_act
from app.models import IngestedVersion
from app.monitor.runner import new_version_dates, run_monitor


async def refresh_corpus() -> None:
    """Fetch and load any legislation versions published since the last run.

    The sync Playwright fetchers run in a worker thread: the sync API refuses
    to run inside the event loop, and threads carry no loop of their own.
    """
    for instrument in NSW_INSTRUMENTS:
        landing = await asyncio.to_thread(fetch_landing, instrument["slug"])
        timeline = parse_version_dates(landing)
        async with async_session_factory() as session:
            act = await ensure_act(session, instrument)
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
                print(f"corpus: {instrument['slug']} no new versions")
                await session.commit()
                continue
            cache = Path("data/raw/nsw") / instrument["slug"]
            paths = await asyncio.to_thread(fetch_versions, instrument["slug"], missing, cache)
            for path in paths:
                version_date = date.fromisoformat(path.stem)
                if version_date not in missing:
                    continue
                stats = await load_version(
                    session, act.id, version_date, parse_whole_act(path.read_text())
                )
                print(f"corpus: {instrument['slug']} {version_date} {stats}")
            await session.commit()


async def refresh_corpus_vic(session_factory=async_session_factory) -> None:
    """Ingest any VIC versions published since the last run. No browser."""
    for instrument in INSTRUMENTS["vic"]:
        versions = await asyncio.to_thread(list_versions, instrument["landing_url"])
        async with session_factory() as session:
            act = await ensure_act(session, instrument)
            ingested = set(
                (
                    await session.execute(
                        select(IngestedVersion.version_date).where(IngestedVersion.act_id == act.id)
                    )
                )
                .scalars()
                .all()
            )
            missing = [v for v in versions if v.effective_date not in ingested]
            if not missing:
                print(f"corpus: {instrument['slug']} no new versions")
                await session.commit()
                continue
            cache_dir = VIC_CACHE_ROOT / instrument["slug"]
            for version in missing:
                url = await asyncio.to_thread(docx_url, instrument["landing_url"], version.number)
                data = await asyncio.to_thread(
                    fetch_docx, url, cache_dir / f"{version.number}.docx"
                )
                stats = await load_version(
                    session, act.id, version.effective_date, parse_docx(data)
                )
                print(f"corpus: {instrument['slug']} {version.effective_date} {stats}")
            await session.commit()


async def run(jurisdiction: str, skip_fetch: bool) -> None:
    if not skip_fetch:
        if jurisdiction == "nsw":
            await refresh_corpus()
        else:
            await refresh_corpus_vic()
    async with async_session_factory() as session:
        result = await run_monitor(session, jurisdiction.upper(), sydney_today())
    print(f"monitor: checked={result.checked} changed={len(result.changes)}")
    for change in result.changes:
        print(f"  {change.client_ref}: {change.changes}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jurisdiction", choices=["nsw", "vic"])
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.jurisdiction, args.skip_fetch))


if __name__ == "__main__":
    main()
