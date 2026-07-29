# Tenant Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move API keys from the `API_KEYS` env var into Postgres with per-tenant rate limits, a daily clause-audit quota, usage counters, and an admin CLI — so third parties can safely hold keys.

**Architecture:** Three new tables (`tenants`, `api_keys`, `usage_counters`) in the existing Postgres. Auth hashes the presented key, checks a 60 s process-local cache, and falls back to one DB query. A per-tenant in-process token bucket enforces `rpm_limit` on every `/v1` route; the clause-audit POST additionally counts today's jobs against `clause_audits_per_day`. Billable events upsert a daily counter. `python -m app.tenants` is the admin surface; lifespan idempotently imports `API_KEYS` on startup so one deploy migrates production with zero downtime.

**Tech Stack:** FastAPI, async SQLAlchemy 2.0, Alembic, PostgreSQL (`ON CONFLICT` upsert), argparse CLI.

**Spec:** `docs/superpowers/specs/2026-07-29-tenant-foundation-design.md`

## Global Constraints

- Python 3.12+, `uv` only: `uv run ...`, `uv add ...` — never `python3`/`pip`.
- TDD: failing test first, watch it fail for the right reason, then implement.
- Every task ends: full suite (`uv run pytest`) -> ruff sequence (`uv run ruff format .` -> `uv run ruff check --fix .` -> `uv run ruff check .` -> `uv run ruff format --check .`) -> commit -> push origin main -> CI green.
- No emojis in code, logs, or prints. Docstrings over inline comments.
- Key format: `lk_` + `secrets.token_urlsafe(24)`; prefix = first 8 chars; storage = SHA-256 hex. Plaintext printed exactly once at creation.
- Defaults for new tenants: `rpm_limit=60`, `clause_audits_per_day=10`. Auth cache TTL 60 seconds.
- Error semantics: unknown/revoked key 401 `Invalid API key`; suspended tenant 403 `Tenant suspended`; rpm exceeded 429 with `Retry-After` header; daily quota 429 detail names the quota and midnight-UTC reset.
- Usage classes counted: `audit` (POST /v1/audits), `clause_audit` (POST /v1/clause-audits), `legislation` (GET /v1/legislation/*). Nothing else is counted.
- Existing router handler responses and paths must not change; the SaaS client keeps working unmodified.

---

### Task 1: Tenant tables, key helpers, migration

**Files:**
- Create: `app/models/tenant.py`
- Modify: `app/models/__init__.py`
- Create: `app/core/keys.py`
- Create: `alembic/versions/b3e8f2a91c47_tenants.py`
- Test: `tests/test_keys.py`

**Interfaces:**
- Consumes: `app.core.db.Base`.
- Produces: models `Tenant`, `ApiKey`, `UsageCounter` (importable from `app.models`); functions `generate_key() -> str`, `hash_key(key: str) -> str`, `key_prefix(key: str) -> str` in `app.core.keys`. Later tasks rely on these exact names.

- [ ] **Step 1: Write the failing test**

`tests/test_keys.py`:

```python
from app.core.keys import generate_key, hash_key, key_prefix


def test_generated_key_has_prefix_and_length():
    key = generate_key()
    assert key.startswith("lk_")
    assert len(key) == 35


def test_generated_keys_are_unique():
    assert generate_key() != generate_key()


def test_hash_is_sha256_hex_of_key():
    assert hash_key("lk_abc") == (
        "45f7da3757385412af05fa3c8f1fcbe3209d09d922f858f58b1656e88bea7fff"
    )


def test_prefix_is_first_eight_chars():
    key = generate_key()
    assert key_prefix(key) == key[:8]
```

(The hash literal is the real SHA-256 of `lk_abc`, verifiable with
`uv run python -c "import hashlib; print(hashlib.sha256(b'lk_abc').hexdigest())"`.)

- [ ] **Step 2: Run it to make sure it fails**

Run: `uv run pytest tests/test_keys.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.core.keys'`

- [ ] **Step 3: Implement the helpers**

`app/core/keys.py`:

```python
"""API key generation and hashing. Plaintext keys are never stored."""

import hashlib
import secrets


def generate_key() -> str:
    return "lk_" + secrets.token_urlsafe(24)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def key_prefix(key: str) -> str:
    return key[:8]
```

- [ ] **Step 4: Run the test again**

Run: `uv run pytest tests/test_keys.py -v`
Expected: 4 passed

- [ ] **Step 5: Write the models**

`app/models/tenant.py`:

```python
"""Tenants, their API keys, and daily usage counters."""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(Text, default="active")
    rpm_limit: Mapped[int] = mapped_column(Integer, default=60)
    clause_audits_per_day: Mapped[int] = mapped_column(Integer, default=10)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"))
    key_hash: Mapped[str] = mapped_column(Text, unique=True)
    prefix: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class UsageCounter(Base):
    __tablename__ = "usage_counters"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), primary_key=True
    )
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    endpoint_class: Mapped[str] = mapped_column(Text, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
```

`app/models/__init__.py` becomes:

```python
from app.models.audit import Audit, AuditChange
from app.models.clause_audit import ClauseAuditJob
from app.models.legislation import Act, IngestedVersion, Section
from app.models.tenant import ApiKey, Tenant, UsageCounter

__all__ = [
    "Act",
    "ApiKey",
    "Audit",
    "AuditChange",
    "ClauseAuditJob",
    "IngestedVersion",
    "Section",
    "Tenant",
    "UsageCounter",
]
```

- [ ] **Step 6: Write the migration**

First confirm the current head is `a1c47e92b5d3`: `uv run alembic heads`

`alembic/versions/b3e8f2a91c47_tenants.py`:

```python
"""tenants

Revision ID: b3e8f2a91c47
Revises: a1c47e92b5d3
Create Date: 2026-07-29

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3e8f2a91c47"
down_revision: str | Sequence[str] | None = "a1c47e92b5d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create tenants, api_keys and usage_counters."""
    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("client_id", sa.Text(), nullable=False, unique=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("rpm_limit", sa.Integer(), nullable=False),
        sa.Column("clause_audits_per_day", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False
        ),
        sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
        sa.Column("prefix", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "usage_counters",
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), primary_key=True
        ),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("endpoint_class", sa.Text(), primary_key=True),
        sa.Column("count", sa.Integer(), nullable=False),
    )


def downgrade() -> None:
    """Drop the tenant tables."""
    op.drop_table("usage_counters")
    op.drop_table("api_keys")
    op.drop_table("tenants")
```

- [ ] **Step 7: Verify the migration applies and rolls back locally**

```bash
uv run alembic upgrade head
uv run alembic downgrade -1
uv run alembic upgrade head
```

Expected: `Running upgrade a1c47e92b5d3 -> b3e8f2a91c47`, then the downgrade, then the upgrade again, no errors.

- [ ] **Step 8: Full suite, ruff, commit**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/models/tenant.py app/models/__init__.py app/core/keys.py alembic/versions/b3e8f2a91c47_tenants.py tests/test_keys.py
git commit -m "Add tenant, api key and usage models with key helpers"
git push origin main
```

Expected: 165 passed (161 existing + 4 new), 5 deselected; CI green.

---

### Task 2: Database-backed authentication

**Files:**
- Rewrite: `app/core/auth.py`
- Modify: `tests/conftest.py`
- Rewrite: `tests/test_auth.py`
- Modify: `tests/test_api.py:15` (the `keys` autouse fixture)
- Modify: `tests/test_clause_api.py:17` (the `keys` autouse fixture)

**Interfaces:**
- Consumes: `app.models` `Tenant`/`ApiKey`; `app.core.keys.hash_key`.
- Produces: `TenantContext` dataclass with fields `tenant_id: uuid.UUID`, `client_id: str`, `status: str`, `rpm_limit: int`, `clause_audits_per_day: int`; dependency `require_tenant`; alias `TenantDep = Annotated[TenantContext, Depends(require_tenant)]`; `require_api_key` (unchanged name, still returns `client_id: str`, now built on `require_tenant` — existing routers keep working without edits); `clear_auth_cache()`. Conftest fixtures `seeded_tenants` and autouse `reset_tenant_state`.

- [ ] **Step 1: Add conftest fixtures**

Append to `tests/conftest.py` (imports go at the top of the file):

```python
from app.core.auth import clear_auth_cache
from app.core.keys import hash_key, key_prefix
from app.models import ApiKey, Tenant


@pytest.fixture(autouse=True)
def reset_tenant_state():
    clear_auth_cache()
    yield
    clear_auth_cache()


@pytest.fixture
async def seeded_tenants(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        testco = Tenant(client_id="testco", name="Test Co")
        otherco = Tenant(client_id="otherco", name="Other Co")
        session.add_all([testco, otherco])
        await session.flush()
        session.add_all(
            [
                ApiKey(
                    tenant_id=testco.id,
                    key_hash=hash_key("test-key"),
                    prefix=key_prefix("test-key"),
                ),
                ApiKey(
                    tenant_id=otherco.id,
                    key_hash=hash_key("other-key"),
                    prefix=key_prefix("other-key"),
                ),
            ]
        )
        await session.commit()
        return {"testco": testco, "otherco": otherco}
```

(`clear_auth_cache` does not exist yet — the import will fail, which is the first failure you watch for.)

- [ ] **Step 2: Rewrite `tests/test_auth.py`**

The old file tested env parsing; replace its entire content:

```python
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.auth import clear_auth_cache
from app.main import app
from app.models import Tenant


@pytest.fixture
async def api(client, seeded_tenants):
    return client


async def test_valid_key_reaches_endpoint(api):
    response = await api.get(
        "/v1/audit-changes", headers={"X-API-Key": "test-key"}
    )
    assert response.status_code == 200


async def test_unknown_key_is_401(api):
    response = await api.get("/v1/audit-changes", headers={"X-API-Key": "nope"})
    assert response.status_code == 401


async def test_missing_key_is_401(api):
    response = await api.get("/v1/audit-changes")
    assert response.status_code == 401


async def test_suspended_tenant_is_403(api, seeded_tenants, db_session):
    tenant = await db_session.get(Tenant, seeded_tenants["testco"].id)
    tenant.status = "suspended"
    await db_session.commit()
    clear_auth_cache()
    response = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert response.status_code == 403


async def test_revoked_key_is_401_after_cache_clear(api, seeded_tenants, db_session):
    ok = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert ok.status_code == 200
    from app.models import ApiKey
    from sqlalchemy import update

    await db_session.execute(update(ApiKey).values(status="revoked"))
    await db_session.commit()
    clear_auth_cache()
    response = await api.get("/v1/audit-changes", headers={"X-API-Key": "test-key"})
    assert response.status_code == 401
```

Note: `/v1/audit-changes` is used as the probe endpoint because it is a cheap
authenticated GET. Check its actual path with
`grep -n "@router.get" app/routers/changes.py` and adjust the URL if it
differs (e.g. `/v1/audit-changes?since=2026-01-01` if a query param is
required); the assertion for a valid key is simply "not 401/403" — if the
endpoint needs params and returns 422 without them, assert
`response.status_code not in (401, 403)` instead of `== 200`, for every
positive-auth test above.

- [ ] **Step 3: Run to watch it fail**

Run: `uv run pytest tests/test_auth.py -v`
Expected: collection error — `ImportError: cannot import name 'clear_auth_cache' from 'app.core.auth'`

- [ ] **Step 4: Rewrite `app/core/auth.py`**

```python
"""Database-backed API key authentication with a short process-local cache."""

import time
import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.keys import hash_key
from app.models import ApiKey, Tenant

CACHE_TTL_SECONDS = 60


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID
    client_id: str
    status: str
    rpm_limit: int
    clause_audits_per_day: int


_cache: dict[str, tuple[TenantContext, float]] = {}


def clear_auth_cache() -> None:
    _cache.clear()


async def require_tenant(
    session: Annotated[AsyncSession, Depends(get_session)],
    x_api_key: str = Header(default=""),
) -> TenantContext:
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    key_hash = hash_key(x_api_key)
    now = time.monotonic()
    cached = _cache.get(key_hash)
    if cached is not None and cached[1] > now:
        ctx = cached[0]
    else:
        row = (
            await session.execute(
                select(ApiKey, Tenant)
                .join(Tenant, ApiKey.tenant_id == Tenant.id)
                .where(ApiKey.key_hash == key_hash, ApiKey.status == "active")
            )
        ).one_or_none()
        if row is None:
            raise HTTPException(status_code=401, detail="Invalid API key")
        api_key, tenant = row
        await session.execute(
            update(ApiKey)
            .where(ApiKey.id == api_key.id)
            .values(last_used_at=time_now())
        )
        await session.commit()
        ctx = TenantContext(
            tenant_id=tenant.id,
            client_id=tenant.client_id,
            status=tenant.status,
            rpm_limit=tenant.rpm_limit,
            clause_audits_per_day=tenant.clause_audits_per_day,
        )
        _cache[key_hash] = (ctx, now + CACHE_TTL_SECONDS)
    if ctx.status != "active":
        raise HTTPException(status_code=403, detail="Tenant suspended")
    return ctx


TenantDep = Annotated[TenantContext, Depends(require_tenant)]


async def require_api_key(tenant: TenantDep) -> str:
    return tenant.client_id
```

Where `time_now()` is:

```python
from datetime import UTC, datetime


def time_now() -> datetime:
    return datetime.now(UTC)
```

(placed above `require_tenant` in the same file; it exists so tests can
monkeypatch a fixed clock if needed).

- [ ] **Step 5: Update the two router-test fixtures**

In `tests/test_api.py` and `tests/test_clause_api.py`, replace the autouse
`keys` fixture (which monkeypatches `settings.api_keys`) with:

```python
@pytest.fixture(autouse=True)
async def keys(seeded_tenants):
    """All API tests in this file run against the seeded testco/otherco tenants."""
```

Delete the now-unused `from app.core.config import settings` import if
nothing else in the file uses it (check with grep before deleting).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all pass. Failures to expect and fix if seen: tests that hit
`/v1` endpoints without the `seeded_tenants` fixture now get 401 — add the
fixture dependency to that file's autouse fixture the same way.

- [ ] **Step 7: Ruff, commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/core/auth.py tests/conftest.py tests/test_auth.py tests/test_api.py tests/test_clause_api.py
git commit -m "Authenticate API keys against the database"
git push origin main
```

Expected: CI green.

---

### Task 3: Per-minute rate limiting

**Files:**
- Create: `app/core/ratelimit.py`
- Modify: `app/routers/audits.py:16` (router constructor)
- Modify: `app/routers/changes.py` (router constructor)
- Modify: `app/routers/clause_audits.py:17` (router constructor)
- Modify: `app/routers/legislation.py:12` (router constructor)
- Modify: `tests/conftest.py` (extend `reset_tenant_state`)
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Consumes: `TenantDep` from Task 2.
- Produces: `TokenBucket(capacity: int, refill_per_second: float, clock=time.monotonic)` with method `try_take() -> float` (0.0 on success, else seconds to wait); dependency `enforce_rate_limit`; `clear_buckets()`.

- [ ] **Step 1: Write the failing unit tests**

`tests/test_ratelimit.py`:

```python
from app.core.ratelimit import TokenBucket


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_bucket_allows_capacity_then_blocks():
    clock = FakeClock()
    bucket = TokenBucket(capacity=3, refill_per_second=1.0, clock=clock)
    assert bucket.try_take() == 0.0
    assert bucket.try_take() == 0.0
    assert bucket.try_take() == 0.0
    assert bucket.try_take() > 0.0


def test_bucket_refills_over_time():
    clock = FakeClock()
    bucket = TokenBucket(capacity=2, refill_per_second=1.0, clock=clock)
    bucket.try_take()
    bucket.try_take()
    assert bucket.try_take() > 0.0
    clock.t = 1.0
    assert bucket.try_take() == 0.0


def test_wait_hint_is_time_until_next_token():
    clock = FakeClock()
    bucket = TokenBucket(capacity=1, refill_per_second=0.5, clock=clock)
    bucket.try_take()
    wait = bucket.try_take()
    assert wait == 2.0


async def test_over_limit_request_gets_429_with_retry_after(
    client, seeded_tenants, db_session
):
    from app.models import Tenant

    tenant = await db_session.get(Tenant, seeded_tenants["testco"].id)
    tenant.rpm_limit = 2
    await db_session.commit()

    headers = {"X-API-Key": "test-key"}
    first = await client.get("/v1/audit-changes", headers=headers)
    assert first.status_code not in (401, 403, 429)
    await client.get("/v1/audit-changes", headers=headers)
    third = await client.get("/v1/audit-changes", headers=headers)
    assert third.status_code == 429
    assert "Retry-After" in third.headers
```

(As in Task 2, if `/v1/audit-changes` requires query params, add them; the
assertions only care about auth/limit status codes.)

- [ ] **Step 2: Run to watch it fail**

Run: `uv run pytest tests/test_ratelimit.py -v`
Expected: `ModuleNotFoundError: No module named 'app.core.ratelimit'`

- [ ] **Step 3: Implement**

`app/core/ratelimit.py`:

```python
"""Per-tenant in-process token buckets. Single instance; resets on restart."""

import math
import time
import uuid

from fastapi import HTTPException

from app.core.auth import TenantDep


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float, clock=time.monotonic):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.clock = clock
        self.tokens = float(capacity)
        self.updated = clock()

    def try_take(self) -> float:
        """Take one token. Returns 0.0 on success, else seconds until one refills."""
        now = self.clock()
        self.tokens = min(
            self.capacity, self.tokens + (now - self.updated) * self.refill_per_second
        )
        self.updated = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) / self.refill_per_second


_buckets: dict[uuid.UUID, TokenBucket] = {}


def clear_buckets() -> None:
    _buckets.clear()


async def enforce_rate_limit(tenant: TenantDep) -> None:
    bucket = _buckets.get(tenant.tenant_id)
    if bucket is None or bucket.capacity != tenant.rpm_limit:
        bucket = TokenBucket(tenant.rpm_limit, tenant.rpm_limit / 60)
        _buckets[tenant.tenant_id] = bucket
    wait = bucket.try_take()
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(math.ceil(wait))},
        )
```

- [ ] **Step 4: Attach to every router**

In `app/routers/audits.py`, `app/routers/changes.py`,
`app/routers/clause_audits.py` — add the dependency to the router
constructor:

```python
from app.core.ratelimit import enforce_rate_limit

router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])
```

In `app/routers/legislation.py`, replace the existing
`dependencies=[Depends(require_api_key)]` with
`dependencies=[Depends(enforce_rate_limit)]` (the limiter depends on
`require_tenant`, so auth still runs; drop the now-unused
`require_api_key` import).

FastAPI caches dependency results per request, so `require_tenant` runs
once even though both the limiter and the handler's `ClientDep` need it.

- [ ] **Step 5: Extend the reset fixture**

In `tests/conftest.py`, `reset_tenant_state` becomes:

```python
from app.core.ratelimit import clear_buckets


@pytest.fixture(autouse=True)
def reset_tenant_state():
    clear_auth_cache()
    clear_buckets()
    yield
    clear_auth_cache()
    clear_buckets()
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all pass (existing API tests stay under the default 60 rpm and
buckets reset between tests).

- [ ] **Step 7: Ruff, commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/core/ratelimit.py app/routers/ tests/test_ratelimit.py tests/conftest.py
git commit -m "Enforce per-tenant request rate limits"
git push origin main
```

Expected: CI green.

---

### Task 4: Daily clause quota and usage counters

**Files:**
- Create: `app/core/usage.py`
- Modify: `app/routers/audits.py` (POST handler)
- Modify: `app/routers/clause_audits.py` (POST handler)
- Modify: `app/routers/legislation.py` (GET handler)
- Test: `tests/test_usage.py`

**Interfaces:**
- Consumes: `TenantDep`, `UsageCounter`, `ClauseAuditJob`.
- Produces: `record_usage(session: AsyncSession, tenant_id: uuid.UUID, endpoint_class: str) -> None` (executes an upsert; caller commits).

- [ ] **Step 1: Write the failing tests**

`tests/test_usage.py`:

```python
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import ClauseAuditJob, UsageCounter


@pytest.fixture(autouse=True)
async def keys(seeded_tenants):
    """Run against the seeded tenants."""


async def _counters(db_session, tenant_id):
    rows = (
        await db_session.execute(
            select(UsageCounter).where(UsageCounter.tenant_id == tenant_id)
        )
    ).scalars()
    return {(c.day, c.endpoint_class): c.count for c in rows}


async def test_deterministic_audit_counts_once(client, seeded_tenants, db_session):
    body = {
        "jurisdiction": "NSW",
        "lease": {"rent_amount": "600", "rent_frequency": "weekly"},
    }
    for _ in range(2):
        response = await client.post(
            "/v1/audits", json=body, headers={"X-API-Key": "test-key"}
        )
        assert response.status_code == 201
    counters = await _counters(db_session, seeded_tenants["testco"].id)
    today = datetime.now(UTC).date()
    assert counters[(today, "audit")] == 2


async def test_legislation_read_counts(client, seeded_tenants, db_session):
    response = await client.get(
        "/v1/legislation/sections",
        params={"act": "act-2010-042", "section_no": "19", "as_at": "2026-07-29"},
        headers={"X-API-Key": "test-key"},
    )
    assert response.status_code == 404
    counters = await _counters(db_session, seeded_tenants["testco"].id)
    today = datetime.now(UTC).date()
    assert (today, "legislation") not in counters


async def test_clause_post_counts_and_daily_quota_blocks(
    client, seeded_tenants, db_session, monkeypatch
):
    from app.core.config import settings
    from app.models import Tenant

    monkeypatch.setattr(settings, "anthropic_api_key", "test")
    tenant = await db_session.get(Tenant, seeded_tenants["testco"].id)
    tenant.clause_audits_per_day = 1
    await db_session.commit()

    files = {"file": ("lease.pdf", b"%PDF-1.4 fake", "application/pdf")}
    data = {"payload": '{"jurisdiction": "NSW"}'}
    headers = {"X-API-Key": "test-key"}

    first = await client.post(
        "/v1/clause-audits", data=data, files=files, headers=headers
    )
    assert first.status_code == 202
    second = await client.post(
        "/v1/clause-audits", data=data, files=files, headers=headers
    )
    assert second.status_code == 429
    assert "quota" in second.json()["detail"].lower()
    assert "utc" in second.json()["detail"].lower()

    counters = await _counters(db_session, seeded_tenants["testco"].id)
    today = datetime.now(UTC).date()
    assert counters[(today, "clause_audit")] == 1
```

Notes for the implementer:
- The legislation test asserts a 404 does NOT count (success-path only);
  counting happens only when the handler returns a section. If the local
  test DB has no corpus, 404 is what you get — that is the point of the
  test. A positive counting test for legislation would need seeded
  sections; skip it, the audit test already proves the helper increments.
- The clause test uses an in-memory fake PDF; the job fails later in the
  worker, but the POST (and therefore the counter and quota) is what is
  under test, and no worker runs in tests.

- [ ] **Step 2: Run to watch it fail**

Run: `uv run pytest tests/test_usage.py -v`
Expected: FAIL — counters table empty (`KeyError`) and the second clause
POST returns 202 instead of 429.

- [ ] **Step 3: Implement the helper**

`app/core/usage.py`:

```python
"""Daily billable-event counters. Caller commits the session."""

import uuid
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import UsageCounter


async def record_usage(
    session: AsyncSession, tenant_id: uuid.UUID, endpoint_class: str
) -> None:
    stmt = insert(UsageCounter).values(
        tenant_id=tenant_id,
        day=datetime.now(UTC).date(),
        endpoint_class=endpoint_class,
        count=1,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "day", "endpoint_class"],
        set_={"count": UsageCounter.count + 1},
    )
    await session.execute(stmt)
```

- [ ] **Step 4: Wire the three handlers**

`app/routers/audits.py` — change the POST handler signature and add the
call (GET handlers keep `ClientDep`):

```python
from app.core.auth import TenantDep, require_api_key
from app.core.usage import record_usage


@router.post("/audits", status_code=201, response_model=AuditInfo)
async def create_audit(body: AuditCreate, tenant: TenantDep, session: SessionDep) -> AuditInfo:
    as_at = body.as_at or sydney_today()
    findings = await run_audit(session, body.jurisdiction, as_at, body.lease)
    audit = Audit(
        jurisdiction=body.jurisdiction,
        as_at=as_at,
        input=body.lease.model_dump(mode="json"),
        findings=[f.model_dump(mode="json") for f in findings],
        engine_version=ENGINE_VERSION,
        client_id=tenant.client_id,
        client_ref=body.client_ref,
    )
    session.add(audit)
    await record_usage(session, tenant.tenant_id, "audit")
    await session.commit()
    ...
```

(the rest of the handler is unchanged; `client_id` references become
`tenant.client_id`).

`app/routers/clause_audits.py` — POST handler: replace
`client_id: ClientDep` with `tenant: TenantDep`; every existing
`client_id` reference in the handler body becomes `tenant.client_id`.
After the in-flight check, add the quota check:

```python
from datetime import UTC, datetime

from app.core.auth import TenantDep
from app.core.usage import record_usage

    midnight_utc = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = (
        await session.execute(
            select(func.count())
            .select_from(ClauseAuditJob)
            .where(
                ClauseAuditJob.client_id == tenant.client_id,
                ClauseAuditJob.created_at >= midnight_utc,
            )
        )
    ).scalar_one()
    if today_count >= tenant.clause_audits_per_day:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Daily clause audit quota of {tenant.clause_audits_per_day} "
                "reached; resets at midnight UTC"
            ),
        )
```

and immediately before the existing `await session.commit()` that saves
the job:

```python
    await record_usage(session, tenant.tenant_id, "clause_audit")
```

`app/routers/legislation.py` — the handler gains the tenant and the
counter (router-level auth stays as-is):

```python
from app.core.auth import TenantDep
from app.core.usage import record_usage


@router.get("/legislation/sections", response_model=SectionInfo)
async def get_section(
    act: str, section_no: str, as_at: date, tenant: TenantDep, session: SessionDep
) -> SectionInfo:
    section = await section_at(session, act, section_no, as_at)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not in force at that date")
    await record_usage(session, tenant.tenant_id, "legislation")
    await session.commit()
    return SectionInfo(...)
```

(the `SectionInfo(...)` construction is unchanged from the current file).

- [ ] **Step 5: Run the new tests, then the full suite**

```bash
uv run pytest tests/test_usage.py -v
uv run pytest
```

Expected: all pass. `tests/test_clause_api.py` has tests that create
multiple jobs for one tenant — if any now trips the default quota of 10,
raise that test tenant's `clause_audits_per_day` inside the test via the
session rather than weakening the default.

- [ ] **Step 6: Ruff, commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/core/usage.py app/routers/ tests/test_usage.py
git commit -m "Add daily clause quota and usage counters"
git push origin main
```

Expected: CI green.

---

### Task 5: Admin CLI and startup import

**Files:**
- Create: `app/tenants/__init__.py`
- Create: `app/tenants/__main__.py`
- Modify: `app/main.py` (lifespan)
- Modify: `deploy/README.md`
- Test: `tests/test_tenants_cli.py`

**Interfaces:**
- Consumes: models, `app.core.keys`, `settings.api_keys`, `async_session_factory`.
- Produces: `app/tenants/__init__.py` exposes `import_env_keys(session) -> int` and the command functions; `python -m app.tenants <command>` works. Lifespan calls `import_env_keys` when `settings.api_keys` is non-empty.

- [ ] **Step 1: Write the failing tests**

`tests/test_tenants_cli.py`:

```python
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models import ApiKey, Tenant
from app.tenants import (
    create_tenant,
    import_env_keys,
    new_key,
    revoke_key,
    set_limits,
    set_status,
)


async def test_create_tenant_returns_plaintext_key_once(db_session):
    key = await create_tenant(db_session, "acme", name="Acme Pty")
    assert key.startswith("lk_")
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.client_id == "acme"))
    ).scalar_one()
    assert tenant.rpm_limit == 60
    assert tenant.clause_audits_per_day == 10
    stored = (await db_session.execute(select(ApiKey))).scalar_one()
    assert stored.key_hash != key
    assert stored.prefix == key[:8]


async def test_create_duplicate_client_id_raises(db_session):
    await create_tenant(db_session, "acme")
    with pytest.raises(ValueError, match="already exists"):
        await create_tenant(db_session, "acme")


async def test_new_key_and_revoke(db_session):
    await create_tenant(db_session, "acme")
    second = await new_key(db_session, "acme")
    keys = (await db_session.execute(select(ApiKey))).scalars().all()
    assert len(keys) == 2
    await revoke_key(db_session, second[:8])
    revoked = (
        await db_session.execute(select(ApiKey).where(ApiKey.prefix == second[:8]))
    ).scalar_one()
    assert revoked.status == "revoked"


async def test_set_limits_and_status(db_session):
    await create_tenant(db_session, "acme")
    await set_limits(db_session, "acme", rpm=300, clause_per_day=200)
    await set_status(db_session, "acme", "suspended")
    tenant = (
        await db_session.execute(select(Tenant).where(Tenant.client_id == "acme"))
    ).scalar_one()
    assert tenant.rpm_limit == 300
    assert tenant.clause_audits_per_day == 200
    assert tenant.status == "suspended"


async def test_import_env_keys_is_idempotent(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "legacy-key:rentalapp")
    first = await import_env_keys(db_session)
    second = await import_env_keys(db_session)
    assert first == 1
    assert second == 0
    tenant = (
        await db_session.execute(
            select(Tenant).where(Tenant.client_id == "rentalapp")
        )
    ).scalar_one()
    assert tenant.status == "active"


async def test_import_env_keys_noop_when_empty(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_keys", "")
    assert await import_env_keys(db_session) == 0
```

- [ ] **Step 2: Run to watch it fail**

Run: `uv run pytest tests/test_tenants_cli.py -v`
Expected: `ModuleNotFoundError: No module named 'app.tenants'`

- [ ] **Step 3: Implement the command functions**

`app/tenants/__init__.py`:

```python
"""Tenant administration commands, shared by the CLI and startup import."""

import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.keys import generate_key, hash_key, key_prefix
from app.models import ApiKey, Tenant


async def _tenant_by_client_id(session: AsyncSession, client_id: str) -> Tenant:
    tenant = (
        await session.execute(select(Tenant).where(Tenant.client_id == client_id))
    ).scalar_one_or_none()
    if tenant is None:
        raise ValueError(f"tenant {client_id!r} not found")
    return tenant


async def create_tenant(
    session: AsyncSession,
    client_id: str,
    name: str = "",
    rpm: int = 60,
    clause_per_day: int = 10,
) -> str:
    existing = (
        await session.execute(select(Tenant).where(Tenant.client_id == client_id))
    ).scalar_one_or_none()
    if existing is not None:
        raise ValueError(f"tenant {client_id!r} already exists")
    tenant = Tenant(
        client_id=client_id, name=name, rpm_limit=rpm, clause_audits_per_day=clause_per_day
    )
    session.add(tenant)
    await session.flush()
    key = generate_key()
    session.add(
        ApiKey(tenant_id=tenant.id, key_hash=hash_key(key), prefix=key_prefix(key))
    )
    await session.commit()
    return key


async def new_key(session: AsyncSession, client_id: str) -> str:
    tenant = await _tenant_by_client_id(session, client_id)
    key = generate_key()
    session.add(
        ApiKey(tenant_id=tenant.id, key_hash=hash_key(key), prefix=key_prefix(key))
    )
    await session.commit()
    return key


async def revoke_key(session: AsyncSession, prefix: str) -> None:
    api_key = (
        await session.execute(select(ApiKey).where(ApiKey.prefix == prefix))
    ).scalar_one_or_none()
    if api_key is None:
        raise ValueError(f"no key with prefix {prefix!r}")
    api_key.status = "revoked"
    await session.commit()


async def set_limits(
    session: AsyncSession,
    client_id: str,
    rpm: int | None = None,
    clause_per_day: int | None = None,
) -> None:
    tenant = await _tenant_by_client_id(session, client_id)
    if rpm is not None:
        tenant.rpm_limit = rpm
    if clause_per_day is not None:
        tenant.clause_audits_per_day = clause_per_day
    await session.commit()


async def set_status(session: AsyncSession, client_id: str, status: str) -> None:
    tenant = await _tenant_by_client_id(session, client_id)
    tenant.status = status
    await session.commit()


async def import_env_keys(session: AsyncSession) -> int:
    """Seed tenants and keys from the API_KEYS env pairs. Idempotent."""
    imported = 0
    for entry in settings.api_keys.split(","):
        if ":" not in entry:
            continue
        key, client_id = (part.strip() for part in entry.split(":", 1))
        if not key or not client_id:
            continue
        exists = (
            await session.execute(
                select(ApiKey).where(ApiKey.key_hash == hash_key(key))
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        tenant = (
            await session.execute(select(Tenant).where(Tenant.client_id == client_id))
        ).scalar_one_or_none()
        if tenant is None:
            tenant = Tenant(client_id=client_id, name=client_id)
            session.add(tenant)
            await session.flush()
        session.add(
            ApiKey(
                tenant_id=tenant.id, key_hash=hash_key(key), prefix=key_prefix(key)
            )
        )
        imported += 1
    await session.commit()
    return imported
```

- [ ] **Step 4: Run the tests again**

Run: `uv run pytest tests/test_tenants_cli.py -v`
Expected: 6 passed

- [ ] **Step 5: Write the CLI entry point**

`app/tenants/__main__.py`:

```python
"""Admin CLI: uv run python -m app.tenants <command> ..."""

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.db import async_session_factory
from app.models import Tenant, UsageCounter
from app.tenants import (
    create_tenant,
    import_env_keys,
    new_key,
    revoke_key,
    set_limits,
    set_status,
)


async def _list_tenants() -> None:
    async with async_session_factory() as session:
        tenants = (await session.execute(select(Tenant))).scalars().all()
        today = datetime.now(UTC).date()
        counters = (
            await session.execute(
                select(UsageCounter).where(UsageCounter.day == today)
            )
        ).scalars().all()
        by_tenant: dict = {}
        for c in counters:
            by_tenant.setdefault(c.tenant_id, {})[c.endpoint_class] = c.count
        for t in tenants:
            usage = by_tenant.get(t.id, {})
            print(
                f"{t.client_id:20} {t.status:10} rpm={t.rpm_limit:<5} "
                f"clause/day={t.clause_audits_per_day:<5} today={usage or '-'}"
            )


async def _usage(client_id: str, days: int) -> None:
    async with async_session_factory() as session:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.client_id == client_id))
        ).scalar_one()
        since = datetime.now(UTC).date() - timedelta(days=days)
        rows = (
            await session.execute(
                select(UsageCounter)
                .where(UsageCounter.tenant_id == tenant.id, UsageCounter.day >= since)
                .order_by(UsageCounter.day, UsageCounter.endpoint_class)
            )
        ).scalars().all()
        for r in rows:
            print(f"{r.day} {r.endpoint_class:14} {r.count}")


async def _run(args: argparse.Namespace) -> None:
    async with async_session_factory() as session:
        if args.command == "create":
            key = await create_tenant(
                session, args.client_id, args.name, args.rpm, args.clause_per_day
            )
            print(f"created {args.client_id}")
            print(f"api key (shown once): {key}")
        elif args.command == "new-key":
            key = await new_key(session, args.client_id)
            print(f"api key (shown once): {key}")
        elif args.command == "revoke-key":
            await revoke_key(session, args.prefix)
            print(f"revoked {args.prefix}")
        elif args.command == "suspend":
            await set_status(session, args.client_id, "suspended")
            print(f"suspended {args.client_id}")
        elif args.command == "activate":
            await set_status(session, args.client_id, "active")
            print(f"activated {args.client_id}")
        elif args.command == "set-limits":
            await set_limits(session, args.client_id, args.rpm, args.clause_per_day)
            print(f"updated {args.client_id}")
        elif args.command == "import-env-keys":
            count = await import_env_keys(session)
            print(f"imported {count} keys")


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.tenants")
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create")
    create.add_argument("client_id")
    create.add_argument("--name", default="")
    create.add_argument("--rpm", type=int, default=60)
    create.add_argument("--clause-per-day", type=int, default=10, dest="clause_per_day")

    newkey = sub.add_parser("new-key")
    newkey.add_argument("client_id")

    revoke = sub.add_parser("revoke-key")
    revoke.add_argument("prefix")

    for name in ("suspend", "activate"):
        p = sub.add_parser(name)
        p.add_argument("client_id")

    limits = sub.add_parser("set-limits")
    limits.add_argument("client_id")
    limits.add_argument("--rpm", type=int, default=None)
    limits.add_argument("--clause-per-day", type=int, default=None, dest="clause_per_day")

    sub.add_parser("import-env-keys")
    sub.add_parser("list")

    usage = sub.add_parser("usage")
    usage.add_argument("client_id")
    usage.add_argument("--days", type=int, default=30)

    args = parser.parse_args()
    if args.command == "list":
        asyncio.run(_list_tenants())
    elif args.command == "usage":
        asyncio.run(_usage(args.client_id, args.days))
    else:
        asyncio.run(_run(args))


main()
```

Smoke-check locally (against the dev DB): `uv run python -m app.tenants list`
Expected: prints nothing or existing rows, exits 0.

- [ ] **Step 6: Startup import in lifespan**

In `app/main.py`, add to the imports:

```python
import logging

from app.core.config import clause_audit_enabled, settings
from app.core.db import async_session_factory, get_session
from app.tenants import import_env_keys
```

and at the top of `lifespan`, after `configure_logging()`:

```python
    if settings.api_keys:
        async with async_session_factory() as session:
            imported = await import_env_keys(session)
        if imported:
            logging.getLogger(__name__).info(
                "imported %d api keys from env", imported
            )
```

- [ ] **Step 7: Document server usage**

Add to `deploy/README.md`, after the "Deploy / roll back" section:

```markdown
## Tenants

All tenant administration runs on the droplet:

```bash
ssh "$LEASE_DEPLOY_SERVER" "cd /opt/lease-compliance \
  && docker compose exec api uv run python -m app.tenants list"
```

Commands: `create <client_id> --name NAME [--rpm N] [--clause-per-day N]`,
`new-key <client_id>`, `revoke-key <prefix>`, `suspend`/`activate
<client_id>`, `set-limits <client_id> --rpm N --clause-per-day N`,
`usage <client_id> --days 30`, `import-env-keys`. `create` and `new-key`
print the plaintext key once; it is never stored or shown again.
```

- [ ] **Step 8: Full suite, ruff, commit**

```bash
uv run pytest
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/tenants/ app/main.py deploy/README.md tests/test_tenants_cli.py
git commit -m "Add tenant admin CLI and startup key import"
git push origin main
```

Expected: CI green.

---

### Task 6: Production rollout (interactive)

No repo changes. Run each step and verify before the next.

- [ ] **Step 1: Deploy**

```bash
export LEASE_DEPLOY_SERVER=deploy@168.144.169.66 LEASE_DEPLOY_DOMAIN=api.leasekoala.com
./deploy/deploy.sh
```

Expected: migration `a1c47e92b5d3 -> b3e8f2a91c47` applies; health returns
200 after the TLS-ready retry. The api log shows
`imported 1 api keys from env` on first boot.

- [ ] **Step 2: Verify the SaaS key still works**

```bash
ssh deploy@168.144.169.66 "cd /opt/lease-compliance && docker compose exec api uv run python -m app.tenants list"
```

Expected: one row, `rentalapp`, active, rpm=60 clause/day=10. Then press
"Check now" on a lease in the local SaaS and confirm it completes.

- [ ] **Step 3: Remove API_KEYS from the server env**

```bash
ssh deploy@168.144.169.66 "sed -i '/^API_KEYS=/d' /opt/lease-compliance/.env"
ssh deploy@168.144.169.66 "cd /opt/lease-compliance && docker compose up -d api"
```

Expected: api restarts, log does NOT show an import line, SaaS "Check now"
still works (auth now comes from the database alone).

- [ ] **Step 4: Raise rentalapp limits**

```bash
ssh deploy@168.144.169.66 "cd /opt/lease-compliance \
  && docker compose exec api uv run python -m app.tenants set-limits rentalapp --rpm 300 --clause-per-day 200 \
  && docker compose exec api uv run python -m app.tenants list"
```

Expected: rpm=300, clause/day=200.

- [ ] **Step 5: Record completion**

Append to `.superpowers/sdd/progress.md`: tenant foundation deployed
(migration b3e8f2a91c47, rentalapp imported from env, env var removed,
limits 300/200). Commit nothing unless docs changed during rollout.

---

## Self-review

- Spec coverage: data model (Task 1), auth path incl. cache/401/403 (Task
  2), rpm limit + Retry-After (Task 3), daily quota + usage counters
  (Task 4), CLI all eight commands + startup import + runbook (Task 5),
  migration/rollout incl. env removal and rentalapp limits (Task 6).
  Defaults and key format pinned in Global Constraints. No gaps found.
- Placeholders: none; every code step carries full code.
- Type consistency: `TenantContext` fields, `TenantDep`, `record_usage`,
  and CLI function signatures match across Tasks 2-5; `clear_auth_cache`
  and `clear_buckets` are produced before conftest consumes them.
