import pytest
from pydantic import ValidationError

from app.rules.base import Finding
from app.schemas.clause_audit import ClauseAuditCreate, ClauseFinding, ClauseLeaseInput


def test_finding_accepts_yellow():
    f = Finding(rule_id="nsw.clause.carpet_cleaning", verdict="yellow", summary="unsure")
    assert f.verdict == "yellow"


def test_clause_finding_carries_quote():
    f = ClauseFinding(
        rule_id="nsw.clause.carpet_cleaning",
        verdict="red",
        summary="found",
        clause_quote="carpet professionally cleaned",
    )
    assert f.clause_quote == "carpet professionally cleaned"
    assert f.model_dump(mode="json")["clause_quote"]


def test_clause_lease_input_all_optional():
    assert ClauseLeaseInput().rent_amount is None


def test_create_payload_defaults():
    body = ClauseAuditCreate.model_validate({"jurisdiction": "NSW"})
    assert body.as_at is None and body.lease is None


def test_create_accepts_vic_and_rejects_unsupported():
    body = ClauseAuditCreate.model_validate({"jurisdiction": "VIC"})
    assert body.jurisdiction == "VIC"
    with pytest.raises(ValidationError):
        ClauseAuditCreate.model_validate({"jurisdiction": "QLD"})


def test_strict_schema_closes_objects_and_requires_all_fields():
    from app.llm.schemas import family_output_model, strict_schema

    model = family_output_model(
        "ProhibitedOutput", ["nsw.clause.carpet_cleaning", "nsw.clause.fumigation"]
    )
    schema = strict_schema(model)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["items"]
    item = schema["$defs"]["ProhibitedOutputItem"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"rule_id", "verdict", "reasoning", "clause_quote"}
    assert "default" not in item["properties"]["clause_quote"]


def test_strict_schema_output_still_validates_nullable_fields():
    from app.llm.schemas import family_output_model, strict_schema

    model = family_output_model("ProhibitedOutput", ["nsw.clause.fumigation"])
    strict_schema(model)
    parsed = model.model_validate(
        {
            "items": [
                {
                    "rule_id": "nsw.clause.fumigation",
                    "verdict": "green",
                    "reasoning": "absent",
                    "clause_quote": None,
                }
            ]
        }
    )
    assert parsed.items[0].clause_quote is None
