"""Download the official rent workbooks by their published URL patterns."""

import calendar
from datetime import date

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.rent_stats.loader import load_nsw_file, load_vic_file

NSW_BASE = "https://www.nsw.gov.au/sites/default/files/noindex"
VIC_BASE = "https://www.dffh.vic.gov.au"
NSW_ANNUAL_YEARS = range(2021, 2026)
NSW_MONTHLY_SINCE = date(2026, 1, 1)
_VIC_QUARTER_MONTH = {1: "march", 2: "june", 3: "september", 4: "december"}


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def nsw_monthly_url(year: int, month: int) -> str:
    folder_year, folder_month = _next_month(year, month)
    name = calendar.month_name[month].lower()
    return f"{NSW_BASE}/{folder_year}-{folder_month:02d}/rentalbond_lodgements_{name}_{year}.xlsx"


def nsw_annual_url(year: int) -> str:
    return f"{NSW_BASE}/{year + 1}-01/rentalbond_lodgements_year_{year}.xlsx"


def vic_quarter_url(year: int, quarter: int) -> str:
    return (
        f"{VIC_BASE}/moving-annual-rent-suburb-{_VIC_QUARTER_MONTH[quarter]}-quarter-{year}-excel"
    )


def nsw_monthly_targets(today: date, since: date) -> list[tuple[str, str]]:
    """(source_file, url) for every complete month from `since` up to last month."""
    targets = []
    year, month = since.year, since.month
    while (year, month) < (today.year, today.month):
        url = nsw_monthly_url(year, month)
        targets.append((url.rsplit("/", 1)[1], url))
        year, month = _next_month(year, month)
    return targets


def quarter_candidates(today: date) -> list[tuple[int, int]]:
    """The 6 most recent completed VIC quarter-ends, newest first."""
    year, quarter = today.year, (today.month - 1) // 3 + 1
    candidates = []
    for _ in range(6):
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
        candidates.append((year, quarter))
    return candidates


async def fetch(client: httpx.AsyncClient, url: str) -> bytes | None:
    response = await client.get(url, follow_redirects=True, timeout=120)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


async def _load_nsw_targets(session, client, targets, summary):
    for name, url in targets:
        data = await fetch(client, url)
        if data is None:
            summary["nsw_missing"].append(name)
            continue
        result = await load_nsw_file(session, name, data, url)
        await session.commit()
        summary["nsw_files"] += 0 if result.unchanged else 1
        summary["nsw_rows"] += result.loaded_rows


async def _load_vic(session, client, today, summary):
    """Probe candidate quarters newest-first and load the first one published."""
    candidates = quarter_candidates(today)
    for year, quarter in candidates:
        url = vic_quarter_url(year, quarter)
        data = await fetch(client, url)
        if data is None:
            continue
        result = await load_vic_file(
            session, f"vic_moving_annual_{year}_q{quarter}.xlsx", data, url
        )
        await session.commit()
        summary["vic_files"] += 0 if result.unchanged else 1
        summary["vic_rows"] += result.loaded_rows
        summary["vic_quarter"] = f"{year}-Q{quarter}"
        return
    summary["vic_missing"].append(vic_quarter_url(*candidates[0]))


def _summary() -> dict:
    return {
        "nsw_files": 0,
        "nsw_rows": 0,
        "nsw_missing": [],
        "vic_files": 0,
        "vic_rows": 0,
        "vic_missing": [],
        "vic_quarter": None,
    }


async def run_backfill(session: AsyncSession, client: httpx.AsyncClient, today: date) -> dict:
    summary = _summary()
    annual = [(f"rentalbond_lodgements_year_{y}.xlsx", nsw_annual_url(y)) for y in NSW_ANNUAL_YEARS]
    await _load_nsw_targets(session, client, annual, summary)
    await _load_nsw_targets(session, client, nsw_monthly_targets(today, NSW_MONTHLY_SINCE), summary)
    await _load_vic(session, client, today, summary)
    return summary


async def run_update(session: AsyncSession, client: httpx.AsyncClient, today: date) -> dict:
    """Load anything new: recent NSW months (last three) and the current VIC quarter."""
    summary = _summary()
    year, month = today.year, today.month
    for _ in range(3):
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    since = date(year, month, 1)
    await _load_nsw_targets(session, client, nsw_monthly_targets(today, since), summary)
    await _load_vic(session, client, today, summary)
    return summary
