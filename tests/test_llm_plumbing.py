from datetime import date

import pytest
from pydantic import ValidationError

from app.clause_audit.document import DocumentInput
from app.llm.client import build_parse_kwargs, document_block
from app.llm.prompts import SYSTEM, clause_instruction, fields_instruction
from app.llm.schemas import FieldsOutput, family_output_model

IDS = ["nsw.clause.carpet_cleaning", "nsw.clause.fumigation"]


def test_family_output_model_locks_rule_ids():
    model = family_output_model("ProhibitedOutput", IDS)
    parsed = model.model_validate(
        {
            "items": [
                {
                    "rule_id": "nsw.clause.carpet_cleaning",
                    "verdict": "red",
                    "reasoning": "found",
                    "clause_quote": "carpet professionally cleaned",
                }
            ]
        }
    )
    assert parsed.items[0].rule_id == "nsw.clause.carpet_cleaning"
    with pytest.raises(ValidationError):
        model.model_validate(
            {"items": [{"rule_id": "nsw.invented", "verdict": "red", "reasoning": "x"}]}
        )


def test_fields_output_locks_field_names():
    parsed = FieldsOutput.model_validate(
        {"fields": [{"field": "rent_amount", "document_value": "$560 per week", "quote": "x"}]}
    )
    assert parsed.fields[0].document_value == "$560 per week"
    with pytest.raises(ValidationError):
        FieldsOutput.model_validate(
            {"fields": [{"field": "made_up", "document_value": "1", "quote": None}]}
        )


def test_text_document_block_carries_cache_control():
    block = document_block(DocumentInput(kind="text", text="lease body"))
    assert block == {
        "type": "text",
        "text": "lease body",
        "cache_control": {"type": "ephemeral"},
    }


def test_pdf_document_block_is_base64_document():
    block = document_block(DocumentInput(kind="pdf", pdf=b"%PDF-fake"))
    assert block["type"] == "document"
    assert block["source"]["type"] == "base64"
    assert block["source"]["media_type"] == "application/pdf"
    assert block["cache_control"] == {"type": "ephemeral"}


def test_build_parse_kwargs_shape():
    doc = DocumentInput(kind="text", text="lease body")
    kwargs = build_parse_kwargs("claude-opus-4-8", doc, "judge these rules")
    assert kwargs["model"] == "claude-opus-4-8"
    assert kwargs["max_tokens"] == 8000
    assert kwargs["thinking"] == {"type": "adaptive"}
    assert kwargs["system"] == SYSTEM
    content = kwargs["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1] == {"type": "text", "text": "judge these rules"}


def test_clause_instruction_embeds_statute_and_rules():
    text = clause_instruction(
        "prohibited terms",
        date(2026, 7, 28),
        {("act-2010-042", "19"): "Prohibited terms\n(2) Terms having the following effects..."},
        [("nsw.clause.carpet_cleaning", "A term requiring professional carpet cleaning.")],
    )
    assert "2026-07-28" in text
    assert "Prohibited terms" in text
    assert "nsw.clause.carpet_cleaning" in text
    assert "act-2010-042 s 19" in text


def test_fields_instruction_lists_every_field():
    text = fields_instruction()
    for name in ("rent_amount", "break_fee_amount", "start_date"):
        assert name in text


def test_system_prompt_disclaims():
    assert "general information" in SYSTEM
    assert "not legal advice" in SYSTEM
