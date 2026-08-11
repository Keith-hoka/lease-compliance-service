"""Judge error taxonomy; the provider-failover breaker lands beside it."""

from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from app.clause_audit.document import DocumentInput

JudgeFn = Callable[[DocumentInput, str, type[BaseModel]], Awaitable[BaseModel]]


class JudgeError(RuntimeError):
    """Content-level failure: the provider answered but produced no usable output."""


class ProviderDown(JudgeError):
    """Infrastructure-level failure: connection, timeout, 5xx, or exhausted 429 retries."""
