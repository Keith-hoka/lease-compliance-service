"""Anthropic judge adapter: messages.create with server-side constrained decoding."""

import base64
import logging

from anthropic import (
    APIConnectionError,
    APIResponseValidationError,
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
            except (APIStatusError, APIResponseValidationError) as exc:
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
