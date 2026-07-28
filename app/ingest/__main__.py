"""Ingest each instrument's full point-in-time history: fetch, parse, load.

Usage: uv run python -m app.ingest nsw [--limit-versions N]
"""

import argparse
import asyncio
from datetime import date
from pathlib import Path

from app.core.db import async_session_factory
from app.ingest.fetcher import fetch_landing, fetch_versions, parse_version_dates
from app.ingest.loader import load_version
from app.ingest.parser import parse_whole_act
from app.ingest.registry import NSW_INSTRUMENTS, ensure_act


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
    for instrument in NSW_INSTRUMENTS:
        landing = await asyncio.to_thread(fetch_landing, instrument["slug"])
        dates = parse_version_dates(landing)
        if limit_versions:
            dates = dates[:limit_versions]
        cache = Path("data/raw/nsw") / instrument["slug"]
        paths = await asyncio.to_thread(fetch_versions, instrument["slug"], dates, cache)
        await load_all(instrument, paths)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jurisdiction", choices=["nsw"])
    parser.add_argument("--limit-versions", type=int, default=None)
    args = parser.parse_args()
    asyncio.run(run(args.limit_versions))


if __name__ == "__main__":
    main()
