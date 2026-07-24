from datetime import date
from pathlib import Path

from app.ingest.fetcher import parse_version_dates

HTML = (Path(__file__).parent / "fixtures" / "landing_timeline.html").read_text()


def test_parses_timeline_dates_ascending():
    assert parse_version_dates(HTML) == [date(2010, 6, 17), date(2024, 10, 31), date(2026, 6, 10)]


def test_ignores_dates_outside_item_descriptions():
    dates = parse_version_dates(HTML)
    assert date(2026, 7, 24) not in dates
    assert date(2026, 7, 25) not in dates
    assert date(2011, 1, 6) not in dates
