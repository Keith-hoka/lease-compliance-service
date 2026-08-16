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


def test_quarter_candidates_newest_completed_first():
    assert fetcher.quarter_candidates(date(2026, 8, 16))[:4] == [
        (2026, 2),
        (2026, 1),
        (2025, 4),
        (2025, 3),
    ]
    assert fetcher.quarter_candidates(date(2026, 1, 5))[0] == (2025, 4)
    assert fetcher.quarter_candidates(date(2026, 3, 31))[0] == (2025, 4)


async def test_load_vic_probes_back_to_the_newest_published_quarter(monkeypatch):
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if "september-quarter-2025" in request.url.path:
            return httpx.Response(200, content=b"workbook")
        return httpx.Response(404)

    loaded = []

    async def fake_load(session, source_file, data, source_url):
        loaded.append((source_file, source_url))
        from app.rent_stats.loader import LoadResult

        return LoadResult(
            loaded_rows=3, skipped_rows=0, unknown_dwelling=0, periods=["2025-Q3"], unchanged=False
        )

    class Session:
        async def commit(self):
            pass

    monkeypatch.setattr(fetcher, "load_vic_file", fake_load)
    summary = fetcher._summary()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await fetcher._load_vic(Session(), client, date(2026, 8, 16), summary)
    assert loaded == [("vic_moving_annual_2025_q3.xlsx", fetcher.vic_quarter_url(2025, 3))]
    assert (
        summary["vic_quarter"] == "2025-Q3"
        and summary["vic_files"] == 1
        and summary["vic_rows"] == 3
    )
    assert len(seen) == 4  # 2026-Q2, 2026-Q1, 2025-Q4 missed, 2025-Q3 hit


async def test_load_vic_records_missing_when_nothing_published():
    def handler(request):
        return httpx.Response(404)

    class Session:
        async def commit(self):
            pass

    summary = fetcher._summary()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await fetcher._load_vic(Session(), client, date(2026, 8, 16), summary)
    assert summary["vic_files"] == 0 and summary["vic_quarter"] is None
    assert summary["vic_missing"] == [fetcher.vic_quarter_url(2026, 2)]
