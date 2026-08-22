"""Compose anchoring, the law card, and the judge into one suggestion.

The judge is built fresh per request via make_judge() (see the router),
which lets tests monkeypatch this module's make_judge reference. A side
effect: the FailoverJudge breaker state is per-request for this endpoint
rather than long-lived, which is acceptable for a synchronous, low-volume
endpoint. The worker's long-lived judge (app.main) is unaffected.
"""

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dates import sydney_today
from app.llm.client import make_judge
from app.rent_suggest.anchor import Anchor, anchor, is_stale, market_cell, period_end
from app.rent_suggest.judge import suggest
from app.rent_suggest.law import law_card
from app.rules import ENGINE_VERSION
from app.rules.base import to_weekly_rent
from app.schemas.rent_suggestions import (
    RentSuggestionRequest,
    RentSuggestionResponse,
    SuggestionMarket,
    SuggestionRange,
    SuggestionSource,
)

DISCLAIMER = "General information, not legal advice."
SOURCES = {
    "NSW": SuggestionSource(
        name="NSW Fair Trading rental bond lodgements",
        url="https://www.nsw.gov.au/housing-and-construction/rental-forms-surveys-and-data/rental-bond-data",
        licence="NSW Government open data (terms on the source page)",
    ),
    "VIC": SuggestionSource(
        name="Homes Victoria Rental Report (moving annual median rents by suburb)",
        url="https://www.dffh.vic.gov.au/publications/rental-report",
        licence="CC BY 4.0",
    ),
}


def _property_desc(request: RentSuggestionRequest) -> str:
    beds = (
        f", {request.property.bedrooms} bedrooms" if request.property.bedrooms is not None else ""
    )
    return f"{request.property.dwelling_type}{beds}, {request.property.area_key}"


def _market(anchored: Anchor, jurisdiction: str, as_at: date) -> SuggestionMarket | None:
    cell = anchored.market
    if cell is None:
        return None
    return SuggestionMarket(
        period=cell.period,
        period_end=period_end(cell.period),
        stale=is_stale(cell.period, as_at),
        median=cell.median,
        p25=cell.p25,
        p75=cell.p75,
        sample_size=cell.sample_size,
        fallback=cell.fallback,
        area_label=cell.area_label,
        source=SOURCES[jurisdiction],
    )


async def build_suggestion(
    session: AsyncSession, request: RentSuggestionRequest
) -> RentSuggestionResponse:
    """Build one rent suggestion.

    Passes failure_threshold=1 to make_judge(): the judge built here is
    per-request (see the module docstring), so it gets at most one call
    before it is discarded - it must switch to the backup on that call's
    first infrastructure failure rather than waiting to accumulate the
    worker's default 3 consecutive failures, which a single request can
    never reach.
    """
    as_at = request.as_at or sydney_today()
    current = to_weekly_rent(request.lease.rent_amount, request.lease.rent_frequency)
    cell = await market_cell(
        session,
        request.jurisdiction,
        request.property.area_key,
        request.property.dwelling_type,
        request.property.bedrooms,
    )
    anchored = anchor(current, request.jurisdiction, cell)
    midpoint = (anchored.low + anchored.high) / 2
    law = await law_card(
        session, request.jurisdiction, as_at, request.lease, request.renewal_start, midpoint
    )
    if law.blocked:
        anchored = Anchor(
            anchored.current_weekly,
            anchored.current_weekly,
            anchored.current_weekly,
            anchored.gap,
            anchored.market,
        )
    stale = is_stale(cell.period, as_at) if cell else False
    result = await suggest(
        make_judge(failure_threshold=1),
        anchored,
        request.jurisdiction,
        request.lease,
        law,
        _property_desc(request),
        as_at,
        stale,
    )
    return RentSuggestionResponse(
        current_weekly=anchored.current_weekly,
        suggested_weekly=result.suggested_weekly,
        range=SuggestionRange(low=anchored.low, high=anchored.high),
        market_gap=anchored.gap,
        market=_market(anchored, request.jurisdiction, as_at),
        law_card=law.findings,
        law_blocked=law.blocked,
        reasoning=result.reasoning,
        model=result.model,
        engine_version=ENGINE_VERSION,
        disclaimer=DISCLAIMER,
    )
