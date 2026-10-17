"""REST: /api/projects/{id}/frames."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import commit_with_retry
from app.models import Frame, FrameStatus
from app.project_db import register_frame_project
from app.web.deps import get_project_session
from app.web.schemas import FrameDTO, UpdateFrameRequest

router = APIRouter(prefix="/projects/{project_id}/frames", tags=["frames"])


@router.get("", response_model=list[FrameDTO])
async def list_frames(
    project_id: int, session: AsyncSession = Depends(get_project_session)
) -> list[Frame]:
    rows = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number.asc())
        )
    ).scalars().all()
    for fr in rows:
        register_frame_project(fr.id, project_id)
    return list(rows)


@router.get("/{frame_id}", response_model=FrameDTO)
async def get_frame(
    project_id: int, frame_id: int, session: AsyncSession = Depends(get_project_session)
) -> Frame:
    f = await session.get(Frame, frame_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="frame not found")
    register_frame_project(f.id, project_id)
    return f


@router.patch("/{frame_id}", response_model=FrameDTO)
async def patch_frame(
    project_id: int,
    frame_id: int,
    payload: UpdateFrameRequest,
    session: AsyncSession = Depends(get_project_session),
) -> Frame:
    f = await session.get(Frame, frame_id)
    if f is None or f.project_id != project_id:
        raise HTTPException(status_code=404, detail="frame not found")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        try:
            data["status"] = FrameStatus(data["status"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"invalid frame status: {data['status']}") from e
    for k, v in data.items():
        setattr(f, k, v)
    await commit_with_retry(session)
    await session.refresh(f)
    register_frame_project(f.id, project_id)
    return f


@router.post("/{frame_id}/regenerate-video")
async def regenerate_frame_video(
    project_id: int,
    frame_id: int,
    session: AsyncSession = Depends(get_project_session),
) -> dict:
    import time
    from pathlib import Path
    from sqlalchemy import delete
    from app.models import Artifact, ArtifactKind, Project

    fr = await session.get(Frame, frame_id)
    if fr is None or fr.project_id != project_id:
        raise HTTPException(status_code=404, detail="frame not found")

    project = await session.get(Project, project_id)
    if project is not None and getattr(project, "data_dir", None):
        videos_dir = Path(project.data_dir) / "videos"
        if videos_dir.is_dir():
            old_dir = videos_dir / "old"
            old_dir.mkdir(parents=True, exist_ok=True)
            for p in videos_dir.glob(f"clip_{fr.number:03d}_*.mp4"):
                target = old_dir / f"{p.stem}_{int(time.time())}{p.suffix}"
                try:
                    p.rename(target)
                except Exception:
                    pass

    await session.execute(
        delete(Artifact).where(
            Artifact.project_id == project_id,
            Artifact.frame_id == frame_id,
            Artifact.kind == ArtifactKind.scene_video,
        )
    )

    attrs = dict(fr.attrs or {})
    attrs.pop("video_gen_skip", None)
    attrs.pop("video_inflight", None)
    attrs.pop("video_gen_fail_count", None)
    fr.attrs = attrs
    fr.status = FrameStatus.ready
    await commit_with_retry(session)
    register_frame_project(fr.id, project_id)
    return {"ok": True, "frame_id": frame_id, "frame_number": fr.number}

