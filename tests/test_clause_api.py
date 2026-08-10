import asyncio
import json
import uuid

import pytest

from app.core.config import settings
from app.models import ClauseAuditJob

KEY = {"X-API-Key": "test-key"}
OTHER = {"X-API-Key": "other-key"}
PAYLOAD = json.dumps({"jurisdiction": "NSW", "client_ref": "lease-9"})


@pytest.fixture(autouse=True)
async def api_key(seeded_tenants, monkeypatch):
    """Seeded tenants plus a configured anthropic key."""
    monkeypatch.setattr(settings, "anthropic_api_key", "unit-test-key")


async def test_disabled_returns_503(client, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    response = await client.post(
        "/v1/clause-audits", data={"payload": PAYLOAD, "text": "lease"}, headers=KEY
    )
    assert response.status_code == 503


async def test_missing_key_is_401(client):
    response = await client.post("/v1/clause-audits", data={"payload": PAYLOAD, "text": "x"})
    assert response.status_code == 401


async def test_invalid_payload_is_422(client):
    response = await client.post(
        "/v1/clause-audits", data={"payload": "not json", "text": "x"}, headers=KEY
    )
    assert response.status_code == 422


async def test_neither_or_both_inputs_is_422(client):
    neither = await client.post("/v1/clause-audits", data={"payload": PAYLOAD}, headers=KEY)
    both = await client.post(
        "/v1/clause-audits",
        data={"payload": PAYLOAD, "text": "x"},
        files={"file": ("l.pdf", b"%PDF-", "application/pdf")},
        headers=KEY,
    )
    assert neither.status_code == 422 and both.status_code == 422


async def test_oversize_text_is_413(client):
    response = await client.post(
        "/v1/clause-audits", data={"payload": PAYLOAD, "text": "x" * 200_001}, headers=KEY
    )
    assert response.status_code == 413


async def test_oversize_file_is_413(client):
    big = b"x" * (10 * 1024 * 1024 + 1)
    response = await client.post(
        "/v1/clause-audits",
        data={"payload": PAYLOAD},
        files={"file": ("l.pdf", big, "application/pdf")},
        headers=KEY,
    )
    assert response.status_code == 413


async def test_create_get_and_isolation(client, db_session):
    created = await client.post(
        "/v1/clause-audits", data={"payload": PAYLOAD, "text": "lease body"}, headers=KEY
    )
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "pending" and body["client_ref"] == "lease-9"
    assert body["model"] == settings.clause_audit_model

    job = await db_session.get(ClauseAuditJob, uuid.UUID(body["id"]))
    assert job.document == b"lease body" and job.document_kind == "text"

    fetched = await client.get(f"/v1/clause-audits/{body['id']}", headers=KEY)
    assert fetched.status_code == 200 and fetched.json()["status"] == "pending"

    foreign = await client.get(f"/v1/clause-audits/{body['id']}", headers=OTHER)
    assert foreign.status_code == 404

    listed = await client.get("/v1/clause-audits", params={"client_ref": "lease-9"}, headers=KEY)
    assert [row["id"] for row in listed.json()] == [body["id"]]


async def test_tenant_in_flight_cap_returns_429(client, db_session):
    from datetime import UTC, datetime

    for _ in range(10):
        db_session.add(
            ClauseAuditJob(
                client_id="testco",
                jurisdiction="NSW",
                as_at=datetime.now(UTC).date(),
                document=b"x",
                document_kind="text",
                engine_version="1.1.1",
                model="claude-opus-4-8",
            )
        )
    await db_session.commit()

    capped = await client.post(
        "/v1/clause-audits", data={"payload": PAYLOAD, "text": "lease"}, headers=KEY
    )
    assert capped.status_code == 429

    other = await client.post(
        "/v1/clause-audits", data={"payload": PAYLOAD, "text": "lease"}, headers=OTHER
    )
    assert other.status_code == 202


async def test_lifespan_sweeps_even_when_disabled(monkeypatch):
    from app import main

    swept = {"n": 0}

    async def spy_sweep():
        swept["n"] += 1

    started = {"n": 0}

    async def spy_loop(judge):
        started["n"] += 1

    monkeypatch.setattr(main, "sweep_stale", spy_sweep)
    monkeypatch.setattr(main, "worker_loop", spy_loop)
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    async with main.lifespan(main.app):
        pass
    assert swept["n"] == 1 and started["n"] == 0


async def test_lifespan_sweeps_and_starts_worker_when_enabled(monkeypatch):
    from app import main

    swept = {"n": 0}

    async def spy_sweep():
        swept["n"] += 1

    started = {"n": 0}

    async def spy_loop(judge):
        started["n"] += 1
        await asyncio.sleep(3600)

    monkeypatch.setattr(main, "sweep_stale", spy_sweep)
    monkeypatch.setattr(main, "worker_loop", spy_loop)
    monkeypatch.setattr(main, "make_judge", lambda: object())
    async with main.lifespan(main.app):
        await asyncio.sleep(0)
    assert swept["n"] == 1 and started["n"] == 1


async def test_post_worker_get_end_to_end(client, db_engine, fake_judge, monkeypatch):
    from datetime import date

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.clause_audit import rules as rules_module
    from app.clause_audit import worker
    from app.clause_audit.rules import ClauseRule
    from app.ingest.loader import load_version
    from app.ingest.parser import ParsedSection
    from app.models import Act
    from app.rules.base import SectionRef

    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        act = Act(
            jurisdiction="NSW",
            slug="act-2010-042",
            title="Residential Tenancies Act 2010",
            source_url="x",
        )
        session.add(act)
        await session.flush()
        await load_version(
            session,
            act.id,
            date(2011, 1, 31),
            [ParsedSection("19", "Prohibited terms", "terms body", "Part 2", None)],
        )
        await session.commit()

    carpet = "The tenant must have the carpet professionally cleaned at the end of the tenancy."
    rule = ClauseRule(
        rule_id="nsw.clause.carpet_cleaning",
        jurisdiction="NSW",
        family="prohibited",
        ref=SectionRef("act-2010-042", "19"),
        applies_from=date(2011, 1, 31),
        applies_to=None,
        question="A term requiring professional carpet cleaning.",
    )
    monkeypatch.setattr(rules_module, "PROHIBITED_RULES", [rule])
    fake_judge.responses["ProhibitedOutput"] = {
        "items": [
            {
                "rule_id": "nsw.clause.carpet_cleaning",
                "verdict": "red",
                "reasoning": "found",
                "clause_quote": carpet,
            }
        ]
    }

    created = await client.post(
        "/v1/clause-audits",
        data={"payload": PAYLOAD, "text": f"AGREEMENT. {carpet}"},
        headers=KEY,
    )
    assert created.status_code == 202

    assert await worker.run_once(fake_judge, factory) is True

    done = await client.get(f"/v1/clause-audits/{created.json()['id']}", headers=KEY)
    body = done.json()
    assert body["status"] == "succeeded"
    assert body["findings"][0]["verdict"] == "red"
    assert body["findings"][0]["clause_quote"] == carpet
    assert body["findings"][0]["citations"][0]["act"] == "Residential Tenancies Act 2010"


async def test_vic_clause_audit_accepted(client, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "unit-test-key")
    response = await client.post(
        "/v1/clause-audits",
        data={"payload": '{"jurisdiction": "VIC"}'},
        files={"file": ("l.pdf", b"%PDF-1.4 fake", "application/pdf")},
        headers=KEY,
    )
    assert response.status_code == 202
    assert response.json()["jurisdiction"] == "VIC"
