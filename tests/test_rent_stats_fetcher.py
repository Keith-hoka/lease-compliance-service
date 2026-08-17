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


def test_nsw_annual_urls_are_the_published_irregular_paths():
    """Historical annual files do not follow one pattern; the URLs are pinned from the source page."""
    assert fetcher.nsw_annual_url(2021).endswith("/2023-11/Rental-bond-lodgements-year-2021.xlsx")
    assert fetcher.nsw_annual_url(2022).endswith("/2023-11/RentalBond_Lodgements_Year_2022.xlsx")
    assert fetcher.nsw_annual_url(2023).endswith("/2024-05/RentalBond_Lodgements_Year_2023.xlsx")
    assert fetcher.nsw_annual_url(2024).endswith("/2025-01/rental-bond-lodgements-year-2024_1.xlsx")
    assert [name for name, _ in fetcher.nsw_annual_targets()] == [
        f"rentalbond_lodgements_year_{y}.xlsx" for y in range(2021, 2026)
    ]


def test_nsw_annual_targets_exclude_years_monthly_coverage_already_reaches(monkeypatch):
    """A pinned annual year at or past NSW_MONTHLY_SINCE would double-count with the monthly loader."""
    patched = dict(fetcher.NSW_ANNUAL_PATHS)
    patched[2026] = "2027-01/rentalbond_lodgements_year_2026.xlsx"
    monkeypatch.setattr(fetcher, "NSW_ANNUAL_PATHS", patched)
    names = [name for name, _ in fetcher.nsw_annual_targets()]
    assert "rentalbond_lodgements_year_2026.xlsx" not in names
    assert names == [f"rentalbond_lodgements_year_{y}.xlsx" for y in range(2021, 2026)]


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


async def test_load_nsw_targets_accumulates_skip_and_unknown_counters(monkeypatch):
    from app.rent_stats.loader import LoadResult

    async def fake_load(session, source_file, data, source_url):
        return LoadResult(
            loaded_rows=5, skipped_rows=2, unknown_dwelling=1, periods=["2026-07"], unchanged=False
        )

    class Session:
        async def commit(self):
            pass

    def handler(request):
        return httpx.Response(200, content=b"workbook")

    monkeypatch.setattr(fetcher, "load_nsw_file", fake_load)
    summary = fetcher._summary()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await fetcher._load_nsw_targets(
            Session(), client, [("a.xlsx", "https://example.test/a.xlsx")], summary
        )
    assert summary["nsw_rows"] == 5
    assert summary["nsw_skipped_rows"] == 2
    assert summary["nsw_unknown_dwelling"] == 1


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
