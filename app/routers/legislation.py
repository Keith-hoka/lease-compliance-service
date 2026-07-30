from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import TenantDep
from app.core.db import get_session
from app.core.ratelimit import enforce_rate_limit
from app.core.usage import record_usage
from app.services.legislation import section_at

router = APIRouter(prefix="/v1", dependencies=[Depends(enforce_rate_limit)])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class SectionInfo(BaseModel):
    section_no: str
    heading: str
    body_text: str
    part: str | None
    division: str | None
    valid_from: date
    valid_to: date | None


@router.get("/legislation/sections", response_model=SectionInfo)
async def get_section(
    act: str, section_no: str, as_at: date, tenant: TenantDep, session: SessionDep
) -> SectionInfo:
    section = await section_at(session, act, section_no, as_at)
    if section is None:
        raise HTTPException(status_code=404, detail="Section not in force at that date")
    await record_usage(session, tenant.tenant_id, "legislation")
    await session.commit()
    return SectionInfo(
        section_no=section.section_no,
        heading=section.heading,
        body_text=section.body_text,
        part=section.part,
        division=section.division,
        valid_from=section.valid_from,
        valid_to=section.valid_to,
    )
