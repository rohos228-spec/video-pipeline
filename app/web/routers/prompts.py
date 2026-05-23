"""REST: /api/prompts."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MasterPrompt
from app.web.deps import get_session
from app.web.schemas import PromptDTO

router = APIRouter(prefix="/prompts", tags=["prompts"])


@router.get("", response_model=list[PromptDTO])
async def list_prompts(
    session: AsyncSession = Depends(get_session),
) -> list[MasterPrompt]:
    rows = (
        await session.execute(
            select(MasterPrompt).order_by(MasterPrompt.key, MasterPrompt.version.desc())
        )
    ).scalars().all()
    return list(rows)


@router.get("/{prompt_id}", response_model=PromptDTO)
async def get_prompt(
    prompt_id: int, session: AsyncSession = Depends(get_session)
) -> MasterPrompt:
    p = await session.get(MasterPrompt, prompt_id)
    if p is None:
        raise HTTPException(status_code=404, detail="prompt not found")
    return p


@router.patch("/{prompt_id}", response_model=PromptDTO)
async def patch_prompt(
    prompt_id: int,
    payload: dict,
    session: AsyncSession = Depends(get_session),
) -> MasterPrompt:
    p = await session.get(MasterPrompt, prompt_id)
    if p is None:
        raise HTTPException(status_code=404, detail="prompt not found")
    if "text" in payload:
        p.text = payload["text"]
    if "active" in payload:
        p.active = bool(payload["active"])
    await session.commit()
    await session.refresh(p)
    return p
