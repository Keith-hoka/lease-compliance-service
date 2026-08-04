"""Ingest each instrument's full point-in-time history: fetch, parse, load.

Usage: uv run python -m app.ingest {nsw|vic} [--limit-versions N]
"""

import argparse
import asyncio
from datetime import date
from pathlib import Path

from app.core.db import async_session_factory
from app.ingest.fetcher import fetch_landing, fetch_versions, parse_version_dates
from app.ingest.fetcher_vic import docx_url, fetch_docx, list_versions
from app.ingest.loader import load_version
from app.ingest.parser import parse_whole_act
from app.ingest.parser_vic import parse_docx
from app.ingest.registry import INSTRUMENTS, ensure_act


async def load_all(instrument: dict, paths) -> None:
    async with async_session_factory() as session:
        act = await ensure_act(session, instrument)
        for path in paths:
            version_date = date.fromisoformat(path.stem)
            sections = parse_whole_act(path.read_text())
            stats = await load_version(session, act.id, version_date, sections)
            print(f"{instrument['slug']} {version_date}: sections={len(sections)} {stats}")
        await session.commit()


async def run(limit_versions: int | None) -> None:
    """One pass over every instrument; fetches run in a worker thread because
    sync Playwright refuses the event loop and a second asyncio.run would reuse
    the engine's pooled connections across dead loops."""
    for instrument in INSTRUMENTS["nsw"]:
        landing = await asyncio.to_thread(fetch_landing, instrument["slug"])
        dates = parse_version_dates(landing)
        if limit_versions:
            dates = dates[:limit_versions]
        cache = Path("data/raw/nsw") / instrument["slug"]
        paths = await asyncio.to_thread(fetch_versions, instrument["slug"], dates, cache)
        await load_all(instrument, paths)


async def load_all_vic(session, instrument: dict, versions, cache_dir: Path) -> None:
    act = await ensure_act(session, instrument)
    for version in versions:
        url = await asyncio.to_thread(docx_url, instrument["landing_url"], version.number)
        data = await asyncio.to_thread(fetch_docx, url, cache_dir / f"{version.number}.docx")
        sections = parse_docx(data)
        if not sections:
            raise ValueError(f"{instrument['slug']} version {version.number}: parsed zero sections")
        stats = await load_version(session, act.id, version.effective_date, sections)
        print(f"{instrument['slug']} {version.effective_date}: sections={len(sections)} {stats}")
    await session.commit()


async def run_vic(limit_versions: int | None) -> None:
    for instrument in INSTRUMENTS["vic"]:
        versions = await asyncio.to_thread(list_versions, instrument["landing_url"])
        if limit_versions:
            versions = versions[-limit_versions:]
        cache_dir = Path("data/raw/vic") / instrument["slug"]
        async with async_session_factory() as session:
            await load_all_vic(session, instrument, versions, cache_dir)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jurisdiction", choices=["nsw", "vic"])
    parser.add_argument("--limit-versions", type=int, default=None)
    args = parser.parse_args()
    if args.jurisdiction == "vic":
        asyncio.run(run_vic(args.limit_versions))
    else:
        asyncio.run(run(args.limit_versions))


if __name__ == "__main__":
    main()
