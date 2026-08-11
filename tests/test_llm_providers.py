"""Provider adapter and judge-construction tests (no network)."""

from types import SimpleNamespace

import httpx
import pytest
from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import InternalServerError as AnthropicServerError
from anthropic import RateLimitError as AnthropicRateLimitError

from app.clause_audit.document import DocumentInput
from app.llm.client import parse_model_ref
from app.llm.failover import JudgeError, ProviderDown
from app.llm.providers import anthropic as anthropic_provider
from app.llm.schemas import FieldsOutput

DOC = DocumentInput(kind="text", text="lease body")


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
    assert kwargs["max_tokens"] == 8000
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


async def test_anthropic_response_validation_error_maps_to_judge_error(monkeypatch):
    from anthropic import APIResponseValidationError

    error = APIResponseValidationError(
        response=httpx.Response(200, request=httpx.Request("POST", "https://api.test")), body=None
    )
    judge, _ = _anthropic_judge(monkeypatch, [error])
    with pytest.raises(JudgeError) as exc_info:
        await judge(DOC, "i", FieldsOutput)
    assert not isinstance(exc_info.value, ProviderDown)


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


async def test_openai_response_validation_error_maps_to_judge_error(monkeypatch):
    from openai import APIResponseValidationError

    error = APIResponseValidationError(
        response=httpx.Response(200, request=httpx.Request("POST", "https://api.test")), body=None
    )
    judge, _ = _openai_judge(monkeypatch, [error])
    with pytest.raises(JudgeError) as exc_info:
        await judge(DOC, "i", FieldsOutput)
    assert not isinstance(exc_info.value, ProviderDown)


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
