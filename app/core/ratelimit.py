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
