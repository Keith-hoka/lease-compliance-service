from datetime import UTC, datetime, timedelta

from app.llm.failover import FailoverJudge
from app.main import app
from app.models import ClauseAuditJob


async def test_health_with_empty_queue(client):
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["clause_audit"] == {"pending": 0, "oldest_pending_seconds": None}
    assert "llm_failover" not in body


async def test_health_answers_head_requests(client):
    """Uptime monitors on free tiers probe with HEAD, not GET."""
    response = await client.head("/health")
    assert response.status_code == 200


async def test_health_reports_pending_queue(client, db_session):
    old = ClauseAuditJob(
        client_id="testco",
        jurisdiction="NSW",
        as_at=datetime.now(UTC).date(),
        document=b"x",
        document_kind="text",
        engine_version="1.1.1",
        model="claude-opus-4-8",
        created_at=datetime.now(UTC) - timedelta(seconds=120),
    )
    fresh = ClauseAuditJob(
        client_id="testco",
        jurisdiction="NSW",
        as_at=datetime.now(UTC).date(),
        document=b"x",
        document_kind="text",
        engine_version="1.1.1",
        model="claude-opus-4-8",
    )
    db_session.add_all([old, fresh])
    await db_session.commit()

    body = (await client.get("/health")).json()
    assert body["clause_audit"]["pending"] == 2
    assert body["clause_audit"]["oldest_pending_seconds"] >= 100


async def test_health_reports_failover_state(client, monkeypatch):
    async def ok(doc, instruction, output_model):
        return None

    judge = FailoverJudge(primary=ok, primary_ref="claude-sonnet-5")
    monkeypatch.setattr(app.state, "judge", judge, raising=False)
    body = (await client.get("/health")).json()
    assert body["llm_failover"] == {"state": "closed", "active_model": "claude-sonnet-5"}
