"""Structured-output models. Rule ids are enum-locked so the model cannot invent rules."""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, create_model

FIELD_NAMES = (
    "rent_amount",
    "rent_frequency",
    "start_date",
    "end_date",
    "bond_amount",
    "rent_in_advance_amount",
    "holding_deposit_amount",
    "other_security_amount",
    "break_fee_amount",
)


def family_output_model(name: str, rule_ids: list[str]) -> type[BaseModel]:
    rule_enum = StrEnum(f"{name}RuleId", {rid.replace(".", "_"): rid for rid in rule_ids})
    item = create_model(
        f"{name}Item",
        rule_id=(rule_enum, ...),
        verdict=(Literal["red", "green", "yellow"], ...),
        reasoning=(str, ...),
        clause_quote=(str | None, None),
    )
    return create_model(name, items=(list[item], ...))


def standard_form_output_model(
    rule_ids: list[str], name: str = "StandardFormOutput"
) -> type[BaseModel]:
    """Build a batch-scoped output model whose rule_id is locked to rule_ids.

    name distinguishes one batch's model from another's (e.g. per-batch
    "StandardFormOutput1", "StandardFormOutput2") so judge call logs, which
    key on the output model's class name, are attributable to a batch.
    """
    rule_enum = StrEnum(f"{name}RuleId", {rid.replace(".", "_"): rid for rid in rule_ids})
    item = create_model(
        f"{name}Item",
        rule_id=(rule_enum, ...),
        outcome=(Literal["covered", "missing", "altered_adverse", "uncertain"], ...),
        reasoning=(str, ...),
        lease_quote=(str | None, None),
        departure=(str | None, None),
    )
    return create_model(name, items=(list[item], ...))


class FieldExtraction(BaseModel):
    field: Literal[
        "rent_amount",
        "rent_frequency",
        "start_date",
        "end_date",
        "bond_amount",
        "rent_in_advance_amount",
        "holding_deposit_amount",
        "other_security_amount",
        "break_fee_amount",
    ]
    document_value: str | None
    quote: str | None = None


class FieldsOutput(BaseModel):
    fields: list[FieldExtraction]
