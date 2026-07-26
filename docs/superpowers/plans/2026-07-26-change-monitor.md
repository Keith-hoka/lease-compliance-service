# Change Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A monitor that re-runs each tenant's monitored leases at today's date, records verdict changes as pollable `audit_changes` rows, and keeps the corpus fresh — with tenant isolation added to the API so multiple companies can use it.

**Architecture:** API keys become `key:client_id` pairs; audits carry `(client_id, client_ref)`. A CLI (`python -m app.monitor nsw`) refreshes the corpus (headed Chrome, reusing the V1 fetcher/parser/loader), re-runs the latest audit per `(client_id, client_ref)` at `sydney_today()`, diffs `{rule_id: verdict}` maps with a pure function, and persists a new audit plus an `audit_changes` row only when the diff is non-empty. `GET /v1/audit-changes` serves tenant-scoped changes ascending by `created_at`.

**Tech Stack:** Existing V1 stack (FastAPI, async SQLAlchemy 2.0, Alembic, PostgreSQL 5433, Playwright headed Chrome, uv, pytest).

## Global Constraints

- `uv` only: `uv run ...`, `uv add ...` — never `python3`/`pip`.
- Ruff sequence before every push, in this exact order from the repo root: `uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`.
- No emojis in code, logs, or prints. Docstrings over inline comments. Do not overengineer.
- Every task ends: full `uv run pytest -q` -> ruff sequence -> commit -> `git push origin main` -> CI green -> report -> WAIT for user approval.
- `jurisdiction` stays a first-class dimension; the monitor CLI takes it as an argument.
- Tests and CI never fetch the live site; monitor tests exercise the no-fetch paths only.
- Local dev DB creds are `rental:rental@localhost:5433` (defaults in code); CI overrides via `TEST_DATABASE_URL`.
- Tenant identity always comes from the authenticated key server-side, never from the request body.
- The corpus-gated eval reuses the `corpus_session` skip-guard fixture from `tests/test_rules_nsw.py` and must clean up every row it writes to the dev store.
- Working directory is always `/Users/keithho/LLMProjects/lease-compliance-service`.

## File Structure

| Path | Responsibility |
|---|---|
| `app/core/auth.py` | modify: parse `key:client_id` pairs, return `client_id` |
| `app/core/dates.py` | new: `sydney_today()` |
| `app/models/audit.py` | modify: `client_id`/`client_ref` on `Audit`; new `AuditChange` |
| `app/models/__init__.py` | modify: export `AuditChange` |
| `alembic/versions/<gen>_monitor.py` | new: columns + `audit_changes` + indexes |
| `app/ingest/registry.py` | new: `NSW_ACT`, `ensure_act` (moved out of `__main__`) |
| `app/ingest/__main__.py` | modify: import from registry, `__main__` guard |
| `app/monitor/__init__.py` | new: empty |
| `app/monitor/runner.py` | new: `diff_findings`, `new_version_dates`, `latest_monitored_audits`, `run_monitor`, `MonitorResult` |
| `app/monitor/__main__.py` | new: CLI `python -m app.monitor nsw [--skip-fetch]` |
| `app/routers/audits.py` | modify: `ClientDep`, stamp tenant keys, tenant-scoped GET |
| `app/routers/changes.py` | new: `GET /v1/audit-changes` |
| `app/schemas/audit.py` | modify: `client_ref` fields; `AuditChangeInfo` |
| `app/main.py` | modify: mount changes router |
| `tests/test_auth.py` | new: key parsing/401s |
| `tests/test_monitor.py` | new: diff units, runner scenarios, corpus temporal eval |
| `tests/test_models.py` | modify: `AuditChange` round trip |
| `tests/test_api.py` | modify: labelled keys, `client_ref`, tenant isolation, changes endpoint |
| `README.md` | modify: `API_KEYS` pairs, monitor command, polling example |

---

### Task 1: Auth returns tenant identity

**Files:**
- Modify: `app/core/auth.py`
- Create: `tests/test_auth.py`
- Modify: `tests/test_api.py` (the `api_key` fixture only)

**Interfaces:**
- Consumes: `settings.api_keys` (existing str field).
- Produces: `require_api_key(x_api_key: str = Header(default="")) -> str` returning the authenticated `client_id`. Existing router-level `dependencies=[Depends(require_api_key)]` keeps working (return value unused until Task 7).

- [x] **Step 1: Write the failing tests** — `tests/test_auth.py`:

```python
import pytest
from fastapi import HTTPException

from app.core.auth import require_api_key
from app.core.config import settings


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "abc123:rentalapp, xyz789:acme")


def test_valid_key_returns_client_id():
    assert require_api_key("abc123") == "rentalapp"


def test_second_key_maps_to_its_tenant():
    assert require_api_key("xyz789") == "acme"


def test_unknown_key_is_401():
    with pytest.raises(HTTPException) as excinfo:
        require_api_key("nope")
    assert excinfo.value.status_code == 401


def test_missing_key_is_401():
    with pytest.raises(HTTPException):
        require_api_key("")


def test_unlabelled_entry_is_unusable(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "bare-key")
    with pytest.raises(HTTPException):
        require_api_key("bare-key")
```

- [x] **Step 2: Run -> fail.** `uv run pytest tests/test_auth.py -q` — expect failures: current `require_api_key` returns `None` and accepts any configured bare key ("abc123:rentalapp" is currently treated as one whole key, so `require_api_key("abc123")` raises where the test expects "rentalapp").

- [x] **Step 3: Implement** — replace the body of `app/core/auth.py`:

```python
from fastapi import Header, HTTPException

from app.core.config import settings


def _client_ids_by_key() -> dict[str, str]:
    """Map api key -> client_id from comma-separated key:client_id pairs."""
    entries = (entry.split(":", 1) for entry in settings.api_keys.split(",") if ":" in entry)
    return {key.strip(): client_id.strip() for key, client_id in entries}


def require_api_key(x_api_key: str = Header(default="")) -> str:
    client_id = _client_ids_by_key().get(x_api_key)
    if not x_api_key or client_id is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return client_id
```

- [x] **Step 4: Update the API fixture** — in `tests/test_api.py` change the autouse fixture to the pair format (single line change):

```python
@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "test-key:testco,other-key:otherco")
```

- [x] **Step 5: Run -> pass.** `uv run pytest tests/test_auth.py tests/test_api.py -q` — all pass (API routes still use the router-level dependency; the return value is ignored for now).

- [x] **Step 6: Full suite; ruff; commit** (`Return tenant identity from API key auth`); push; CI green. Report and WAIT.

---

### Task 2: Tenant columns, AuditChange model, migration

**Files:**
- Modify: `app/models/audit.py`, `app/models/__init__.py`
- Create: `alembic/versions/<generated>_monitor.py`
- Modify: `tests/test_models.py`

**Interfaces:**
- Consumes: `Base`, existing `Audit`.
- Produces: `Audit.client_id: str` (indexed, server_default `"legacy"`), `Audit.client_ref: str | None` (indexed); `AuditChange(id, client_id, client_ref, old_audit_id, new_audit_id, changes: dict, created_at)` exported from `app.models`.

- [x] **Step 1: Failing test** — append to `tests/test_models.py`:

```python
async def test_audit_change_round_trip(db_session):
    audit = Audit(
        jurisdiction="NSW",
        as_at=date(2026, 7, 24),
        input={"rent_amount": "600"},
        findings=[],
        engine_version="1.0.0",
        client_id="rentalapp",
        client_ref="lease-1",
    )
    db_session.add(audit)
    await db_session.flush()
    change = AuditChange(
        client_id="rentalapp",
        client_ref="lease-1",
        old_audit_id=audit.id,
        new_audit_id=audit.id,
        changes={"nsw.bond_max_4_weeks": {"from": "green", "to": "red"}},
    )
    db_session.add(change)
    await db_session.commit()
    stored = (await db_session.execute(select(AuditChange))).scalar_one()
    assert stored.changes["nsw.bond_max_4_weeks"]["to"] == "red"
    assert stored.created_at is not None
```

Update the imports line in `tests/test_models.py`:

```python
from app.models import Act, Audit, AuditChange, IngestedVersion, Section
```

- [x] **Step 2: Run -> fail** (ImportError: cannot import `AuditChange`).

- [x] **Step 3: Implement models** — in `app/models/audit.py`, add to `Audit`:

```python
    client_id: Mapped[str] = mapped_column(String(50), index=True, server_default="legacy")
    client_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
```

and add the new model (plus `ForeignKey` to the existing sqlalchemy import):

```python
class AuditChange(Base):
    __tablename__ = "audit_changes"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(50), index=True)
    client_ref: Mapped[str] = mapped_column(String(100), index=True)
    old_audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audits.id"))
    new_audit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("audits.id"))
    changes: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

`app/models/__init__.py` becomes:

```python
from app.models.audit import Audit, AuditChange
from app.models.legislation import Act, IngestedVersion, Section

__all__ = ["Act", "Audit", "AuditChange", "IngestedVersion", "Section"]
```

- [x] **Step 4: Migration** — `uv run alembic revision -m "monitor"`, then hand-write the generated file's bodies (`down_revision` is already set to the baseline id by alembic):

```python
def upgrade() -> None:
    """Add tenant keys to audits and create audit_changes."""
    op.add_column(
        "audits",
        sa.Column("client_id", sa.String(50), nullable=False, server_default="legacy"),
    )
    op.add_column("audits", sa.Column("client_ref", sa.String(100), nullable=True))
    op.create_index("ix_audits_client_id", "audits", ["client_id"])
    op.create_index("ix_audits_client_ref", "audits", ["client_ref"])

    op.create_table(
        "audit_changes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.String(50), nullable=False),
        sa.Column("client_ref", sa.String(100), nullable=False),
        sa.Column("old_audit_id", sa.Uuid(), sa.ForeignKey("audits.id"), nullable=False),
        sa.Column("new_audit_id", sa.Uuid(), sa.ForeignKey("audits.id"), nullable=False),
        sa.Column("changes", sa.JSON(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("ix_audit_changes_client_id", "audit_changes", ["client_id"])
    op.create_index("ix_audit_changes_client_ref", "audit_changes", ["client_ref"])


def downgrade() -> None:
    """Drop audit_changes and the tenant columns."""
    op.drop_table("audit_changes")
    op.drop_index("ix_audits_client_ref", "audits")
    op.drop_index("ix_audits_client_id", "audits")
    op.drop_column("audits", "client_ref")
    op.drop_column("audits", "client_id")
```

- [x] **Step 5: Verify migration cycle** — `uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head` against the dev DB. Existing audit rows get `client_id = "legacy"`, `client_ref = NULL` (excluded from monitoring by design).

- [x] **Step 6: Run -> pass; full suite; ruff; commit** (`Add tenant keys and the audit_changes table`); push; CI green. Report and WAIT.

---

### Task 3: Ingest registry refactor

**Files:**
- Create: `app/ingest/registry.py`
- Modify: `app/ingest/__main__.py`
- Modify: `tests/test_loader.py` (add one test at the end)

**Interfaces:**
- Consumes: `Act` model, `LANDING_URL_TEMPLATE`.
- Produces: `app.ingest.registry.NSW_ACT: dict` (keys `jurisdiction`, `slug`, `title`) and `async ensure_act(session) -> Act` — importable without side effects (unlike `app.ingest.__main__`, which runs `main()` on import today).

- [x] **Step 1: Failing test** — append to `tests/test_loader.py`:

```python
async def test_ensure_act_creates_then_reuses(db_session):
    from app.ingest.registry import NSW_ACT, ensure_act

    created = await ensure_act(db_session)
    assert created.slug == NSW_ACT["slug"]
    again = await ensure_act(db_session)
    assert again.id == created.id
```

- [x] **Step 2: Run -> fail** (ModuleNotFoundError: `app.ingest.registry`).

- [x] **Step 3: Implement** — `app/ingest/registry.py`:

```python
from sqlalchemy import select

from app.ingest.fetcher import LANDING_URL_TEMPLATE
from app.models import Act

NSW_ACT = {
    "jurisdiction": "NSW",
    "slug": "act-2010-042",
    "title": "Residential Tenancies Act 2010",
}


async def ensure_act(session) -> Act:
    """The registered NSW act row, created on first use."""
    act = (
        await session.execute(select(Act).where(Act.slug == NSW_ACT["slug"]))
    ).scalar_one_or_none()
    if act is None:
        act = Act(**NSW_ACT, source_url=LANDING_URL_TEMPLATE.format(slug=NSW_ACT["slug"]))
        session.add(act)
        await session.flush()
    return act
```

In `app/ingest/__main__.py`: delete the `NSW_ACT` dict, the `ensure_act` function and their now-unused imports (`select`, `Act`, `LANDING_URL_TEMPLATE` — keep `LANDING_URL_TEMPLATE` only if still referenced); add `from app.ingest.registry import NSW_ACT, ensure_act`; change the bare `main()` call at the bottom to:

```python
if __name__ == "__main__":
    main()
```

- [x] **Step 4: Run -> pass.** `uv run pytest tests/test_loader.py -q`.

- [x] **Step 5: Manual CLI check** — `uv run python -m app.ingest nsw --limit-versions 1` (opens Chrome once for the landing page; version file is cached). Expected final line: `2010-06-17: sections=227 LoadStats(inserted=0, closed=0, skipped=True)`.

- [x] **Step 6: Full suite; ruff; commit** (`Move the act registry out of the ingest entrypoint`); push; CI green. Report and WAIT.

---

### Task 4: Monitor pure functions

**Files:**
- Create: `app/monitor/__init__.py` (empty), `app/monitor/runner.py` (pure functions only in this task)
- Create: `tests/test_monitor.py`

**Interfaces:**
- Consumes: nothing app-side.
- Produces: `diff_findings(old: list[dict], new: list[dict]) -> dict[str, dict]` (only changed rules; absent side is `None`) and `new_version_dates(timeline: list[date], ingested: set[date]) -> list[date]` (ascending). Task 5 extends this module; Task 6's CLI imports both.

- [x] **Step 1: Failing tests** — `tests/test_monitor.py`:

```python
from datetime import date

from app.monitor.runner import diff_findings, new_version_dates


def _f(rule_id, verdict):
    return {"rule_id": rule_id, "verdict": verdict}


def test_diff_verdict_flip():
    delta = diff_findings(
        [_f("nsw.bond_max_4_weeks", "green")], [_f("nsw.bond_max_4_weeks", "red")]
    )
    assert delta == {"nsw.bond_max_4_weeks": {"from": "green", "to": "red"}}


def test_diff_skipped_transition_counts():
    delta = diff_findings(
        [_f("nsw.fixed_term_increase_disclosure", "red")],
        [_f("nsw.fixed_term_increase_disclosure", "skipped")],
    )
    assert delta == {"nsw.fixed_term_increase_disclosure": {"from": "red", "to": "skipped"}}


def test_diff_rule_added_and_removed():
    delta = diff_findings([_f("nsw.old_rule", "green")], [_f("nsw.new_rule", "green")])
    assert delta == {
        "nsw.old_rule": {"from": "green", "to": None},
        "nsw.new_rule": {"from": None, "to": "green"},
    }


def test_diff_no_change_is_empty():
    same = [_f("nsw.bond_max_4_weeks", "red"), _f("nsw.no_other_security", "skipped")]
    assert diff_findings(same, list(same)) == {}


def test_new_version_dates_subtracts_and_sorts():
    timeline = [date(2026, 6, 10), date(2010, 6, 17), date(2026, 9, 1)]
    ingested = {date(2010, 6, 17), date(2026, 6, 10)}
    assert new_version_dates(timeline, ingested) == [date(2026, 9, 1)]
```

- [x] **Step 2: Run -> fail** (ModuleNotFoundError).

- [x] **Step 3: Implement** — `app/monitor/runner.py`:

```python
from datetime import date


def diff_findings(old: list[dict], new: list[dict]) -> dict[str, dict]:
    """Rules whose verdict differs between two findings lists.

    A rule present on one side only reports None for the absent side.
    """
    old_verdicts = {f["rule_id"]: f["verdict"] for f in old}
    new_verdicts = {f["rule_id"]: f["verdict"] for f in new}
    return {
        rule_id: {"from": old_verdicts.get(rule_id), "to": new_verdicts.get(rule_id)}
        for rule_id in old_verdicts.keys() | new_verdicts.keys()
        if old_verdicts.get(rule_id) != new_verdicts.get(rule_id)
    }


def new_version_dates(timeline: list[date], ingested: set[date]) -> list[date]:
    """Timeline dates not yet ingested, ascending."""
    return sorted(set(timeline) - ingested)
```

- [x] **Step 4: Run -> pass; full suite; ruff; commit** (`Add the monitor diff and version arithmetic`); push; CI green. Report and WAIT.

---

### Task 5: Monitor runner

**Files:**
- Modify: `app/monitor/runner.py`
- Modify: `tests/test_monitor.py`

**Interfaces:**
- Consumes: `Audit`, `AuditChange`, `run_audit`, `ENGINE_VERSION`, `LeaseInput`, `diff_findings`.
- Produces: `MonitorResult(checked: int, changes: list[AuditChange])`; `async latest_monitored_audits(session, jurisdiction: str) -> list[Audit]`; `async run_monitor(session, jurisdiction: str, as_at: date) -> MonitorResult` (commits on success). Task 6's CLI and the corpus eval call `run_monitor`.

- [x] **Step 1: Failing tests** — append to `tests/test_monitor.py`:

```python
from app.ingest.loader import load_version
from app.ingest.parser import ParsedSection
from app.models import Act, Audit
from app.monitor.runner import run_monitor
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput

LEASE = {
    "rent_amount": "600",
    "rent_frequency": "weekly",
    "start_date": "2020-06-01",
    "bond_amount": "3000",
}


async def _seed_act(db_session):
    act = Act(jurisdiction="NSW", slug="act-2010-042", title="T", source_url="x")
    db_session.add(act)
    await db_session.flush()
    await load_version(
        db_session,
        act.id,
        date(2020, 1, 1),
        [ParsedSection("159", "Payment of bonds", "4 weeks", None, None)],
    )


async def _stored_audit(db_session, client_id="rentalapp", client_ref="lease-1"):
    """A real audit whose bond verdict is tampered to green, so a re-run flips it."""
    findings = await run_audit(db_session, "NSW", date(2021, 1, 1), LeaseInput(**LEASE))
    dumped = [f.model_dump(mode="json") for f in findings]
    bond = next(f for f in dumped if f["rule_id"] == "nsw.bond_max_4_weeks")
    bond["verdict"] = "green"
    audit = Audit(
        jurisdiction="NSW",
        as_at=date(2021, 1, 1),
        input=LEASE,
        findings=dumped,
        engine_version="1.0.0",
        client_id=client_id,
        client_ref=client_ref,
    )
    db_session.add(audit)
    await db_session.commit()
    return audit


async def test_monitor_records_verdict_change(db_session):
    await _seed_act(db_session)
    stored = await _stored_audit(db_session)
    result = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    assert result.checked == 1
    [change] = result.changes
    assert change.changes == {"nsw.bond_max_4_weeks": {"from": "green", "to": "red"}}
    assert change.old_audit_id == stored.id
    new_audit = await db_session.get(Audit, change.new_audit_id)
    assert new_audit.client_id == "rentalapp"
    assert new_audit.client_ref == "lease-1"
    assert new_audit.as_at == date(2021, 6, 1)


async def test_monitor_is_idempotent(db_session):
    await _seed_act(db_session)
    await _stored_audit(db_session)
    first = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    second = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    assert len(first.changes) == 1
    assert second.checked == 1
    assert second.changes == []


async def test_audit_without_client_ref_is_not_monitored(db_session):
    await _seed_act(db_session)
    await _stored_audit(db_session, client_ref=None)
    result = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    assert result.checked == 0


async def test_same_ref_different_tenants_grouped_separately(db_session):
    await _seed_act(db_session)
    await _stored_audit(db_session, client_id="rentalapp", client_ref="lease-1")
    await _stored_audit(db_session, client_id="acme", client_ref="lease-1")
    result = await run_monitor(db_session, "NSW", date(2021, 6, 1))
    assert result.checked == 2
    assert len(result.changes) == 2
```

- [x] **Step 2: Run -> fail** (ImportError: cannot import `run_monitor`).

- [x] **Step 3: Implement** — append to `app/monitor/runner.py` (new imports go to the top of the file):

```python
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audit, AuditChange
from app.rules import ENGINE_VERSION
from app.rules.engine import run_audit
from app.schemas.lease import LeaseInput


@dataclass
class MonitorResult:
    checked: int
    changes: list[AuditChange]


async def latest_monitored_audits(session: AsyncSession, jurisdiction: str) -> list[Audit]:
    """The newest audit per (client_id, client_ref) where client_ref is set."""
    rows = (
        (
            await session.execute(
                select(Audit)
                .where(Audit.jurisdiction == jurisdiction, Audit.client_ref.is_not(None))
                .order_by(Audit.created_at.desc(), Audit.id.desc())
            )
        )
        .scalars()
        .all()
    )
    latest: dict[tuple[str, str], Audit] = {}
    for audit in rows:
        latest.setdefault((audit.client_id, audit.client_ref), audit)
    return list(latest.values())


async def run_monitor(session: AsyncSession, jurisdiction: str, as_at: date) -> MonitorResult:
    """Re-run monitored audits at as_at and record verdict changes."""
    monitored = await latest_monitored_audits(session, jurisdiction)
    changes: list[AuditChange] = []
    for audit in monitored:
        findings = await run_audit(session, jurisdiction, as_at, LeaseInput(**audit.input))
        new_findings = [f.model_dump(mode="json") for f in findings]
        delta = diff_findings(audit.findings, new_findings)
        if not delta:
            continue
        new_audit = Audit(
            jurisdiction=jurisdiction,
            as_at=as_at,
            input=audit.input,
            findings=new_findings,
            engine_version=ENGINE_VERSION,
            client_id=audit.client_id,
            client_ref=audit.client_ref,
        )
        session.add(new_audit)
        await session.flush()
        change = AuditChange(
            client_id=audit.client_id,
            client_ref=audit.client_ref,
            old_audit_id=audit.id,
            new_audit_id=new_audit.id,
            changes=delta,
        )
        session.add(change)
        changes.append(change)
    await session.commit()
    return MonitorResult(checked=len(monitored), changes=changes)
```

- [x] **Step 4: Run -> pass.** `uv run pytest tests/test_monitor.py -q`. The idempotence test passes because the monitor-written audit becomes the newest for its `(client_id, client_ref)` and re-diffs empty.

- [x] **Step 5: Full suite; ruff; commit** (`Add the monitor runner`); push; CI green. Report and WAIT.

---

### Task 6: Monitor CLI + corpus temporal eval

**Files:**
- Create: `app/core/dates.py`, `app/monitor/__main__.py`
- Modify: `tests/test_monitor.py`

**Interfaces:**
- Consumes: fetcher (`fetch_landing`, `fetch_versions`, `parse_version_dates`), `parse_whole_act`, `load_version`, `registry.NSW_ACT`/`ensure_act`, `runner.new_version_dates`/`run_monitor`, `IngestedVersion`.
- Produces: `sydney_today() -> date` in `app.core.dates` (Task 7's router reuses it); CLI `uv run python -m app.monitor nsw [--skip-fetch]`.

- [x] **Step 1: `app/core/dates.py`:**

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo


def sydney_today() -> date:
    """Today in the service's operating timezone."""
    return datetime.now(tz=ZoneInfo("Australia/Sydney")).date()
```

- [x] **Step 2: Failing corpus eval** — append to `tests/test_monitor.py` (the fixture import mirrors `tests/test_golden.py`):

```python
from sqlalchemy import select

from app.core.dates import sydney_today
from app.models import AuditChange
from tests.test_rules_nsw import corpus_session  # noqa: F401  (reuse the skip-guard fixture)


async def test_s42_repeal_flips_disclosure_on_corpus(corpus_session):  # noqa: F811
    """A pre-repeal fixed-term audit re-run today must show the s42 flip."""
    lease = LeaseInput(
        rent_amount="600",
        rent_frequency="weekly",
        start_date="2024-01-01",
        end_date="2025-01-01",
        rent_increases=[{"effective_on": "2024-09-01", "new_amount": "620"}],
    )
    findings = await run_audit(corpus_session, "NSW", date(2024, 6, 1), lease)
    audit = Audit(
        jurisdiction="NSW",
        as_at=date(2024, 6, 1),
        input=lease.model_dump(mode="json"),
        findings=[f.model_dump(mode="json") for f in findings],
        engine_version="1.0.0",
        client_id="evaltest",
        client_ref="eval-lease-1",
    )
    corpus_session.add(audit)
    await corpus_session.commit()
    try:
        result = await run_monitor(corpus_session, "NSW", sydney_today())
        [change] = [c for c in result.changes if c.client_id == "evaltest"]
        assert change.changes == {
            "nsw.fixed_term_increase_disclosure": {"from": "red", "to": "skipped"},
            "nsw.rent_increase_first_year": {"from": "skipped", "to": "red"},
        }
    finally:
        for row in (
            (
                await corpus_session.execute(
                    select(AuditChange).where(AuditChange.client_id == "evaltest")
                )
            )
            .scalars()
            .all()
        ):
            await corpus_session.delete(row)
        for row in (
            (await corpus_session.execute(select(Audit).where(Audit.client_id == "evaltest")))
            .scalars()
            .all()
        ):
            await corpus_session.delete(row)
        await corpus_session.commit()
```

The expected delta is exact: at 2024-06-01 the disclosure rule (s42, repealed 2024-12-13) is red for this lease and the first-year rule (commenced 2024-10-31) is skipped; today both flip. Every other rule keeps its verdict.

- [x] **Step 3: Run the eval.** `uv run pytest tests/test_monitor.py -q`. This eval characterizes Task 5's runner against the real corpus, so a first-run pass is the acceptance signal (the TDD failure for this module happened in Tasks 4-5). If it fails, the corpus or rules drifted from the pinned expectations — investigate the diff; do not massage the expected delta.

- [x] **Step 4: CLI** — `app/monitor/__main__.py`:

```python
"""Monitor legislation changes and re-audit monitored leases.

Usage: uv run python -m app.monitor nsw [--skip-fetch]
"""

import argparse
import asyncio
from datetime import date
from pathlib import Path

from sqlalchemy import select

from app.core.dates import sydney_today
from app.core.db import async_session_factory
from app.ingest.fetcher import fetch_landing, fetch_versions, parse_version_dates
from app.ingest.loader import load_version
from app.ingest.parser import parse_whole_act
from app.ingest.registry import NSW_ACT, ensure_act
from app.models import IngestedVersion
from app.monitor.runner import new_version_dates, run_monitor


async def refresh_corpus() -> None:
    """Fetch and load any legislation versions published since the last run."""
    landing = fetch_landing(NSW_ACT["slug"])
    timeline = parse_version_dates(landing)
    async with async_session_factory() as session:
        act = await ensure_act(session)
        ingested = set(
            (
                await session.execute(
                    select(IngestedVersion.version_date).where(IngestedVersion.act_id == act.id)
                )
            )
            .scalars()
            .all()
        )
        missing = new_version_dates(timeline, ingested)
        if not missing:
            print("corpus: no new versions")
            await session.commit()
            return
        cache = Path("data/raw/nsw") / NSW_ACT["slug"]
        for path in fetch_versions(NSW_ACT["slug"], missing, cache):
            version_date = date.fromisoformat(path.stem)
            if version_date not in missing:
                continue
            stats = await load_version(
                session, act.id, version_date, parse_whole_act(path.read_text())
            )
            print(f"corpus: {version_date} sections loaded {stats}")
        await session.commit()


async def run(skip_fetch: bool) -> None:
    if not skip_fetch:
        await refresh_corpus()
    async with async_session_factory() as session:
        result = await run_monitor(session, NSW_ACT["jurisdiction"], sydney_today())
    print(f"monitor: checked={result.checked} changed={len(result.changes)}")
    for change in result.changes:
        print(f"  {change.client_ref}: {change.changes}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jurisdiction", choices=["nsw"])
    parser.add_argument("--skip-fetch", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.skip_fetch))


if __name__ == "__main__":
    main()
```

Note the single `asyncio.run` for both phases: the module-level engine pools connections per event loop, and two sequential `asyncio.run` calls would replay V1's cross-loop failure. `fetch_versions` returns paths for every requested date, so the loop re-checks membership in `missing` before loading.

- [x] **Step 5: Manual CLI checks** —
  1. `uv run python -m app.monitor nsw --skip-fetch` — expected: `monitor: checked=0 changed=0` (dev store audits are `legacy` with NULL `client_ref`).
  2. `uv run python -m app.monitor nsw` — opens Chrome for the landing page; expected `corpus: no new versions` (or ingest lines if NSW published since 2026-06-10 — report whichever happened) then the monitor summary.

- [x] **Step 6: Run -> pass; full suite; ruff; commit** (`Add the monitor CLI and corpus temporal eval`); push; CI green. Report (include the manual CLI output) and WAIT.

---

### Task 7: Tenant-scoped API + audit-changes endpoint

**Files:**
- Modify: `app/routers/audits.py`, `app/schemas/audit.py`, `app/main.py`, `README.md`
- Create: `app/routers/changes.py`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `require_api_key -> str` (Task 1), `AuditChange` (Task 2), `sydney_today` (Task 6).
- Produces: `POST /v1/audits` accepting `client_ref` and stamping `client_id`; tenant-scoped `GET /v1/audits/{id}`; `GET /v1/audit-changes?since=&client_ref=&limit=` returning `list[AuditChangeInfo]` ascending by `created_at`.

- [x] **Step 1: Failing tests** — append to `tests/test_api.py`:

```python
OTHER = {"X-API-Key": "other-key"}


async def test_create_echoes_client_ref(client, seeded):
    body = dict(AUDIT_BODY, client_ref="lease-77")
    created = await client.post("/v1/audits", json=body, headers=KEY)
    assert created.status_code == 201
    assert created.json()["client_ref"] == "lease-77"


async def test_cross_tenant_audit_is_404(client, seeded):
    created = await client.post("/v1/audits", json=AUDIT_BODY, headers=KEY)
    audit_id = created.json()["id"]
    assert (await client.get(f"/v1/audits/{audit_id}", headers=KEY)).status_code == 200
    assert (await client.get(f"/v1/audits/{audit_id}", headers=OTHER)).status_code == 404


@pytest.fixture
async def seeded_changes(db_session):
    from app.models import AuditChange

    audits = {}
    for client_id in ("testco", "otherco"):
        audit = Audit(
            jurisdiction="NSW",
            as_at=date(2026, 1, 1),
            input={},
            findings=[],
            engine_version="1.0.0",
            client_id=client_id,
            client_ref="lease-1",
        )
        db_session.add(audit)
        await db_session.flush()
        audits[client_id] = audit
    for client_id, audit in audits.items():
        db_session.add(
            AuditChange(
                client_id=client_id,
                client_ref="lease-1",
                old_audit_id=audit.id,
                new_audit_id=audit.id,
                changes={"nsw.bond_max_4_weeks": {"from": "green", "to": "red"}},
            )
        )
    await db_session.commit()


async def test_changes_are_tenant_scoped(client, seeded_changes):
    listed = await client.get("/v1/audit-changes", headers=KEY)
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["client_ref"] == "lease-1"
    assert body[0]["changes"]["nsw.bond_max_4_weeks"]["to"] == "red"


async def test_changes_since_filter(client, seeded_changes):
    listed = (await client.get("/v1/audit-changes", headers=KEY)).json()
    cursor = listed[0]["created_at"]
    after = await client.get("/v1/audit-changes", params={"since": cursor}, headers=KEY)
    assert after.json() == []


async def test_changes_require_key(client):
    assert (await client.get("/v1/audit-changes")).status_code == 401
```

Also update the `Audit` import at the top of `tests/test_api.py`:

```python
from app.models import Act, Audit
```

- [x] **Step 2: Run -> fail** (`client_ref` missing from the response, cross-tenant GET returns 200, `/v1/audit-changes` 404).

- [x] **Step 3: Schemas** — in `app/schemas/audit.py` add `client_ref` to both models and the new info model:

```python
class AuditCreate(BaseModel):
    jurisdiction: Literal["NSW"]
    as_at: date | None = None
    client_ref: str | None = None
    lease: LeaseInput


class AuditInfo(BaseModel):
    id: uuid.UUID
    jurisdiction: str
    as_at: date
    engine_version: str
    client_ref: str | None = None
    findings: list[Finding]
    created_at: datetime


class AuditChangeInfo(BaseModel):
    id: uuid.UUID
    client_ref: str
    old_audit_id: uuid.UUID
    new_audit_id: uuid.UUID
    changes: dict
    created_at: datetime
```

- [x] **Step 4: Audits router** — in `app/routers/audits.py`: replace the router line and both endpoints (`require_api_key` now injects per-endpoint so the value is available; `date.today` fallback becomes `sydney_today()`):

```python
router = APIRouter(prefix="/v1")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClientDep = Annotated[str, Depends(require_api_key)]


@router.post("/audits", status_code=201, response_model=AuditInfo)
async def create_audit(body: AuditCreate, client_id: ClientDep, session: SessionDep) -> AuditInfo:
    as_at = body.as_at or sydney_today()
    findings = await run_audit(session, body.jurisdiction, as_at, body.lease)
    audit = Audit(
        jurisdiction=body.jurisdiction,
        as_at=as_at,
        input=body.lease.model_dump(mode="json"),
        findings=[f.model_dump(mode="json") for f in findings],
        engine_version=ENGINE_VERSION,
        client_id=client_id,
        client_ref=body.client_ref,
    )
    session.add(audit)
    await session.commit()
    await session.refresh(audit)
    return AuditInfo(
        id=audit.id,
        jurisdiction=audit.jurisdiction,
        as_at=audit.as_at,
        engine_version=audit.engine_version,
        client_ref=audit.client_ref,
        findings=findings,
        created_at=audit.created_at,
    )


@router.get("/audits/{audit_id}", response_model=AuditInfo)
async def get_audit(audit_id: uuid.UUID, client_id: ClientDep, session: SessionDep) -> AuditInfo:
    audit = await session.get(Audit, audit_id)
    if audit is None or audit.client_id != client_id:
        raise HTTPException(status_code=404, detail="Audit not found")
    return AuditInfo(
        id=audit.id,
        jurisdiction=audit.jurisdiction,
        as_at=audit.as_at,
        engine_version=audit.engine_version,
        client_ref=audit.client_ref,
        findings=audit.findings,
        created_at=audit.created_at,
    )
```

Imports to adjust at the top: drop `from datetime import datetime` / `ZoneInfo` lines (now unused), add `from app.core.dates import sydney_today`, and import `AuditChangeInfo` is NOT needed here.

- [x] **Step 5: Changes router** — `app/routers/changes.py`:

```python
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_api_key
from app.core.db import get_session
from app.models import AuditChange
from app.schemas.audit import AuditChangeInfo

router = APIRouter(prefix="/v1")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
ClientDep = Annotated[str, Depends(require_api_key)]


@router.get("/audit-changes", response_model=list[AuditChangeInfo])
async def list_audit_changes(
    client_id: ClientDep,
    session: SessionDep,
    since: datetime | None = None,
    client_ref: str | None = None,
    limit: int = 100,
) -> list[AuditChangeInfo]:
    query = (
        select(AuditChange)
        .where(AuditChange.client_id == client_id)
        .order_by(AuditChange.created_at.asc())
        .limit(limit)
    )
    if since is not None:
        query = query.where(AuditChange.created_at > since)
    if client_ref is not None:
        query = query.where(AuditChange.client_ref == client_ref)
    rows = (await session.execute(query)).scalars().all()
    return [
        AuditChangeInfo(
            id=row.id,
            client_ref=row.client_ref,
            old_audit_id=row.old_audit_id,
            new_audit_id=row.new_audit_id,
            changes=row.changes,
            created_at=row.created_at,
        )
        for row in rows
    ]
```

Mount it in `app/main.py`:

```python
from app.routers.changes import router as changes_router

app.include_router(changes_router)
```

The legislation router keeps its router-level `dependencies=[Depends(require_api_key)]` — it needs no tenant value.

- [x] **Step 6: Run -> pass.** `uv run pytest tests/test_api.py -q`.

- [x] **Step 7: README** — update the usage section: `API_KEYS` becomes `key:client_id` pairs (`API_KEYS=dev-key:rentalapp uv run uvicorn app.main:app`), add `client_ref` to the audit example body, and append the monitoring section:

```markdown
## Change monitoring

Re-run monitored leases (audits created with a `client_ref`) against the
law as at today, after refreshing the corpus:

```bash
uv run python -m app.monitor nsw
```

Poll detected changes (tenant-scoped, ascending; pass the last seen
`created_at` as `since`):

```bash
curl -s "http://localhost:8000/v1/audit-changes?since=2026-07-26T00:00:00Z" \
  -H "X-API-Key: dev-key"
```
```

- [x] **Step 8: Full suite; ruff; commit** (`Add tenant scoping and the audit-changes API`); push; CI green. Report and WAIT — change monitor complete.
