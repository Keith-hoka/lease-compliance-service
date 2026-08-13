# Provider Failover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatic runtime failover from Anthropic to an eval-gated OpenAI
backup behind the unchanged judge interface, with the Anthropic client
hardened to `messages.create` + own validation.

**Architecture:** Provider adapters (`app/llm/providers/`) expose
`make_<provider>_judge(model) -> JudgeFn`; `make_judge()` composes primary
and backup into a `FailoverJudge` circuit-breaker wrapper (closed ->
open after 3 consecutive infrastructure failures -> half-open probe after
300 s). Error taxonomy decides switching: `ProviderDown` (infra) counts,
`JudgeError` (content) never does. Spec:
`docs/superpowers/specs/2026-08-11-provider-failover-design.md`.

**Tech Stack:** Python 3.12, FastAPI, anthropic SDK (messages.create +
`output_config` constrained decoding), openai SDK (Responses API + strict
structured outputs), pytest.

## Global Constraints

- `uv` only: `uv run ...`, `uv add ...` — never `python3`/`pip`.
- Ruff sequence before every push, exact order: `uv run ruff format .` ->
  `uv run ruff check --fix .` -> `uv run ruff check .` ->
  `uv run ruff format --check .`.
- TDD: failing test first, watch it fail for the right reason. No emojis.
  Docstrings over comments. No defensive programming.
- Commit trailer: `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- `JudgeFn` signature is frozen:
  `Callable[[DocumentInput, str, type[BaseModel]], Awaitable[BaseModel]]`.
  Processor and call sites must not change.
- `ProviderDown` subclasses `JudgeError`. 4xx client errors are
  `JudgeError`, never `ProviderDown`.
- Breaker constants: `FAILURE_THRESHOLD = 3`, `COOLDOWN_SECONDS = 300.0`.
- Anthropic: `max_tokens=16000` (amended from 8000, owner decision 2026-08-12 - truncation evidence), `thinking={"type": "adaptive"}`,
  `cache_control` placement unchanged. OpenAI:
  `max_output_tokens=16000`, `reasoning={"effort": "medium"}`.
- Model refs: `provider:model` with prefixes `anthropic:`/`openai:`;
  bare ref means anthropic. Backup candidate `openai:gpt-5.6-terra`,
  escalation `openai:gpt-5.6-sol`.
- The usage log line format is frozen:
  `"judge call %s: input=%s cache_read=%s cache_write=%s output=%s"`.
- Both providers share `SYSTEM`, instruction text, and output models
  verbatim.
- Eval gates unchanged and never lowered: prohibited families pooled
  P>=0.9/R>=0.8; standard-form per-term recall at n=6 with family-pooled
  precision (amended 2026-08-13, owner decision — see
  tests/test_llm_eval.py::_assert_sf_thresholds); fields; PDF smokes.
- Secrets: keys live in `.env` files only, never printed, never in shell
  history.
- Tasks 6-8 are controller-run (eval discipline, production ops, secrets);
  Tasks 1-5 are normal implementer tasks.

---

### Task 1: Error taxonomy, strict schema transform, model-ref parsing

**Files:**
- Create: `app/llm/failover.py` (taxonomy only; the breaker class lands in
  Task 4)
- Modify: `app/llm/client.py:16-20` (JudgeFn/JudgeError move out;
  re-import)
- Modify: `app/llm/schemas.py` (add `strict_schema`)
- Test: `tests/test_llm_providers.py` (new), `tests/test_clause_schemas.py`
  (add strict_schema tests)

**Interfaces:**
- Produces: `app.llm.failover.JudgeFn`, `JudgeError`,
  `ProviderDown(JudgeError)`; `app.llm.schemas.strict_schema(model) ->
  dict`; `app.llm.client.parse_model_ref(ref) -> tuple[str, str]`.
  `app.llm.client` re-exports `JudgeFn`/`JudgeError` so existing imports
  (`worker.py`, `processor.py`, tests) keep working.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm_providers.py`:

```python
"""Provider adapter and judge-construction tests (no network)."""

import pytest

from app.llm.client import parse_model_ref
from app.llm.failover import JudgeError, ProviderDown


def test_provider_down_is_a_judge_error():
    assert issubclass(ProviderDown, JudgeError)


def test_parse_model_ref_defaults_bare_to_anthropic():
    assert parse_model_ref("claude-sonnet-5") == ("anthropic", "claude-sonnet-5")


def test_parse_model_ref_splits_prefix():
    assert parse_model_ref("openai:gpt-5.6-terra") == ("openai", "gpt-5.6-terra")
    assert parse_model_ref("anthropic:claude-opus-4-8") == ("anthropic", "claude-opus-4-8")


def test_parse_model_ref_rejects_unknown_provider():
    with pytest.raises(ValueError, match="unknown provider"):
        parse_model_ref("deepseek:chat")
```

Append to `tests/test_clause_schemas.py`:

```python
def test_strict_schema_closes_objects_and_requires_all_fields():
    from app.llm.schemas import family_output_model, strict_schema

    model = family_output_model(
        "ProhibitedOutput", ["nsw.clause.carpet_cleaning", "nsw.clause.fumigation"]
    )
    schema = strict_schema(model)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["items"]
    item = schema["$defs"]["ProhibitedOutputItem"]
    assert item["additionalProperties"] is False
    assert set(item["required"]) == {"rule_id", "verdict", "reasoning", "clause_quote"}
    assert "default" not in item["properties"]["clause_quote"]


def test_strict_schema_output_still_validates_nullable_fields():
    from app.llm.schemas import family_output_model, strict_schema

    model = family_output_model("ProhibitedOutput", ["nsw.clause.fumigation"])
    strict_schema(model)
    parsed = model.model_validate(
        {
            "items": [
                {
                    "rule_id": "nsw.clause.fumigation",
                    "verdict": "green",
                    "reasoning": "absent",
                    "clause_quote": None,
                }
            ]
        }
    )
    assert parsed.items[0].clause_quote is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_providers.py tests/test_clause_schemas.py -v`
Expected: FAIL — `ModuleNotFoundError: app.llm.failover` and
`ImportError: strict_schema`.

- [ ] **Step 3: Implement**

Create `app/llm/failover.py`:

```python
"""Judge error taxonomy; the provider-failover breaker lands beside it."""

from collections.abc import Awaitable, Callable

from pydantic import BaseModel

from app.clause_audit.document import DocumentInput

JudgeFn = Callable[[DocumentInput, str, type[BaseModel]], Awaitable[BaseModel]]


class JudgeError(RuntimeError):
    """Content-level failure: the provider answered but produced no usable output."""


class ProviderDown(JudgeError):
    """Infrastructure-level failure: connection, timeout, 5xx, or exhausted 429 retries."""
```

In `app/llm/client.py`: delete the local `JudgeFn = ...` and
`class JudgeError` definitions, and replace with imports plus
`parse_model_ref`:

```python
from app.llm.failover import JudgeError, JudgeFn, ProviderDown

PROVIDERS = ("anthropic", "openai")


def parse_model_ref(ref: str) -> tuple[str, str]:
    """Split 'provider:model'; a bare ref means anthropic."""
    provider, sep, model = ref.partition(":")
    if not sep:
        return "anthropic", ref
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider prefix in model ref: {ref}")
    return provider, model
```

(`ProviderDown` is imported so `app.llm.client` re-exports the full
taxonomy; `make_judge` and the rest of client.py stay as they are for now.
If ruff flags the unused import, add
`__all__ = ["JudgeError", "JudgeFn", "ProviderDown", "make_judge", "parse_model_ref"]`.)

Append to `app/llm/schemas.py`:

```python
def strict_schema(model: type[BaseModel]) -> dict:
    """JSON schema with every object closed, every property required, defaults stripped.

    Both providers' constrained decoding wants closed objects; OpenAI strict
    mode additionally rejects optional properties and default annotations.
    Optional fields stay nullable through their anyOf, so the model emits an
    explicit null instead of omitting the key.
    """
    schema = model.model_json_schema()
    for node in (schema, *schema.get("$defs", {}).values()):
        _close(node)
    return schema


def _close(node: dict) -> None:
    if "properties" not in node:
        return
    node["additionalProperties"] = False
    node["required"] = list(node["properties"])
    for prop in node["properties"].values():
        prop.pop("default", None)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -m "not llm_eval" -q`
Expected: PASS (existing `from app.llm.client import JudgeError, JudgeFn`
sites resolve through the re-import).

- [ ] **Step 5: Ruff sequence, commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/llm/failover.py app/llm/client.py app/llm/schemas.py tests/test_llm_providers.py tests/test_clause_schemas.py
git commit -m "Add judge error taxonomy, strict schema transform, model-ref parsing"
```

---

### Task 2: Hardened Anthropic adapter

**Files:**
- Create: `app/llm/providers/__init__.py` (empty), `app/llm/providers/anthropic.py`
- Modify: `app/llm/client.py` (make_judge delegates; drop the old parse
  path, `document_block`, `build_parse_kwargs`, the AsyncAnthropic import,
  and the `SYSTEM` import)
- Test: `tests/test_llm_providers.py`, `tests/test_llm_plumbing.py`

**Interfaces:**
- Consumes: `JudgeError`/`ProviderDown`/`JudgeFn` from `app.llm.failover`;
  `strict_schema` from `app.llm.schemas`; `parse_model_ref` from Task 1.
- Produces: `app.llm.providers.anthropic.make_anthropic_judge(model: str)
  -> JudgeFn`, `document_block(doc) -> dict`,
  `build_create_kwargs(model, doc, instruction, output_model) -> dict`;
  `app.llm.client._provider_judge(ref: str) -> JudgeFn` (anthropic branch;
  Task 3 adds openai).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_llm_providers.py`:

```python
import httpx
from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import InternalServerError as AnthropicServerError
from anthropic import RateLimitError as AnthropicRateLimitError
from types import SimpleNamespace

from app.clause_audit.document import DocumentInput
from app.llm.providers import anthropic as anthropic_provider
from app.llm.schemas import FieldsOutput

DOC = DocumentInput(kind="text", text="lease body")


def _anthropic_response(stop_reason="end_turn", text='{"fields": []}'):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=10,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
            output_tokens=5,
        ),
        content=[
            SimpleNamespace(type="thinking"),
            SimpleNamespace(type="text", text=text),
        ],
    )


class StubAnthropic:
    """Scripted messages.create: returns or raises each result in order."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0
        self.messages = self

    async def create(self, **kwargs):
        self.calls += 1
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _anthropic_judge(monkeypatch, results):
    stub = StubAnthropic(results)
    monkeypatch.setattr(anthropic_provider, "AsyncAnthropic", lambda **kw: stub)
    return anthropic_provider.make_anthropic_judge("claude-sonnet-5"), stub


def _request_error(cls):
    return cls(request=httpx.Request("POST", "https://api.test"))


def _status_error(cls, code):
    request = httpx.Request("POST", "https://api.test")
    response = httpx.Response(code, request=request)
    return cls("boom", response=response, body=None)


def test_anthropic_create_kwargs_use_output_config():
    kwargs = anthropic_provider.build_create_kwargs(
        "claude-sonnet-5", DOC, "judge these rules", FieldsOutput
    )
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["max_tokens"] == 16000
    assert kwargs["thinking"] == {"type": "adaptive"}
    fmt = kwargs["output_config"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["schema"]["additionalProperties"] is False
    content = kwargs["messages"][0]["content"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert content[1] == {"type": "text", "text": "judge these rules"}


async def test_anthropic_judge_parses_last_text_block(monkeypatch):
    judge, stub = _anthropic_judge(monkeypatch, [_anthropic_response()])
    result = await judge(DOC, "i", FieldsOutput)
    assert result.fields == [] and stub.calls == 1


async def test_anthropic_refusal_raises_without_retry(monkeypatch):
    judge, stub = _anthropic_judge(monkeypatch, [_anthropic_response(stop_reason="refusal")])
    with pytest.raises(JudgeError, match="declined"):
        await judge(DOC, "i", FieldsOutput)
    assert stub.calls == 1


async def test_anthropic_truncation_raises_without_retry(monkeypatch):
    judge, stub = _anthropic_judge(monkeypatch, [_anthropic_response(stop_reason="max_tokens")])
    with pytest.raises(JudgeError, match="truncated"):
        await judge(DOC, "i", FieldsOutput)
    assert stub.calls == 1


async def test_anthropic_validation_failure_retries_once_then_raises(monkeypatch):
    bad = _anthropic_response(text='{"fields": [truncat')
    judge, stub = _anthropic_judge(monkeypatch, [bad, bad])
    with pytest.raises(JudgeError, match="no parseable output"):
        await judge(DOC, "i", FieldsOutput)
    assert stub.calls == 2


async def test_anthropic_validation_retry_can_succeed(monkeypatch):
    bad = _anthropic_response(text="not json")
    judge, stub = _anthropic_judge(monkeypatch, [bad, _anthropic_response()])
    result = await judge(DOC, "i", FieldsOutput)
    assert result.fields == [] and stub.calls == 2


@pytest.mark.parametrize(
    "error",
    [
        _request_error(AnthropicConnectionError),
        _status_error(AnthropicRateLimitError, 429),
        _status_error(AnthropicServerError, 500),
    ],
)
async def test_anthropic_infra_errors_map_to_provider_down(monkeypatch, error):
    judge, _ = _anthropic_judge(monkeypatch, [error])
    with pytest.raises(ProviderDown):
        await judge(DOC, "i", FieldsOutput)


async def test_anthropic_client_error_maps_to_judge_error(monkeypatch):
    from anthropic import BadRequestError

    judge, _ = _anthropic_judge(monkeypatch, [_status_error(BadRequestError, 400)])
    with pytest.raises(JudgeError) as exc_info:
        await judge(DOC, "i", FieldsOutput)
    assert not isinstance(exc_info.value, ProviderDown)
```

In `tests/test_llm_plumbing.py`:
- change the import `from app.llm.client import build_parse_kwargs,
  document_block` to `from app.llm.providers.anthropic import
  build_create_kwargs, document_block`
- in `test_build_parse_kwargs_shape`, rename to
  `test_build_create_kwargs_shape` and replace the
  `build_parse_kwargs(...)` call with `build_create_kwargs("claude-opus-4-8",
  doc, "judge these rules", FieldsOutput)` (`FieldsOutput` is already
  imported in that file); keep all existing asserts
- delete `test_make_judge_raises_on_refusal` (superseded by the provider
  tests above)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: app.llm.providers`.

- [ ] **Step 3: Implement the adapter**

Create empty `app/llm/providers/__init__.py`. Create
`app/llm/providers/anthropic.py`:

```python
"""Anthropic judge adapter: messages.create with server-side constrained decoding."""

import base64
import logging

from anthropic import (
    APIConnectionError,
    APIStatusError,
    AsyncAnthropic,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from app.clause_audit.document import DocumentInput
from app.core.config import settings
from app.llm.failover import JudgeError, JudgeFn, ProviderDown
from app.llm.prompts import SYSTEM
from app.llm.schemas import strict_schema

logger = logging.getLogger("app.llm")

MAX_TOKENS = 8000


def document_block(doc: DocumentInput) -> dict:
    if doc.kind == "text":
        framed = f"<lease_document>\n{doc.text}\n</lease_document>"
        return {"type": "text", "text": framed, "cache_control": {"type": "ephemeral"}}
    return {
        "type": "document",
        "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.standard_b64encode(doc.pdf).decode(),
        },
        "cache_control": {"type": "ephemeral"},
    }


def build_create_kwargs(
    model: str, doc: DocumentInput, instruction: str, output_model: type[BaseModel]
) -> dict:
    return {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "thinking": {"type": "adaptive"},
        "system": SYSTEM,
        "messages": [
            {
                "role": "user",
                "content": [document_block(doc), {"type": "text", "text": instruction}],
            }
        ],
        "output_config": {"format": {"type": "json_schema", "schema": strict_schema(output_model)}},
    }


def make_anthropic_judge(model: str) -> JudgeFn:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def judge(doc: DocumentInput, instruction: str, output_model: type[BaseModel]):
        kwargs = build_create_kwargs(model, doc, instruction, output_model)
        for attempt in (1, 2):
            try:
                response = await client.messages.create(**kwargs)
            except (APIConnectionError, InternalServerError, RateLimitError) as exc:
                raise ProviderDown(f"anthropic unavailable: {type(exc).__name__}") from exc
            except APIStatusError as exc:
                raise JudgeError(f"anthropic rejected the request: {type(exc).__name__}") from exc
            usage = response.usage
            logger.info(
                "judge call %s: input=%s cache_read=%s cache_write=%s output=%s",
                output_model.__name__,
                usage.input_tokens,
                usage.cache_read_input_tokens,
                usage.cache_creation_input_tokens,
                usage.output_tokens,
            )
            if response.stop_reason == "refusal":
                raise JudgeError("model declined the request")
            if response.stop_reason == "max_tokens":
                raise JudgeError(
                    f"output truncated at max_tokens ({usage.output_tokens} output tokens)"
                )
            texts = [block.text for block in response.content if block.type == "text"]
            payload = texts[-1] if texts else ""
            try:
                return output_model.model_validate_json(payload)
            except ValidationError:
                logger.warning(
                    "judge output failed validation (attempt %d/2, stop_reason=%s): %.200s",
                    attempt,
                    response.stop_reason,
                    payload,
                )
        raise JudgeError("model returned no parseable output")

    return judge
```

Exception mapping note (why the order matters): `RateLimitError` and
`InternalServerError` subclass `APIStatusError`, so they must be listed in
the earlier `except` clause; the remaining `APIStatusError` catch is the
4xx client-error path. Exception messages carry only the class name
because `JudgeError` text becomes client-visible `job.error`.

In `app/llm/client.py`: delete `document_block`, `build_parse_kwargs`, the
old `make_judge` body, and the now-unused imports (`base64`, `logging`,
`AsyncAnthropic`, `BaseModel`, `DocumentInput`, `SYSTEM`); replace with:

```python
"""Judge construction: provider selection behind the frozen JudgeFn interface."""

from app.core.config import settings
from app.llm.failover import JudgeError, JudgeFn, ProviderDown
from app.llm.providers.anthropic import make_anthropic_judge

__all__ = ["JudgeError", "JudgeFn", "ProviderDown", "make_judge", "parse_model_ref"]

PROVIDERS = ("anthropic", "openai")


def parse_model_ref(ref: str) -> tuple[str, str]:
    """Split 'provider:model'; a bare ref means anthropic."""
    provider, sep, model = ref.partition(":")
    if not sep:
        return "anthropic", ref
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider prefix in model ref: {ref}")
    return provider, model


def _provider_judge(ref: str) -> JudgeFn:
    provider, model = parse_model_ref(ref)
    if provider == "anthropic":
        return make_anthropic_judge(model)
    raise ValueError(f"openai adapter not wired yet: {ref}")


def make_judge() -> JudgeFn:
    return _provider_judge(settings.clause_audit_model)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -m "not llm_eval" -q`
Expected: PASS.

- [ ] **Step 5: Ruff sequence, commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/llm/providers app/llm/client.py tests/test_llm_providers.py tests/test_llm_plumbing.py
git commit -m "Harden the Anthropic judge: messages.create with own validation"
```

---

### Task 3: OpenAI adapter and configuration

**Files:**
- Create: `app/llm/providers/openai_.py` (underscore avoids shadowing the
  `openai` package)
- Modify: `app/core/config.py:11-13`, `app/llm/client.py` (`_provider_judge`
  openai branch)
- Test: `tests/test_llm_providers.py`

**Interfaces:**
- Consumes: taxonomy from `app.llm.failover`; `strict_schema`; `SYSTEM`.
- Produces: `make_openai_judge(model: str) -> JudgeFn`,
  `document_part(doc) -> dict`, `build_response_kwargs(model, doc,
  instruction, output_model) -> dict`; settings `openai_api_key: str = ""`
  and `clause_audit_failover_model: str = ""`; `_provider_judge` raises
  `RuntimeError` when an openai ref is configured without a key.

- [ ] **Step 1: Add the dependency**

```bash
uv add openai
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_llm_providers.py`:

```python
from openai import APIConnectionError as OpenAIConnectionError
from openai import InternalServerError as OpenAIServerError
from openai import RateLimitError as OpenAIRateLimitError

from app.llm.providers import openai_ as openai_provider


def _openai_response(status="completed", text='{"fields": []}', refusal=False):
    if refusal:
        content = [SimpleNamespace(type="refusal", refusal="cannot help")]
    else:
        content = [SimpleNamespace(type="output_text", text=text)]
    return SimpleNamespace(
        status=status,
        incomplete_details=SimpleNamespace(reason="max_tokens"),
        output=[SimpleNamespace(type="message", content=content)],
        output_text="" if refusal else text,
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
        ),
    )


class StubOpenAI:
    """Scripted responses.create: returns or raises each result in order."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0
        self.responses = self

    async def create(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _openai_judge(monkeypatch, results):
    stub = StubOpenAI(results)
    monkeypatch.setattr(openai_provider, "AsyncOpenAI", lambda **kw: stub)
    return openai_provider.make_openai_judge("gpt-5.6-terra"), stub


def test_openai_response_kwargs_shape():
    kwargs = openai_provider.build_response_kwargs(
        "gpt-5.6-terra", DOC, "judge these rules", FieldsOutput
    )
    assert kwargs["model"] == "gpt-5.6-terra"
    assert kwargs["max_output_tokens"] == 16000
    assert kwargs["reasoning"] == {"effort": "medium"}
    fmt = kwargs["text"]["format"]
    assert fmt["type"] == "json_schema"
    assert fmt["name"] == "FieldsOutput"
    assert fmt["strict"] is True
    assert fmt["schema"]["additionalProperties"] is False
    content = kwargs["input"][0]["content"]
    assert content[0] == {
        "type": "input_text",
        "text": "<lease_document>\nlease body\n</lease_document>",
    }
    assert content[1] == {"type": "input_text", "text": "judge these rules"}


def test_openai_pdf_document_part_is_base64_file():
    part = openai_provider.document_part(DocumentInput(kind="pdf", pdf=b"%PDF-fake"))
    assert part["type"] == "input_file"
    assert part["filename"] == "lease.pdf"
    assert part["file_data"].startswith("data:application/pdf;base64,")


async def test_openai_judge_parses_output(monkeypatch):
    judge, stub = _openai_judge(monkeypatch, [_openai_response()])
    result = await judge(DOC, "i", FieldsOutput)
    assert result.fields == [] and stub.calls == 1


async def test_openai_incomplete_raises_truncation(monkeypatch):
    judge, stub = _openai_judge(monkeypatch, [_openai_response(status="incomplete")])
    with pytest.raises(JudgeError, match="truncated"):
        await judge(DOC, "i", FieldsOutput)
    assert stub.calls == 1


async def test_openai_refusal_raises_without_retry(monkeypatch):
    judge, stub = _openai_judge(monkeypatch, [_openai_response(refusal=True)])
    with pytest.raises(JudgeError, match="declined"):
        await judge(DOC, "i", FieldsOutput)
    assert stub.calls == 1


async def test_openai_validation_failure_retries_once(monkeypatch):
    bad = _openai_response(text="not json")
    judge, stub = _openai_judge(monkeypatch, [bad, _openai_response()])
    result = await judge(DOC, "i", FieldsOutput)
    assert result.fields == [] and stub.calls == 2


@pytest.mark.parametrize(
    "error",
    [
        _request_error(OpenAIConnectionError),
        _status_error(OpenAIRateLimitError, 429),
        _status_error(OpenAIServerError, 500),
    ],
)
async def test_openai_infra_errors_map_to_provider_down(monkeypatch, error):
    judge, _ = _openai_judge(monkeypatch, [error])
    with pytest.raises(ProviderDown):
        await judge(DOC, "i", FieldsOutput)


def test_provider_judge_requires_openai_key(monkeypatch):
    from app.core.config import settings
    from app.llm import client as client_module

    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        client_module._provider_judge("openai:gpt-5.6-terra")


def test_provider_judge_builds_openai_judge_with_key(monkeypatch):
    from app.core.config import settings
    from app.llm import client as client_module

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    judge = client_module._provider_judge("openai:gpt-5.6-terra")
    assert callable(judge)
```

Note: `OpenAIConnectionError(request=...)` and the status-error helper work
for both SDKs because the openai and anthropic packages share the same
exception constructor shapes.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_llm_providers.py -v`
Expected: FAIL — `ModuleNotFoundError: app.llm.providers.openai_`.

- [ ] **Step 4: Implement**

In `app/core/config.py`, extend `Settings`:

```python
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    clause_audit_model: str = "claude-sonnet-5"
    clause_audit_failover_model: str = ""
```

Create `app/llm/providers/openai_.py`:

```python
"""OpenAI judge adapter: Responses API with strict structured outputs."""

import base64
import logging

from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    InternalServerError,
    RateLimitError,
)
from pydantic import BaseModel, ValidationError

from app.clause_audit.document import DocumentInput
from app.core.config import settings
from app.llm.failover import JudgeError, JudgeFn, ProviderDown
from app.llm.prompts import SYSTEM
from app.llm.schemas import strict_schema

logger = logging.getLogger("app.llm")

MAX_OUTPUT_TOKENS = 16000
REASONING_EFFORT = "medium"


def document_part(doc: DocumentInput) -> dict:
    if doc.kind == "text":
        framed = f"<lease_document>\n{doc.text}\n</lease_document>"
        return {"type": "input_text", "text": framed}
    data = base64.standard_b64encode(doc.pdf).decode()
    return {
        "type": "input_file",
        "filename": "lease.pdf",
        "file_data": f"data:application/pdf;base64,{data}",
    }


def build_response_kwargs(
    model: str, doc: DocumentInput, instruction: str, output_model: type[BaseModel]
) -> dict:
    return {
        "model": model,
        "instructions": SYSTEM,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "reasoning": {"effort": REASONING_EFFORT},
        "input": [
            {
                "role": "user",
                "content": [document_part(doc), {"type": "input_text", "text": instruction}],
            }
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "name": output_model.__name__,
                "strict": True,
                "schema": strict_schema(output_model),
            }
        },
    }


def _refused(response) -> bool:
    for item in response.output:
        if item.type == "message":
            for part in item.content:
                if part.type == "refusal":
                    return True
    return False


def make_openai_judge(model: str) -> JudgeFn:
    client = AsyncOpenAI(api_key=settings.openai_api_key)

    async def judge(doc: DocumentInput, instruction: str, output_model: type[BaseModel]):
        kwargs = build_response_kwargs(model, doc, instruction, output_model)
        for attempt in (1, 2):
            try:
                response = await client.responses.create(**kwargs)
            except (APIConnectionError, InternalServerError, RateLimitError) as exc:
                raise ProviderDown(f"openai unavailable: {type(exc).__name__}") from exc
            except APIStatusError as exc:
                raise JudgeError(f"openai rejected the request: {type(exc).__name__}") from exc
            usage = response.usage
            logger.info(
                "judge call %s: input=%s cache_read=%s cache_write=0 output=%s",
                output_model.__name__,
                usage.input_tokens,
                usage.input_tokens_details.cached_tokens,
                usage.output_tokens,
            )
            if response.status == "incomplete":
                raise JudgeError(
                    f"output truncated ({response.incomplete_details.reason}, "
                    f"{usage.output_tokens} output tokens)"
                )
            if _refused(response):
                raise JudgeError("model declined the request")
            try:
                return output_model.model_validate_json(response.output_text)
            except ValidationError:
                logger.warning(
                    "judge output failed validation (attempt %d/2, status=%s): %.200s",
                    attempt,
                    response.status,
                    response.output_text,
                )
        raise JudgeError("model returned no parseable output")

    return judge
```

In `app/llm/client.py`, replace `_provider_judge`:

```python
from app.llm.providers.openai_ import make_openai_judge


def _provider_judge(ref: str) -> JudgeFn:
    provider, model = parse_model_ref(ref)
    if provider == "anthropic":
        return make_anthropic_judge(model)
    if not settings.openai_api_key:
        raise RuntimeError(f"model ref {ref} requires OPENAI_API_KEY")
    return make_openai_judge(model)
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -m "not llm_eval" -q`
Expected: PASS.

- [ ] **Step 6: Ruff sequence, commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add pyproject.toml uv.lock app/llm/providers/openai_.py app/llm/client.py app/core/config.py tests/test_llm_providers.py
git commit -m "Add the OpenAI judge adapter and failover settings"
```

---

### Task 4: FailoverJudge circuit breaker

**Files:**
- Modify: `app/llm/failover.py` (add the breaker class),
  `app/llm/client.py` (`make_judge` returns the wrapper),
  `tests/test_llm_eval.py` (autouse pollution guard)
- Test: `tests/test_failover.py` (new)

**Interfaces:**
- Consumes: `_provider_judge` from Tasks 2-3.
- Produces: `app.llm.failover.FailoverJudge` — callable satisfying
  `JudgeFn`, constructor `(primary, primary_ref, backup=None,
  backup_ref=None, clock=time.monotonic)`, properties `state` and
  `active_model`, method `drain_models_used() -> list[str]`; constants
  `FAILURE_THRESHOLD = 3`, `COOLDOWN_SECONDS = 300.0`;
  `make_judge() -> FailoverJudge` (always the wrapper, backup only when
  `clause_audit_failover_model` is set).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_failover.py`:

```python
"""Circuit-breaker state machine tests: fake judges, injected clock, no sleeps."""

import pytest

from app.clause_audit.document import DocumentInput
from app.llm.failover import COOLDOWN_SECONDS, FailoverJudge, JudgeError, ProviderDown
from app.llm.schemas import FieldsOutput

DOC = DocumentInput(kind="text", text="x")


def _judge(script):
    """Scripted judge: 'down' raises ProviderDown, 'bad' raises JudgeError, else returned."""
    remaining = list(script)
    calls = []

    async def judge(doc, instruction, output_model):
        calls.append(instruction)
        action = remaining.pop(0)
        if action == "down":
            raise ProviderDown("x")
        if action == "bad":
            raise JudgeError("x")
        return action

    judge.calls = calls
    return judge


def _wrapper(primary_script, backup_script=None, clock=None):
    now = {"t": 0.0}
    primary = _judge(primary_script)
    backup = _judge(backup_script) if backup_script is not None else None
    wrapper = FailoverJudge(
        primary=primary,
        primary_ref="claude-sonnet-5",
        backup=backup,
        backup_ref="openai:gpt-5.6-terra" if backup else None,
        clock=clock or (lambda: now["t"]),
    )
    return wrapper, primary, backup, now


async def test_trips_after_three_consecutive_downs_and_serves_via_backup():
    wrapper, primary, backup, _ = _wrapper(["down", "down", "down"], ["ok"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed"
    assert await wrapper(DOC, "i", FieldsOutput) == "ok"
    assert wrapper.state == "open"
    assert len(primary.calls) == 3 and len(backup.calls) == 1


async def test_success_resets_the_counter():
    wrapper, _, _, _ = _wrapper(["down", "down", "r1", "down", "down"], ["never"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert await wrapper(DOC, "i", FieldsOutput) == "r1"
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed"


async def test_content_error_neither_counts_nor_switches():
    wrapper, _, backup, _ = _wrapper(["down", "down", "bad", "down", "down"], ["never"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    with pytest.raises(JudgeError):
        await wrapper(DOC, "i", FieldsOutput)
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed" and backup.calls == []


async def test_open_routes_everything_to_backup():
    wrapper, primary, backup, _ = _wrapper(["down"] * 3, ["b1", "b2"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    await wrapper(DOC, "i", FieldsOutput)
    assert await wrapper(DOC, "i", FieldsOutput) == "b2"
    assert len(primary.calls) == 3


async def test_half_open_probe_success_recovers():
    wrapper, _, _, now = _wrapper(["down"] * 3 + ["recovered"], ["b1"])
    for _ in range(3):
        try:
            await wrapper(DOC, "i", FieldsOutput)
        except ProviderDown:
            pass
    assert wrapper.state == "open"
    now["t"] += COOLDOWN_SECONDS
    assert await wrapper(DOC, "i", FieldsOutput) == "recovered"
    assert wrapper.state == "closed"


async def test_half_open_probe_failure_reopens_with_fresh_cooldown():
    wrapper, primary, backup, now = _wrapper(["down"] * 4, ["b1", "b2", "b3"])
    for _ in range(3):
        try:
            await wrapper(DOC, "i", FieldsOutput)
        except ProviderDown:
            pass
    now["t"] += COOLDOWN_SECONDS
    assert await wrapper(DOC, "i", FieldsOutput) == "b2"
    assert wrapper.state == "open"
    now["t"] += COOLDOWN_SECONDS - 1
    assert await wrapper(DOC, "i", FieldsOutput) == "b3"
    assert len(primary.calls) == 4


async def test_half_open_probe_content_error_closes():
    wrapper, _, _, now = _wrapper(["down"] * 3 + ["bad"], ["b1"])
    for _ in range(3):
        try:
            await wrapper(DOC, "i", FieldsOutput)
        except ProviderDown:
            pass
    now["t"] += COOLDOWN_SECONDS
    with pytest.raises(JudgeError):
        await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed"


async def test_double_failure_raises_and_stays_open():
    wrapper, _, _, _ = _wrapper(["down"] * 3, ["down"])
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    with pytest.raises(ProviderDown):
        await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "open"


async def test_without_backup_downs_raise_and_never_trip():
    wrapper, _, _, _ = _wrapper(["down"] * 5)
    for _ in range(5):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.state == "closed"


async def test_drain_reports_first_use_order_then_clears():
    wrapper, _, _, _ = _wrapper(["p1", "down", "down", "down"], ["b1"])
    await wrapper(DOC, "i", FieldsOutput)
    for _ in range(2):
        with pytest.raises(ProviderDown):
            await wrapper(DOC, "i", FieldsOutput)
    await wrapper(DOC, "i", FieldsOutput)
    assert wrapper.drain_models_used() == ["claude-sonnet-5", "openai:gpt-5.6-terra"]
    assert wrapper.drain_models_used() == []


async def test_active_model_follows_state():
    wrapper, _, _, _ = _wrapper(["down"] * 3, ["b1"])
    assert wrapper.active_model == "claude-sonnet-5"
    for _ in range(3):
        try:
            await wrapper(DOC, "i", FieldsOutput)
        except ProviderDown:
            pass
    assert wrapper.active_model == "openai:gpt-5.6-terra"


def test_make_judge_returns_wrapper_without_backup(monkeypatch):
    from app.core.config import settings
    from app.llm import client as client_module

    monkeypatch.setattr(settings, "clause_audit_failover_model", "")
    judge = client_module.make_judge()
    assert isinstance(judge, FailoverJudge)
    assert judge.active_model == settings.clause_audit_model


def test_make_judge_wires_backup_when_configured(monkeypatch):
    from app.core.config import settings
    from app.llm import client as client_module

    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(settings, "clause_audit_failover_model", "openai:gpt-5.6-terra")
    judge = client_module.make_judge()
    assert isinstance(judge, FailoverJudge)
    assert judge.state == "closed"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_failover.py -v`
Expected: FAIL — `ImportError: FailoverJudge`.

- [ ] **Step 3: Implement the breaker**

Append to `app/llm/failover.py` (extend the module imports with `import
logging`, `import time`; add `logger = logging.getLogger("app.llm")`):

```python
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
```

In `app/llm/client.py`, replace `make_judge` and extend the failover
import:

```python
from app.llm.failover import FailoverJudge, JudgeError, JudgeFn, ProviderDown


def make_judge() -> FailoverJudge:
    primary_ref = settings.clause_audit_model
    backup_ref = settings.clause_audit_failover_model
    backup = _provider_judge(backup_ref) if backup_ref else None
    return FailoverJudge(
        primary=_provider_judge(primary_ref),
        primary_ref=primary_ref,
        backup=backup,
        backup_ref=backup_ref or None,
    )
```

Add `FailoverJudge` to `__all__`.

- [ ] **Step 4: Add the eval pollution guard**

In `tests/test_llm_eval.py`, next to the existing fixtures (add
`from app.core.config import settings` to the imports if absent):

```python
@pytest.fixture(autouse=True)
def _no_failover_during_eval():
    """A mid-run breaker trip would silently score a different model."""
    assert settings.clause_audit_failover_model == "", (
        "unset CLAUSE_AUDIT_FAILOVER_MODEL for eval runs"
    )
```

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -m "not llm_eval" -q`
Expected: PASS (worker still types `JudgeFn`; the wrapper is callable, so
`main.py` and existing tests are unaffected until Task 5 wires draining).

- [ ] **Step 6: Ruff sequence, commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/llm/failover.py app/llm/client.py tests/test_failover.py tests/test_llm_eval.py
git commit -m "Compose judges behind a provider-failover circuit breaker"
```

---

### Task 5: Worker drain, health exposure, engine bump

**Files:**
- Modify: `app/clause_audit/worker.py:11,53-73,86`, `app/main.py:34-41,56-70`,
  `app/rules/__init__.py:4`
- Test: `tests/test_clause_worker.py`, `tests/test_health.py`

**Interfaces:**
- Consumes: `FailoverJudge` (`state`, `active_model`,
  `drain_models_used()`), `make_judge() -> FailoverJudge`.
- Produces: `run_once(judge: FailoverJudge, ...)` /
  `worker_loop(judge: FailoverJudge, ...)`; `/health` gains
  `"llm_failover": {"state": ..., "active_model": ...}` when the worker is
  running; `app.state.judge` set in lifespan; `ENGINE_VERSION = "1.6.0"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_clause_worker.py`, add after the imports:

```python
from app.llm.failover import FailoverJudge


def _wrap(judge, ref="claude-opus-4-8"):
    return FailoverJudge(primary=judge, primary_ref=ref)
```

Wrap every judge handed to `worker.run_once` / `worker.worker_loop` (the
`fake_judge`, `broken_judge`, `declining_judge`, and `slow_judge` call
sites): e.g. `worker.run_once(_wrap(fake_judge), session_factory)`. The
default ref matches `_job()`'s recorded model so existing assertions are
undisturbed. Then add:

```python
async def test_run_once_records_actual_model(fake_judge, session_factory, seeded_s19):
    fake_judge.responses["ProhibitedOutput"] = RED
    job_id = await _add(session_factory, _job())
    await worker.run_once(_wrap(fake_judge, ref="claude-sonnet-5"), session_factory)
    row = await _fetch(session_factory, job_id)
    assert row.status == "succeeded"
    assert row.model == "claude-sonnet-5"
```

In `tests/test_health.py`, add:

```python
from app.llm.failover import FailoverJudge
from app.main import app


async def test_health_reports_failover_state(client, monkeypatch):
    async def ok(doc, instruction, output_model):
        return None

    judge = FailoverJudge(primary=ok, primary_ref="claude-sonnet-5")
    monkeypatch.setattr(app.state, "judge", judge, raising=False)
    body = (await client.get("/health")).json()
    assert body["llm_failover"] == {"state": "closed", "active_model": "claude-sonnet-5"}
```

and extend `test_health_with_empty_queue` with
`assert "llm_failover" not in body`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_clause_worker.py::test_run_once_records_actual_model tests/test_health.py -v`
Expected: `test_run_once_records_actual_model` FAILS with
`row.model == "claude-opus-4-8"` (no rewrite yet);
`test_health_reports_failover_state` FAILS with `KeyError: 'llm_failover'`.

- [ ] **Step 3: Implement the worker drain**

In `app/clause_audit/worker.py`: change the llm imports to

```python
from app.llm.client import JudgeError
from app.llm.failover import FailoverJudge
```

change both signatures (`run_once(judge: FailoverJudge, ...)`,
`worker_loop(judge: FailoverJudge, ...)`), and rework `run_once`'s body:

```python
async def run_once(judge: FailoverJudge, session_factory=async_session_factory) -> bool:
    """Process at most one job; True when a job was claimed."""
    async with session_factory() as session:
        job = await claim_next(session)
        if job is None:
            return False
        job_id = job.id
        try:
            await asyncio.wait_for(process_job(session, job, judge), JOB_TIMEOUT_SECONDS)
            used = judge.drain_models_used()
            if used:
                job.model = "+".join(used)
            await session.commit()
            logger.info("clause audit job %s succeeded", job_id)
        except TimeoutError:
            logger.warning("clause audit job %s timed out", job_id)
            await _fail(session, job_id, "job timed out")
        except JudgeError as exc:
            logger.warning("clause audit job %s judge error: %s", job_id, exc)
            await _fail(session, job_id, str(exc))
        except Exception:
            logger.exception("clause audit job %s failed", job_id)
            await _fail(session, job_id, INTERNAL_ERROR)
        leftover = judge.drain_models_used()
        if leftover:
            logger.info("clause audit job %s used %s before failing", job_id, "+".join(leftover))
        return True
```

- [ ] **Step 4: Implement health exposure and the lifespan stash**

In `app/main.py`: add `Request` to the fastapi import; in `lifespan`
replace the worker startup block:

```python
    task = None
    if clause_audit_enabled():
        judge = make_judge()
        app.state.judge = judge
        task = asyncio.create_task(worker_loop(judge))
```

and rework `health`:

```python
@app.api_route("/health", methods=["GET", "HEAD"])
async def health(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> dict:
    """Liveness plus the cheapest dead-worker detector: the pending queue.

    HEAD is allowed because uptime monitors probe with it. llm_failover
    appears only when the clause-audit worker is running.
    """
    count, oldest = (
        await session.execute(
            select(func.count(), func.min(ClauseAuditJob.created_at)).where(
                ClauseAuditJob.status == "pending"
            )
        )
    ).one()
    age = (datetime.now(UTC) - oldest).total_seconds() if oldest is not None else None
    payload = {"status": "ok", "clause_audit": {"pending": count, "oldest_pending_seconds": age}}
    judge = getattr(request.app.state, "judge", None)
    if judge is not None:
        payload["llm_failover"] = {"state": judge.state, "active_model": judge.active_model}
    return payload
```

- [ ] **Step 5: Bump the engine version**

In `app/rules/__init__.py:4` set `ENGINE_VERSION = "1.6.0"` (precedent:
the 1.2.0 bump was also a pure client/model change). Run
`grep -rn '"1\.5\.0"' tests/ app/` and update any pinned occurrences
(expected: none — tests build jobs with their own literals).

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest -m "not llm_eval" -q`
Expected: PASS.

- [ ] **Step 7: Ruff sequence, commit**

```bash
uv run ruff format . && uv run ruff check --fix . && uv run ruff check . && uv run ruff format --check .
git add app/clause_audit/worker.py app/main.py app/rules/__init__.py tests/test_clause_worker.py tests/test_health.py
git commit -m "Record actual judge models on jobs and expose failover state on /health"
```

---

### Task 6: Anthropic regression eval (controller-run)

The hardening rewrite changed the request shape (`parse`/`output_format`
-> `create`/`output_config` + all-required schemas), so the primary model
re-passes the full gate. This run also clears the standing debt: NSW
standard-form gates were never measured under the shipped 13ebabb prompt.

- [ ] **Step 1: Preflight** — dev corpus restored, `ANTHROPIC_API_KEY` in
  `.env`, `CLAUSE_AUDIT_FAILOVER_MODEL` unset (the autouse guard enforces
  it). Cost expectation ~$10-15.

- [ ] **Step 2: Run**

```bash
CLAUSE_AUDIT_FAILOVER_MODEL= uv run pytest -m llm_eval -v -s
```

Run in background with a process-exit watchdog per the established
monitoring discipline; do not poll logs with patterns that self-match.

- [ ] **Step 3: Gate** — every family green: prohibited NSW+VIC pooled
  P>=0.9/R>=0.8, standard-form NSW/VIC-F1/VIC-F2 per-term, fields, both
  PDF smokes. Any red: per-case diagnosis before any fix; suspect the
  adapter change first (this run's variable), goldens last (they survived
  30 runs). Thresholds are never adjusted.

- [ ] **Step 4: Record** — append to the results table in
  `docs/model-evals.md`: date 2026-08-11, model claude-sonnet-5
  (hardened create-path client, engine 1.6.0), measured family results,
  wall clock, verdict "hardening regression: pass" — plus one sentence
  noting the NSW standard-form debt from 13ebabb is now measured. Ruff
  sequence, commit `"Record the hardened-client regression eval"`, push,
  CI green.

---

### Task 7: OpenAI backup eval (controller-run)

Tier decision (owner, 2026-08-11 brainstorm): evaluate the cheap tier
first, escalate to flagship on model-side failure. In the current OpenAI
lineup the cheap-tier candidate is `gpt-5.6-terra` ($2/$12 — the
Sonnet-class analogue); `gpt-5.6-luna` is excluded for the same
capability reason that excluded Haiku from the primary; escalation is
`gpt-5.6-sol` ($5/$30).

- [ ] **Step 1: Preflight** — `OPENAI_API_KEY` present in `.env` (never
  echoed; confirm with `grep -c '^OPENAI_API_KEY=' .env`).

- [ ] **Step 2: Run Terra**

```bash
CLAUSE_AUDIT_FAILOVER_MODEL= CLAUSE_AUDIT_MODEL=openai:gpt-5.6-terra uv run pytest -m llm_eval -v -s
```

- [ ] **Step 3: Gate and decide** — pass: the backup is
  `openai:gpt-5.6-terra`. Fail: per-case diagnosis under the established
  discipline (goldens are proven; default suspicion is the model side —
  but check the adapter first for systematic shapes, e.g. every case
  truncated points at `max_output_tokens`, every case invalid points at
  the strict schema). Genuine model-side quality failure: rerun Step 2
  with `openai:gpt-5.6-sol`; that result decides the backup. A second
  failure at Sol is an owner escalation, not a threshold adjustment.

- [ ] **Step 4: Record** — append the run row(s) to `docs/model-evals.md`
  and a short "Backup provider decision" paragraph naming the chosen ref,
  the tier rationale above, and the per-family numbers. Ruff sequence,
  commit `"Record the OpenAI backup eval and decision"`, push, CI green.

---

### Task 8: Rollout (controller-run)

- [ ] **Step 1: Deploy with failover unconfigured** — after Tasks 1-7 are
  pushed and CI is green:

```bash
LEASE_DEPLOY_SERVER=deploy@168.144.169.66 LEASE_DEPLOY_DOMAIN=api.leasekoala.com ./deploy/deploy.sh sha-<short>
```

502 during the boot window is normal; verify the running image with
docker inspect. Production behaviour equals today (inert wrapper).

- [ ] **Step 2: Production acceptance** — one real NSW and one real VIC
  clause audit through `POST /v1/clause-audits` (multipart `payload` JSON
  + `text` field, `X-API-Key` from the SaaS `.env`, value never printed).
  Verify findings look normal, the service log shows the unchanged usage
  line, and `GET /health` shows
  `"llm_failover": {"state": "closed", "active_model": "claude-sonnet-5"}`.

- [ ] **Step 3: Enable the backup** — append `OPENAI_API_KEY=...` and
  `CLAUSE_AUDIT_FAILOVER_MODEL=openai:<decided ref>` to the server-side
  `.env` over ssh (values pasted from the user's secure source, never
  echoed to the terminal or logs), restart the stack, verify `/health`
  still reports `state=closed` with the Anthropic active_model, and run
  one more real audit to confirm normal traffic stays on the primary
  (job model unchanged).

- [ ] **Step 4: Backup path smoke** — temporarily set
  `CLAUSE_AUDIT_MODEL=openai:<decided ref>` on the server, restart, run
  one real audit end to end (this proves network, key, and dependency
  reachability of the OpenAI adapter inside the production container),
  verify the finding shapes and that the job records the openai model
  ref, then revert `CLAUSE_AUDIT_MODEL` and restart. The breaker
  transition itself is unit-tested, not drilled in production.

- [ ] **Step 5: Document** — `deploy/README.md`: add the two env vars
  (`OPENAI_API_KEY`, `CLAUSE_AUDIT_FAILOVER_MODEL`), the `/health`
  `llm_failover` field, and a short "backup smoke" procedure recording
  Step 4. Ruff sequence, commit
  `"Document the failover configuration and backup smoke"`, push, CI
  green.

- [ ] **Step 6: Close** — ledger entry in `.superpowers/sdd/progress.md`;
  final whole-branch review per the SDD flow before declaring the
  milestone done.
