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
