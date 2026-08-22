from datetime import date
from decimal import Decimal

from app.models import RentStatistic
from app.rent_suggest.anchor import (
    MarketCell,
    anchor,
    band_for,
    dollars,
    is_stale,
    market_cell,
    period_end,
)


def _cell(**kw) -> MarketCell:
    base = dict(  # noqa: C408
        period="2026-07",
        median=Decimal(760),
        p25=Decimal("697.5"),
        p75=Decimal("886.25"),
        sample_size=170,
        fallback=None,
        series=[],
        area_label="2000",
    )
    base.update(kw)
    return MarketCell(**base)


def test_dollars_rounds_half_up():
    assert dollars(Decimal("697.5")) == Decimal(698)
    assert dollars(Decimal("886.25")) == Decimal(886)


def test_nsw_band_is_p25_p75_and_vic_band_is_median_pm_8pct():
    assert band_for("NSW", _cell()) == (Decimal(698), Decimal(886))
    vic = _cell(median=Decimal(643), p25=None, p75=None)
    assert band_for("VIC", vic) == (Decimal(592), Decimal(694))


def test_within_intersects_market_and_cap_bands():
    result = anchor(Decimal(650), "NSW", _cell())
    assert (result.low, result.high, result.gap) == (Decimal(698), Decimal(748), "within")


def test_within_can_collapse_to_a_single_point_above_current():
    """market_low == cap_high is still "within", not "below_current": the market
    isn't below current, it just leaves a one-point gap right at the cap.
    """
    cell = _cell(median=Decimal(795), p25=Decimal(690), p75=Decimal(900))
    result = anchor(Decimal(600), "NSW", cell)
    assert (result.low, result.high, result.gap) == (Decimal(690), Decimal(690), "within")


def test_above_cap_uses_cap_band():
    result = anchor(Decimal(500), "NSW", _cell())
    assert (result.low, result.high, result.gap) == (Decimal(500), Decimal(575), "above_cap")


def test_below_current_collapses_to_current():
    result = anchor(Decimal(950), "NSW", _cell())
    assert (result.low, result.high, result.gap) == (
        Decimal(950),
        Decimal(950),
        "below_current",
    )


def test_no_data_uses_cap_band():
    result = anchor(Decimal(600), "NSW", None)
    assert (result.low, result.high, result.gap, result.market) == (
        Decimal(600),
        Decimal(690),
        "no_data",
        None,
    )


def test_period_end_for_a_month():
    assert period_end("2026-07") == date(2026, 7, 31)


def test_period_end_for_each_quarter():
    assert period_end("2025-Q1") == date(2025, 3, 31)
    assert period_end("2025-Q2") == date(2025, 6, 30)
    assert period_end("2025-Q3") == date(2025, 9, 30)
    assert period_end("2025-Q4") == date(2025, 12, 31)


def test_is_stale_boundary_for_a_month_period():
    assert is_stale("2026-01", date(2026, 7, 31)) is False
    assert is_stale("2026-01", date(2026, 8, 1)) is True


def test_is_stale_boundary_for_a_quarter_period():
    assert is_stale("2025-Q3", date(2026, 3, 30)) is False
    assert is_stale("2025-Q3", date(2026, 3, 31)) is True


async def test_market_cell_prefers_exact_then_falls_back_when_thin(db_session):
    rows = [
        RentStatistic(
            jurisdiction="NSW",
            period="2026-07",
            area_code="2000",
            dwelling_type="unit",
            bedrooms=2,
            median=Decimal(800),
            p25=Decimal(750),
            p75=Decimal(850),
            sample_size=4,
            source_url="u",
        ),
        RentStatistic(
            jurisdiction="NSW",
            period="2026-07",
            area_code="2000",
            dwelling_type="unit",
            bedrooms=None,
            median=Decimal(760),
            p25=Decimal("697.5"),
            p75=Decimal("886.25"),
            sample_size=170,
            source_url="u",
        ),
        RentStatistic(
            jurisdiction="NSW",
            period="2026-06",
            area_code="2000",
            dwelling_type="unit",
            bedrooms=None,
            median=Decimal(750),
            p25=Decimal(690),
            p75=Decimal(880),
            sample_size=160,
            source_url="u",
        ),
    ]
    db_session.add_all(rows)
    await db_session.commit()
    cell = await market_cell(db_session, "NSW", "2000", "unit", 2)
    assert cell.fallback == "bedrooms_all" and cell.sample_size == 170
    assert [s.period for s in cell.series] == ["2026-07", "2026-06"]
    exact = await market_cell(db_session, "NSW", "2000", "unit", None)
    assert exact.fallback is None and exact.period == "2026-07"
    assert await market_cell(db_session, "NSW", "9999", "unit", 2) is None


async def test_market_cell_resolves_vic_grouped_label(db_session):
    db_session.add(
        RentStatistic(
            jurisdiction="VIC",
            period="2025-Q3",
            area_code="Albert Park-Middle Park-West St Kilda",
            dwelling_type="unit",
            bedrooms=2,
            median=Decimal(643),
            p25=None,
            p75=None,
            sample_size=120,
            source_url="u",
        )
    )
    await db_session.commit()
    cell = await market_cell(db_session, "VIC", "albert park", "unit", 2)
    assert cell.area_label == "Albert Park-Middle Park-West St Kilda"
    full = await market_cell(db_session, "VIC", "Albert Park-Middle Park-West St Kilda", "unit", 2)
    assert full.area_label == "Albert Park-Middle Park-West St Kilda"
    assert await market_cell(db_session, "VIC", "Nowhere", "unit", 2) is None


async def test_vic_falls_back_only_to_all(db_session):
    db_session.add(
        RentStatistic(
            jurisdiction="VIC",
            period="2025-Q3",
            area_code="Carlton",
            dwelling_type="all",
            bedrooms=None,
            median=Decimal(600),
            p25=None,
            p75=None,
            sample_size=900,
            source_url="u",
        )
    )
    await db_session.commit()
    cell = await market_cell(db_session, "VIC", "Carlton", "unit", 2)
    assert cell.fallback == "dwelling_all" and cell.median == Decimal(600)
