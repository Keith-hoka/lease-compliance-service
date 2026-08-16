"""CLI: uv run python -m app.rent_stats backfill|update"""

import argparse
import asyncio
import json

import httpx

from app.core.dates import sydney_today
from app.core.db import async_session_factory
from app.rent_stats.fetcher import run_backfill, run_update


async def run(command: str) -> None:
    async with async_session_factory() as session, httpx.AsyncClient() as client:
        runner = run_backfill if command == "backfill" else run_update
        summary = await runner(session, client, sydney_today())
    print(json.dumps(summary))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["backfill", "update"])
    args = parser.parse_args()
    asyncio.run(run(args.command))


if __name__ == "__main__":
    main()
