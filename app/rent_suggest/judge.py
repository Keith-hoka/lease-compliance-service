"""One judge call that picks a figure inside a pre-computed range and explains it."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, create_model

from app.clause_audit.document import DocumentInput
from app.llm.failover import FailoverJudge
from app.llm.prompts import RENT_SUGGESTION_SYSTEM, rent_suggestion_instruction
from app.rent_suggest.anchor import Anchor, dollars
from app.rent_suggest.law import LawCard
from app.schemas.lease import LeaseInput

HOLD_REASON_BLOCKED = (
    "A rent increase at this renewal would breach a rent-increase rule (see the law card), "
    "so the suggestion is to hold the current rent."
)
HOLD_REASON_BELOW = (
    "The market band for comparable properties sits below the current rent, so the "
    "suggestion is to hold the current rent."
)
HOLD_REASON_AT_CAP = (
    "The market band meets the allowed cap at a single figure, so that figure is the suggestion."
)


@dataclass(frozen=True)
class Suggestion:
    suggested_weekly: Decimal
    reasoning: str
    model: str | None


def suggestion_output_model(low: Decimal, high: Decimal) -> type[BaseModel]:
    return create_model(
        "RentSuggestionOutput",
        suggested_weekly=(Decimal, Field(ge=low, le=high)),
        reasoning=(str, ...),
    )


def evidence_numbers(anchor: Anchor, lease: LeaseInput, law: LawCard) -> set[Decimal]:
    numbers = {anchor.current_weekly, anchor.low, anchor.high, lease.rent_amount}
    for inc in lease.rent_increases or []:
        numbers.add(inc.new_amount)
    if anchor.market:
        for row in anchor.market.series or [anchor.market]:
            for value in (row.median, row.p25, row.p75):
                if value is not None:
                    numbers.add(Decimal(value))
        numbers.update(
            {anchor.market.median, *(v for v in (anchor.market.p25, anchor.market.p75) if v)}
        )
    return numbers


def _history_lines(lease: LeaseInput) -> list[str]:
    lines = [
        f"- tenancy started {lease.start_date:%Y-%m}; current rent {lease.rent_amount} per {lease.rent_frequency}"
    ]
    previous = None
    for inc in lease.rent_increases or []:
        pct = f" (+{((inc.new_amount / previous) - 1) * 100:.1f}%)" if previous else ""
        lines.append(f"- {inc.effective_on:%Y-%m}: rent {inc.new_amount}{pct}")
        previous = inc.new_amount
    return lines


def evidence_block(
    anchor: Anchor,
    jurisdiction: str,
    lease: LeaseInput,
    law: LawCard,
    property_desc: str,
    as_at: date,
) -> str:
    parts = [
        "<evidence>",
        f"As at: {as_at.isoformat()}",
        f"Property: {property_desc} ({jurisdiction}).",
        f"Current weekly rent: {anchor.current_weekly}.",
        f"Allowed range: {anchor.low} to {anchor.high} weekly; market gap: {anchor.gap}.",
    ]
    if anchor.market:
        cell = anchor.market
        note = f" (fallback: {cell.fallback})" if cell.fallback else ""
        parts.append(f"Market cell used: period {cell.period}, sample {cell.sample_size}{note}.")
        for row in cell.series or [cell]:
            pct = f", p25 {row.p25}, p75 {row.p75}" if row.p25 is not None else ""
            parts.append(f"- {row.period}: median {row.median}{pct}, n={row.sample_size}")
    else:
        parts.append("Market: no statistics for this area.")
    parts.append("Rent history:")
    parts.extend(_history_lines(lease))
    parts.append("Legal check (deterministic rules):")
    parts.extend(f"- {f.rule_id}: {f.verdict} - {f.summary}" for f in law.findings)
    parts.append("</evidence>")
    return "\n".join(parts)


async def suggest(
    judge: FailoverJudge,
    anchor: Anchor,
    jurisdiction: str,
    lease: LeaseInput,
    law: LawCard,
    property_desc: str,
    as_at: date,
    stale: bool,
) -> Suggestion:
    if anchor.low == anchor.high:
        if law.blocked:
            return Suggestion(anchor.current_weekly, HOLD_REASON_BLOCKED, None)
        if anchor.gap == "below_current":
            return Suggestion(anchor.current_weekly, HOLD_REASON_BELOW, None)
        return Suggestion(anchor.low, HOLD_REASON_AT_CAP, None)
    doc = DocumentInput(
        kind="text",
        text=evidence_block(anchor, jurisdiction, lease, law, property_desc, as_at),
        system=RENT_SUGGESTION_SYSTEM,
    )
    output = await judge(
        doc,
        rent_suggestion_instruction(anchor.low, anchor.high, anchor.gap, stale),
        suggestion_output_model(anchor.low, anchor.high),
    )
    used = judge.drain_models_used()
    return Suggestion(
        dollars(output.suggested_weekly), output.reasoning, used[-1] if used else None
    )
