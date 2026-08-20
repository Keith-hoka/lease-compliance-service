"""Golden scenarios for the renewal rent-suggestion feature.

Each scenario seeds its own RentStatistic rows and carries the exact
deterministic expectations that app.rent_suggest.anchor.anchor and
app.rent_suggest.law.law_card must produce for it, with no model involved
(see the structural test in tests/test_golden.py). The LLM eval
(tests/test_llm_eval.py) runs the full pipeline including the judge and
grades it against RS_GATE.

direction is the net deterministic outcome once the service's law-blocked
collapse is taken into account: "hold" when the market gap is already
below_current or the law card blocks any increase (both make suggest()
return the hold template without calling a model); "increase" otherwise,
when the judge picks a figure inside a genuine low-high band.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal

from app.models import RentStatistic
from app.schemas.lease import LeaseInput, RentIncrease
from app.schemas.rent_suggestions import SuggestionProperty

_MONEY = re.compile(r"\$?(\d[\d,]*(?:\.\d+)?)")


def money_figures(text: str) -> set[Decimal]:
    return {Decimal(m.replace(",", "")) for m in _MONEY.findall(text)}


RS_GATE = 0.9


@dataclass(frozen=True)
class Scenario:
    name: str
    jurisdiction: Literal["NSW", "VIC"]
    as_at: date
    market_rows: list[RentStatistic]
    lease: LeaseInput
    property: SuggestionProperty
    renewal_start: date
    expected_gap: Literal["within", "above_cap", "below_current", "no_data"]
    expected_range: tuple[Decimal, Decimal]
    direction: Literal["increase", "hold"]


_AS_AT = date(2026, 8, 17)
_RENEWAL = date(2026, 10, 1)
_OLD_START = date(2015, 1, 1)


def _stat(
    jurisdiction: str,
    area: str,
    dwelling: str,
    bedrooms: int | None,
    *,
    median,
    p25=None,
    p75=None,
    sample_size: int = 150,
    period: str = "2026-07",
) -> RentStatistic:
    return RentStatistic(
        jurisdiction=jurisdiction,
        period=period,
        area_code=area,
        dwelling_type=dwelling,
        bedrooms=bedrooms,
        median=Decimal(median),
        p25=Decimal(p25) if p25 is not None else None,
        p75=Decimal(p75) if p75 is not None else None,
        sample_size=sample_size,
        source_url="u",
    )


def _lease(**kw) -> LeaseInput:
    base = dict(  # noqa: C408
        rent_amount=Decimal(600), rent_frequency="weekly", start_date=_OLD_START
    )
    base.update(kw)
    return LeaseInput(**base)


def _prop(area: str, dwelling: str, bedrooms: int | None) -> SuggestionProperty:
    return SuggestionProperty(area_key=area, dwelling_type=dwelling, bedrooms=bedrooms)


SCENARIOS: list[Scenario] = [
    # -- NSW within, three sample cells --------------------------------
    Scenario(
        name="nsw_within_unit_2br",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[_stat("NSW", "2000", "unit", 2, median=750, p25=700, p75=820)],
        lease=_lease(rent_amount=Decimal(650)),
        property=_prop("2000", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="within",
        expected_range=(Decimal(700), Decimal(748)),
        direction="increase",
    ),
    Scenario(
        name="nsw_within_unit_1br",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[_stat("NSW", "2010", "unit", 1, median=500, p25=460, p75=540)],
        lease=_lease(rent_amount=Decimal(480)),
        property=_prop("2010", "unit", 1),
        renewal_start=_RENEWAL,
        expected_gap="within",
        expected_range=(Decimal(480), Decimal(540)),
        direction="increase",
    ),
    Scenario(
        name="nsw_within_house_3br",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[_stat("NSW", "2020", "house", 3, median=900, p25=850, p75=980)],
        lease=_lease(rent_amount=Decimal(870)),
        property=_prop("2020", "house", 3),
        renewal_start=_RENEWAL,
        expected_gap="within",
        expected_range=(Decimal(870), Decimal(980)),
        direction="increase",
    ),
    # -- NSW above_cap ---------------------------------------------------
    Scenario(
        name="nsw_above_cap_unit_2br",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[_stat("NSW", "2030", "unit", 2, median=900, p25=850, p75=950)],
        lease=_lease(rent_amount=Decimal(600)),
        property=_prop("2030", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="above_cap",
        expected_range=(Decimal(600), Decimal(690)),
        direction="increase",
    ),
    Scenario(
        name="nsw_above_cap_house_4br",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[_stat("NSW", "2040", "house", 4, median=1200, p25=1100, p75=1300)],
        lease=_lease(rent_amount=Decimal(800)),
        property=_prop("2040", "house", 4),
        renewal_start=_RENEWAL,
        expected_gap="above_cap",
        expected_range=(Decimal(800), Decimal(920)),
        direction="increase",
    ),
    # -- NSW below_current -------------------------------------------------
    Scenario(
        name="nsw_below_current_unit_2br",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[_stat("NSW", "2050", "unit", 2, median=500, p25=460, p75=540)],
        lease=_lease(rent_amount=Decimal(600)),
        property=_prop("2050", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="below_current",
        expected_range=(Decimal(600), Decimal(600)),
        direction="hold",
    ),
    # -- NSW no_data -------------------------------------------------------
    Scenario(
        name="nsw_no_data_unit_2br",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[],
        lease=_lease(rent_amount=Decimal(600)),
        property=_prop("2060", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="no_data",
        expected_range=(Decimal(600), Decimal(690)),
        direction="increase",
    ),
    # -- NSW thin-sample fallback (2) ---------------------------------------
    Scenario(
        name="nsw_thin_falls_back_to_bedrooms_all",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[
            _stat("NSW", "2500", "unit", 2, median=700, p25=650, p75=760, sample_size=4),
            _stat("NSW", "2500", "unit", None, median=680, p25=630, p75=740, sample_size=120),
        ],
        lease=_lease(rent_amount=Decimal(600)),
        property=_prop("2500", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="within",
        expected_range=(Decimal(630), Decimal(690)),
        direction="increase",
    ),
    Scenario(
        name="nsw_thin_used_when_no_fallback_exists",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[_stat("NSW", "2560", "unit", 3, median=720, p25=690, p75=760, sample_size=3)],
        lease=_lease(rent_amount=Decimal(650)),
        property=_prop("2560", "unit", 3),
        renewal_start=_RENEWAL,
        expected_gap="within",
        expected_range=(Decimal(690), Decimal(748)),
        direction="increase",
    ),
    # -- NSW stale market period, still used deterministically --------------
    Scenario(
        name="nsw_stale_market_period_still_used",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[
            _stat(
                "NSW",
                "2600",
                "house",
                3,
                median=620,
                p25=580,
                p75=660,
                sample_size=40,
                period="2023-02",
            )
        ],
        lease=_lease(rent_amount=Decimal(600)),
        property=_prop("2600", "house", 3),
        renewal_start=_RENEWAL,
        expected_gap="within",
        expected_range=(Decimal(600), Decimal(660)),
        direction="increase",
    ),
    # -- monthly / fortnightly frequency conversion (2) ----------------------
    Scenario(
        name="nsw_monthly_rent_converts_to_weekly",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[_stat("NSW", "2610", "unit", 2, median=700, p25=650, p75=760)],
        lease=_lease(rent_amount=Decimal(2600), rent_frequency="monthly"),
        property=_prop("2610", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="within",
        expected_range=(Decimal(650), Decimal(690)),
        direction="increase",
    ),
    Scenario(
        name="vic_fortnightly_rent_converts_to_weekly",
        jurisdiction="VIC",
        as_at=_AS_AT,
        market_rows=[_stat("VIC", "Brunswick", "unit", 2, median=640)],
        lease=_lease(rent_amount=Decimal(1200), rent_frequency="fortnightly"),
        property=_prop("Brunswick", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="within",
        expected_range=(Decimal(600), Decimal(690)),
        direction="increase",
    ),
    # -- VIC within / above_cap / below_current (3) --------------------------
    Scenario(
        name="vic_within_unit_2br",
        jurisdiction="VIC",
        as_at=_AS_AT,
        market_rows=[_stat("VIC", "Carlton", "unit", 2, median=600)],
        lease=_lease(rent_amount=Decimal(580)),
        property=_prop("Carlton", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="within",
        expected_range=(Decimal(580), Decimal(648)),
        direction="increase",
    ),
    Scenario(
        name="vic_above_cap_house_3br",
        jurisdiction="VIC",
        as_at=_AS_AT,
        market_rows=[_stat("VIC", "Richmond", "house", 3, median=900)],
        lease=_lease(rent_amount=Decimal(600)),
        property=_prop("Richmond", "house", 3),
        renewal_start=_RENEWAL,
        expected_gap="above_cap",
        expected_range=(Decimal(600), Decimal(690)),
        direction="increase",
    ),
    Scenario(
        name="vic_below_current_unit_1br",
        jurisdiction="VIC",
        as_at=_AS_AT,
        market_rows=[_stat("VIC", "Fitzroy", "unit", 1, median=500)],
        lease=_lease(rent_amount=Decimal(600)),
        property=_prop("Fitzroy", "unit", 1),
        renewal_start=_RENEWAL,
        expected_gap="below_current",
        expected_range=(Decimal(600), Decimal(600)),
        direction="hold",
    ),
    # -- law_blocked frequency, NSW and VIC (2) ------------------------------
    Scenario(
        name="nsw_law_blocked_frequency_under_12_months",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[],
        lease=_lease(
            rent_amount=Decimal(600),
            start_date=date(2023, 1, 1),
            rent_increases=[RentIncrease(effective_on=date(2026, 4, 1), new_amount=Decimal(600))],
        ),
        property=_prop("2620", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="no_data",
        expected_range=(Decimal(600), Decimal(690)),
        direction="hold",
    ),
    Scenario(
        name="vic_law_blocked_frequency_under_12_months",
        jurisdiction="VIC",
        as_at=_AS_AT,
        market_rows=[],
        lease=_lease(
            rent_amount=Decimal(600),
            start_date=date(2023, 1, 1),
            rent_increases=[RentIncrease(effective_on=date(2026, 4, 1), new_amount=Decimal(600))],
        ),
        property=_prop("Preston", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="no_data",
        expected_range=(Decimal(600), Decimal(690)),
        direction="hold",
    ),
    # -- NSW first-year rule (1) ----------------------------------------------
    Scenario(
        name="nsw_law_blocked_first_year_increase",
        jurisdiction="NSW",
        as_at=_AS_AT,
        market_rows=[],
        lease=_lease(rent_amount=Decimal(600), start_date=date(2026, 3, 1)),
        property=_prop("2630", "unit", 2),
        renewal_start=_RENEWAL,
        expected_gap="no_data",
        expected_range=(Decimal(600), Decimal(690)),
        direction="hold",
    ),
    # -- NSW fixed-term disclosure, s42 (repealed 2024-12-13) (1) --------------
    Scenario(
        name="nsw_law_blocked_fixed_term_disclosure",
        jurisdiction="NSW",
        as_at=date(2024, 1, 1),
        market_rows=[],
        lease=_lease(
            rent_amount=Decimal(600),
            start_date=date(2023, 1, 1),
            end_date=date(2024, 6, 1),
            fixed_term_increase_in_agreement=False,
        ),
        property=_prop("2640", "unit", 2),
        renewal_start=date(2024, 3, 1),
        expected_gap="no_data",
        expected_range=(Decimal(600), Decimal(690)),
        direction="hold",
    ),
]
