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


def test_create_rejects_other_jurisdiction():
    with pytest.raises(ValidationError):
        ClauseAuditCreate.model_validate({"jurisdiction": "VIC"})
