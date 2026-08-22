from decimal import Decimal

from app.market_rent.estimate import Trend, trend, year_earlier
from app.models import RentStatistic


def _row(jurisdiction, period, median, **kw) -> RentStatistic:
    base = dict(  # noqa: C408
        jurisdiction=jurisdiction,
        period=period,
        area_code="2000" if jurisdiction == "NSW" else "Albert Park-Middle Park-West St Kilda",
        dwelling_type="unit",
        bedrooms=2,
        median=Decimal(median),
        p25=None,
        p75=None,
        sample_size=20,
        source_url="u",
    )
    base.update(kw)
    return RentStatistic(**base)


def test_year_earlier_for_month_and_quarter():
    assert year_earlier("2026-07") == "2025-07"
    assert year_earlier("2025-Q3") == "2024-Q3"


def test_trend_compares_with_the_same_period_a_year_earlier():
    series = [_row("NSW", "2026-07", 760), _row("NSW", "2026-01", 740), _row("NSW", "2025-07", 700)]
    assert trend(series) == Trend("2025-07", Decimal(700), Decimal("8.6"))


def test_trend_handles_a_fall_and_quarters():
    series = [_row("VIC", "2025-Q3", 643), _row("VIC", "2025-Q2", 638), _row("VIC", "2024-Q3", 650)]
    assert trend(series) == Trend("2024-Q3", Decimal(650), Decimal("-1.1"))


def test_trend_is_none_when_the_comparison_period_is_absent():
    assert trend([_row("NSW", "2026-07", 760), _row("NSW", "2026-06", 750)]) is None
    assert trend([]) is None
