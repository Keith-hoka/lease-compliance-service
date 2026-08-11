"""Judge error taxonomy; the provider-failover breaker lands beside it."""

import logging
import time
from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from app.clause_audit.document import DocumentInput

JudgeFn = Callable[[DocumentInput, str, type[BaseModel]], Awaitable[BaseModel]]

logger = logging.getLogger("app.llm")


class JudgeError(RuntimeError):
    """Content-level failure: the provider answered but produced no usable output."""


class ProviderDown(JudgeError):
    """Infrastructure-level failure: connection, timeout, 5xx, or exhausted 429 retries."""


FAILURE_THRESHOLD = 3
COOLDOWN_SECONDS = 300.0


class FailoverJudge:
    """Callable judge that routes to a backup while the primary is down.

    closed: calls go to primary; FAILURE_THRESHOLD consecutive ProviderDown
    failures trip to open. open: calls go to backup until COOLDOWN_SECONDS
    elapse, then the next call probes primary (half_open). Any primary
    response - including a content-level JudgeError - closes the breaker;
    ProviderDown re-opens it. The state property reflects the last call,
    not wall-clock time. drain_models_used() returns the refs that judged
    successfully since the previous drain, in first-use order.
    """

    def __init__(
        self,
        primary: JudgeFn,
        primary_ref: str,
        backup: JudgeFn | None = None,
        backup_ref: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._primary = primary
        self._primary_ref = primary_ref
        self._backup = backup
        self._backup_ref = backup_ref
        self._clock = clock
        self._state = "closed"
        self._failures = 0
        self._opened_at = 0.0
        self._models_used: dict[str, None] = {}

    @property
    def state(self) -> str:
        return self._state

    @property
    def active_model(self) -> str:
        if self._state == "open":
            return self._backup_ref
        return self._primary_ref

    def drain_models_used(self) -> list[str]:
        used = list(self._models_used)
        self._models_used.clear()
        return used

    async def __call__(self, doc, instruction, output_model):
        if self._state == "open" and self._clock() - self._opened_at >= COOLDOWN_SECONDS:
            self._state = "half_open"
            logger.warning("failover breaker half-open: probing primary %s", self._primary_ref)
        if self._state == "open":
            return await self._on_backup(doc, instruction, output_model)
        try:
            result = await self._primary(doc, instruction, output_model)
        except ProviderDown:
            self._register_primary_down()
            if self._state == "open":
                return await self._on_backup(doc, instruction, output_model)
            raise
        except JudgeError:
            self._register_primary_up()
            raise
        self._register_primary_up()
        self._models_used.setdefault(self._primary_ref)
        return result

    def _register_primary_up(self) -> None:
        self._failures = 0
        if self._state == "half_open":
            self._state = "closed"
            logger.info("failover breaker closed: primary %s recovered", self._primary_ref)

    def _register_primary_down(self) -> None:
        if self._state == "half_open":
            self._trip("probe failed")
            return
        self._failures += 1
        if self._failures >= FAILURE_THRESHOLD and self._backup is not None:
            self._trip(f"{self._failures} consecutive failures")

    def _trip(self, why: str) -> None:
        self._state = "open"
        self._opened_at = self._clock()
        self._failures = 0
        logger.warning(
            "failover breaker open (%s): routing %s traffic to %s",
            why,
            self._primary_ref,
            self._backup_ref,
        )

    async def _on_backup(self, doc, instruction, output_model):
        result = await self._backup(doc, instruction, output_model)
        self._models_used.setdefault(self._backup_ref)
        return result
