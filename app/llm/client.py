"""The judge: one structured-output call per check family, cache-sharing request shape."""

import base64
import logging
from collections.abc import Awaitable, Callable

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from app.clause_audit.document import DocumentInput
from app.core.config import settings
from app.llm.prompts import SYSTEM

logger = logging.getLogger("app.llm")

JudgeFn = Callable[[DocumentInput, str, type[BaseModel]], Awaitable[BaseModel]]


class JudgeError(RuntimeError):
    pass


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


def build_parse_kwargs(model: str, doc: DocumentInput, instruction: str) -> dict:
    return {
        "model": model,
        "max_tokens": 8000,
        "thinking": {"type": "adaptive"},
        "system": SYSTEM,
        "messages": [
            {
                "role": "user",
                "content": [document_block(doc), {"type": "text", "text": instruction}],
            }
        ],
    }


def make_judge() -> JudgeFn:
    client = AsyncAnthropic(api_key=settings.anthropic_api_key)

    async def judge(doc: DocumentInput, instruction: str, output_model: type[BaseModel]):
        kwargs = build_parse_kwargs(settings.clause_audit_model, doc, instruction)
        response = await client.messages.parse(**kwargs, output_format=output_model)
        usage = getattr(response, "usage", None)
        if usage is not None:
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
        if response.parsed_output is None:
            raise JudgeError("model returned no parseable output")
        return response.parsed_output

    return judge
