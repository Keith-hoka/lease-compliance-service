from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.clause_audit.document import DocumentInput
from app.llm.failover import FailoverJudge
from app.llm.prompts import RENT_SUGGESTION_SYSTEM, rent_suggestion_instruction
from app.rent_suggest.anchor import Anchor, MarketCell
from app.rent_suggest.judge import (
    HOLD_REASON_AT_CAP,
    HOLD_REASON_BLOCKED,
    evidence_block,
    evidence_numbers,
    suggest,
    suggestion_output_model,
)
from app.rent_suggest.law import LawCard
from app.rules.base import Finding
from app.schemas.lease import LeaseInput, RentIncrease

LEASE = LeaseInput(
    rent_amount=Decimal(600),
    rent_frequency="weekly",
    start_date=date(2024, 10, 1),
    end_date=date(2026, 9, 30),
    rent_increases=[RentIncrease(effective_on=date(2025, 10, 1), new_amount=Decimal(600))],
)
CELL = MarketCell(
    period="2026-07",
    median=Decimal(760),
    p25=Decimal("697.5"),
    p75=Decimal("886.25"),
    sample_size=170,
    fallback=None,
    series=[],
)
ANCHOR = Anchor(Decimal(650), Decimal(698), Decimal(748), "within", CELL)
LAW = LawCard(
    findings=[
        Finding(
            rule_id="nsw.rent_increase_frequency",
            verdict="green",
            summary="Increases at least 12 months apart.",
        )
    ],
    blocked=False,
)


def test_output_model_bounds_the_suggestion():
    model = suggestion_output_model(Decimal(698), Decimal(748))
    ok = model.model_validate({"suggested_weekly": "720", "reasoning": "x"})
    assert ok.suggested_weekly == Decimal(720)
    with pytest.raises(ValidationError):
        model.model_validate({"suggested_weekly": "760", "reasoning": "x"})
    with pytest.raises(ValidationError):
        model.model_validate({"suggested_weekly": "690", "reasoning": "x"})


def test_evidence_block_carries_numbers_and_no_tenant_fields():
    text = evidence_block(
        ANCHOR, "NSW", LEASE, LAW, "unit, 2 bedrooms, postcode 2000", date(2026, 8, 17)
    )
    for token in (
        "698",
        "748",
        "760",
        "170",
        "2026-07",
        "650",
        "600",
        "nsw.rent_increase_frequency",
    ):
        assert token in text
    assert "tenant" not in text.lower() or "tenancy" in text.lower()
    assert "As at: 2026-08-17" in text


def test_evidence_numbers_collects_every_money_figure():
    numbers = evidence_numbers(ANCHOR, LEASE, LAW)
    assert {
        Decimal(600),
        Decimal(650),
        Decimal(698),
        Decimal(748),
        Decimal(760),
        Decimal("697.5"),
        Decimal("886.25"),
    } <= numbers


def test_system_and_instruction_mention_range_and_citation_rule():
    assert "general information" in RENT_SUGGESTION_SYSTEM.lower()
    text = rent_suggestion_instruction(Decimal(698), Decimal(748), "above_cap")
    assert "698" in text and "748" in text and "upper" in text.lower()


async def test_suggest_skips_the_model_when_range_is_degenerate():
    calls = []

    async def never(doc, instruction, output_model):
        calls.append(1)

    judge = FailoverJudge(primary=never, primary_ref="claude-sonnet-5")
    held = Anchor(Decimal(600), Decimal(600), Decimal(600), "within", CELL)
    blocked = LawCard(findings=[], blocked=True)
    result = await suggest(judge, held, "NSW", LEASE, blocked, "unit", date(2026, 8, 17))
    assert result.suggested_weekly == Decimal(600) and result.model is None
    assert result.reasoning == HOLD_REASON_BLOCKED and calls == []


async def test_suggest_holds_at_cap_for_a_single_point_range_above_current():
    """A degenerate range is always mathematically determined - low == high is
    the one figure the judge could possibly return - so it never reaches the
    judge, regardless of cause. Not law-blocked and not below_current (the
    market sits exactly at the cap) gets its own template, HOLD_REASON_AT_CAP,
    suggesting anchor.low rather than the current rent.
    """
    calls = []

    async def never(doc, instruction, output_model):
        calls.append(1)
        return output_model.model_validate({"suggested_weekly": "690", "reasoning": "x"})

    judge = FailoverJudge(primary=never, primary_ref="claude-sonnet-5")
    single_point = Anchor(Decimal(600), Decimal(690), Decimal(690), "within", CELL)
    result = await suggest(judge, single_point, "NSW", LEASE, LAW, "unit", date(2026, 8, 17))
    assert calls == []
    assert result.suggested_weekly == Decimal(690)
    assert result.reasoning == HOLD_REASON_AT_CAP
    assert result.model is None


async def test_suggest_calls_the_judge_with_evidence_and_records_model():
    seen = {}

    async def fake(doc, instruction, output_model):
        seen["doc"] = doc
        seen["instruction"] = instruction
        return output_model.model_validate(
            {"suggested_weekly": "720", "reasoning": "Market median 760."}
        )

    judge = FailoverJudge(primary=fake, primary_ref="claude-sonnet-5")
    live = Anchor(Decimal(600), Decimal(698), Decimal(748), "within", CELL)
    result = await suggest(judge, live, "NSW", LEASE, LAW, "unit", date(2026, 8, 17))
    assert result.suggested_weekly == Decimal(720) and result.model == "claude-sonnet-5"
    assert isinstance(seen["doc"], DocumentInput) and seen["doc"].kind == "text"
    assert "760" in seen["doc"].text and "698" in seen["instruction"]


async def test_suggest_quantizes_the_judged_suggestion_to_whole_dollars():
    async def fake(doc, instruction, output_model):
        return output_model.model_validate(
            {"suggested_weekly": "720.40", "reasoning": "Market analysis."}
        )

    judge = FailoverJudge(primary=fake, primary_ref="claude-sonnet-5")
    live = Anchor(Decimal(600), Decimal(698), Decimal(748), "within", CELL)
    result = await suggest(judge, live, "NSW", LEASE, LAW, "unit", date(2026, 8, 17))
    assert result.suggested_weekly == Decimal(720)
