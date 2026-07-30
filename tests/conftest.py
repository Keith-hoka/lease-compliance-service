import os

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import clear_auth_cache
from app.core.db import Base, get_session
from app.core.keys import hash_key, key_prefix
from app.core.ratelimit import clear_buckets
from app.main import app
from app.models import ApiKey, Tenant

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://rental:rental@localhost:5433/lease_compliance_test",
)


@pytest.fixture
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def client(db_engine):
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


class FakeJudge:
    """Canned judge: responses keyed by the output model's class name."""

    def __init__(self):
        self.responses = {}
        self.calls = []

    async def __call__(self, doc, instruction, output_model):
        self.calls.append((doc, instruction, output_model))
        return output_model.model_validate(self.responses[output_model.__name__])


@pytest.fixture
def fake_judge():
    return FakeJudge()


@pytest.fixture(autouse=True)
def reset_tenant_state():
    clear_auth_cache()
    clear_buckets()
    yield
    clear_auth_cache()
    clear_buckets()


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
