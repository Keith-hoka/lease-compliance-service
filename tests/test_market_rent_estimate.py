from datetime import date
from decimal import Decimal

from app.market_rent.estimate import MarketEstimate, Trend, estimate, trend, year_earlier
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


AS_AT = date(2026, 8, 21)


def _nsw_year(area="2000", bedrooms=2, sample=170):
    rows = [
        _row("NSW", "2026-07", 760, p25=Decimal("697.5"), p75=Decimal("886.25"), sample_size=sample)
    ]
    rows += [
        _row("NSW", f"2026-{m:02d}", 750, p25=Decimal(690), p75=Decimal(880), sample_size=sample)
        for m in range(6, 0, -1)
    ]
    rows += [
        _row("NSW", f"2025-{m:02d}", 700, p25=Decimal(650), p75=Decimal(800), sample_size=sample)
        for m in range(12, 6, -1)
    ]
    for r in rows:
        r.area_code = area
        r.bedrooms = bedrooms
    return rows


async def test_nsw_estimate_band_trend_and_series(db_session):
    db_session.add_all(_nsw_year())
    await db_session.flush()
    result = await estimate(db_session, "NSW", "2000", "unit", 2, AS_AT)
    assert isinstance(result, MarketEstimate)
    assert result.estimate_weekly == Decimal(760)
    assert (result.band_low, result.band_high) == (Decimal(698), Decimal(886))
    assert result.period_end == date(2026, 7, 31)
    assert result.stale is False
    assert result.trend == Trend("2025-07", Decimal(700), Decimal("8.6"))
    assert len(result.cell.series) == 13
    assert result.cell.fallback is None


async def test_thin_exact_cell_falls_back_like_the_anchor(db_session):
    db_session.add_all(_nsw_year(bedrooms=2, sample=4) + _nsw_year(bedrooms=None, sample=170))
    await db_session.flush()
    result = await estimate(db_session, "NSW", "2000", "unit", 2, AS_AT)
    assert result.cell.fallback == "bedrooms_all"
    assert result.estimate_weekly == Decimal(760)


async def test_vic_estimate_resolves_the_grouped_label_and_is_stale(db_session):
    db_session.add_all(
        [
            _row("VIC", "2025-Q3", 643, sample_size=144),
            _row("VIC", "2025-Q2", 638, sample_size=140),
            _row("VIC", "2024-Q3", 650, sample_size=150),
        ]
    )
    await db_session.flush()
    result = await estimate(db_session, "VIC", "albert park", "unit", 2, AS_AT)
    assert result.cell.area_label == "Albert Park-Middle Park-West St Kilda"
    assert result.estimate_weekly == Decimal(643)
    assert (result.band_low, result.band_high) == (Decimal(592), Decimal(694))
    assert result.stale is True
    assert result.period_end == date(2025, 9, 30)
    assert result.trend == Trend("2024-Q3", Decimal(650), Decimal("-1.1"))


async def test_unresolvable_area_and_missing_rows_give_none(db_session):
    assert await estimate(db_session, "VIC", "Nowhere", "unit", 2, AS_AT) is None
    assert await estimate(db_session, "NSW", "2999", "unit", 2, AS_AT) is None
