from datetime import date

import httpx
import pytest

from app.rent_stats import fetcher


def test_nsw_urls_follow_the_published_patterns():
    assert fetcher.nsw_monthly_url(2026, 7) == (
        "https://www.nsw.gov.au/sites/default/files/noindex/2026-08/rentalbond_lodgements_july_2026.xlsx"
    )
    assert fetcher.nsw_monthly_url(2025, 12) == (
        "https://www.nsw.gov.au/sites/default/files/noindex/2026-01/rentalbond_lodgements_december_2025.xlsx"
    )
    assert fetcher.nsw_annual_url(2025) == (
        "https://www.nsw.gov.au/sites/default/files/noindex/2026-01/rentalbond_lodgements_year_2025.xlsx"
    )


def test_vic_url_follows_the_published_pattern():
    assert fetcher.vic_quarter_url(2025, 3) == (
        "https://www.dffh.vic.gov.au/moving-annual-rent-suburb-september-quarter-2025-excel"
    )


def test_nsw_monthly_targets_stop_before_current_month():
    targets = fetcher.nsw_monthly_targets(today=date(2026, 8, 16), since=date(2026, 1, 1))
    names = [name for name, _ in targets]
    assert names[0] == "rentalbond_lodgements_january_2026.xlsx"
    assert names[-1] == "rentalbond_lodgements_july_2026.xlsx"
    assert len(names) == 7


async def test_fetch_returns_none_on_404():
    def handler(request):
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetcher.fetch(client, "https://example.test/missing.xlsx") is None


async def test_fetch_raises_on_server_error():
    def handler(request):
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError):
            await fetcher.fetch(client, "https://example.test/down.xlsx")
