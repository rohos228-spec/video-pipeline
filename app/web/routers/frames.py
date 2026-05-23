"""REST: /api/projects/{id}/frames."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame
from app.web.deps import get_session
from app.web.schemas import FrameDTO, UpdateFrameRequest

router = APIRouter(prefix="/projects/{project_id}/frames", tags=["frames"])


@router.get("", response_model=list[FrameDTO])
async def list_frames(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> list[Frame]:
    rows = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number.asc())
        )
    ).scalars().all()
    return list(rows)


@router.get("/{frame_id}", response_model=FrameDTO)
async def get_frame(
    project_id: int, frame_id: int, session: AsyncSession = Depends(get_session)
) -> Frame:
    f = await session.get(Frame, frame_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="frame not found")
    return f


@router.patch("/{frame_id}", response_model=FrameDTO)
async def patch_frame(
    project_id: int,
    frame_id: int,
    payload: UpdateFrameRequest,
    session: AsyncSession = Depends(get_session),
) -> Frame:
    f = await session.get(Frame, frame_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="frame not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(f, k, v)
    await session.commit()
    await session.refresh(f)
    return f
