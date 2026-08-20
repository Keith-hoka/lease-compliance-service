"""Renewal rent suggestions: deterministic range and law card, one judged figure."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TenantDep
from app.core.db import get_session
from app.core.ratelimit import enforce_rate_limit
from app.core.usage import record_usage
from app.llm.failover import JudgeError
from app.rent_suggest.service import build_suggestion
from app.schemas.rent_suggestions import RentSuggestionRequest, RentSuggestionResponse

router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])
SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/rent-suggestions", response_model=RentSuggestionResponse)
async def create_rent_suggestion(
    body: RentSuggestionRequest, tenant: TenantDep, session: SessionDep
) -> RentSuggestionResponse:
    try:
        response = await build_suggestion(session, body)
    except JudgeError as exc:
        raise HTTPException(status_code=502, detail={"code": "judge_unavailable"}) from exc
    await record_usage(session, tenant.tenant_id, "rent_suggestions")
    await session.commit()
    return response
