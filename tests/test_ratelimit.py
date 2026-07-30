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


async def test_over_limit_request_gets_429_with_retry_after(client, seeded_tenants, db_session):
    from app.models import Tenant

    tenant = await db_session.get(Tenant, seeded_tenants["testco"].id)
    tenant.rpm_limit = 2
    await db_session.commit()

    headers = {"X-API-Key": "test-key"}
    first = await client.get("/v1/audit-changes", headers=headers)
    assert first.status_code == 200
    await client.get("/v1/audit-changes", headers=headers)
    third = await client.get("/v1/audit-changes", headers=headers)
    assert third.status_code == 429
    assert "Retry-After" in third.headers
