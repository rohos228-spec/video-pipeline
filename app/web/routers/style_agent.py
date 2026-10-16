"""REST ноды «Разбор стиля» (style_extract) и «Проверка стиля» (hitl_style).

Все пути под /projects/{id}/style-agent. Состояние — meta.style_agent +
файлы в <project>/style/. LLM вызывается только в фоновом job (run).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db import commit_with_retry
from app.models import Project
from app.services.event_bus import publish_project_event
from app.services.style_agent import job as sa_job
from app.services.style_agent import schema as sa_schema
from app.web.deps import get_project_session

router = APIRouter(prefix="/projects", tags=["style-agent"])

ProjectSession = Annotated[AsyncSession, Depends(get_project_session)]
JsonBody = Annotated[dict[str, Any], Body()]

MAX_UPLOAD_BYTES = 600 * 1024 * 1024


def _project_or_404(project: Project | None) -> Project:
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


async def _commit(session: AsyncSession, project: Project, *, event: str | None = None) -> None:
    flag_modified(project, "meta")
    project.updated_at = datetime.utcnow()
    await commit_with_retry(session)
    if event:
        await publish_project_event(project.id, event_type="project_updated", payload={"style_agent": True, "status": event})


@router.get("/{project_id}/style-agent")
async def get_style_agent(project_id: int, session: ProjectSession, node_key: str | None = Query(None)) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    return sa_job.snapshot(project, node_key)


@router.patch("/{project_id}/style-agent")
async def patch_style_agent_settings(project_id: int, session: ProjectSession, payload: JsonBody) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    patch = sa_job.normalize_settings(payload if isinstance(payload, dict) else {})
    if not patch:
        raise HTTPException(status_code=400, detail="нет допустимых настроек")
    sa_job.set_meta(project, {"settings": {**sa_job.get_settings(project), **patch}})
    await _commit(session, project)
    return {"settings": sa_job.get_settings(project)}


@router.post("/{project_id}/style-agent/estimate")
async def estimate_style_agent(
    project_id: int, session: ProjectSession, payload: JsonBody | None = None, node_key: str | None = Query(None)
) -> dict[str, Any]:
    """План LLM-вызовов и оценка цены до запуска (без обращения к модели)."""
    project = _project_or_404(await session.get(Project, project_id))
    return sa_job.estimate(project, node_key, payload if isinstance(payload, dict) else None)


@router.post("/{project_id}/style-agent/run")
async def run_style_agent(
    project_id: int, session: ProjectSession, payload: JsonBody | None = None, node_key: str | None = Query(None)
) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    if sa_job.is_running(project.id):
        raise HTTPException(status_code=409, detail="разбор уже идёт")
    overrides = sa_job.normalize_settings(payload if isinstance(payload, dict) else {})
    if not sa_job.list_ref_videos(project, node_key):
        raise HTTPException(status_code=400, detail="нет роликов: загрузите mp4 в ноду или подключите «Файлы»")
    if overrides:
        sa_job.set_meta(project, {"settings": {**sa_job.get_settings(project), **overrides}})
        await _commit(session, project)
    est = sa_job.estimate(project, node_key)
    sa_job.spawn_style_extract_job(project.id, node_key=node_key, overrides=overrides)
    return {"started": True, "estimate": est}


@router.post("/{project_id}/style-agent/stop")
async def stop_style_agent(project_id: int, session: ProjectSession) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    return {"stopped": sa_job.stop_job(project.id)}


@router.post("/{project_id}/style-agent/upload")
async def upload_style_ref(
    project_id: int,
    session: ProjectSession,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="пустой файл")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="файл больше 600 МБ")
    try:
        dest = sa_job.save_ref_upload(project, file.filename or "video.mp4", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await publish_project_event(project.id, event_type="project_updated", payload={"style_agent": True, "status": "uploaded"})
    return {"name": dest.name, "size": len(data), "videos": [p.name for p in sa_job.list_ref_videos(project)]}


@router.delete("/{project_id}/style-agent/refs/{name}")
async def delete_style_ref(project_id: int, name: str, session: ProjectSession) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    if not sa_job.remove_ref(project, name):
        raise HTTPException(status_code=404, detail="файл не найден")
    return {"videos": [p.name for p in sa_job.list_ref_videos(project)]}


@router.get("/{project_id}/style-agent/passport")
async def get_style_passport(project_id: int, session: ProjectSession, approved: bool = Query(False)) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    passport = sa_job.read_passport(project, approved=approved)
    if passport is None:
        raise HTTPException(status_code=404, detail="паспорт ещё не создан")
    return {"passport": passport, "problems": sa_schema.validate_passport(passport)}


@router.put("/{project_id}/style-agent/passport")
async def put_style_passport(project_id: int, session: ProjectSession, payload: JsonBody) -> dict[str, Any]:
    """Сохранить отредактированный черновик (hitl_style). Не материализует."""
    project = _project_or_404(await session.get(Project, project_id))
    passport = payload.get("passport") if isinstance(payload, dict) and "passport" in payload else payload
    if not isinstance(passport, dict):
        raise HTTPException(status_code=400, detail="passport object required")
    problems = sa_schema.validate_passport(passport)
    if problems:
        raise HTTPException(status_code=400, detail={"problems": problems})
    sa_job.write_passport(project, passport, approved=False)
    sa_job.set_meta(project, {"passport": {"draftAt": passport.get("created_at"), "style_id": passport.get("style_id"), "approvedAt": None}})
    await _commit(session, project, event="draft_saved")
    return {"ok": True, "problems": []}


@router.post("/{project_id}/style-agent/approve")
async def approve_style_passport(project_id: int, session: ProjectSession, payload: JsonBody | None = None) -> dict[str, Any]:
    """Утвердить паспорт → style_passport.json → блоки/пресет/style lock + патчи проекта."""
    project = _project_or_404(await session.get(Project, project_id))
    body = payload if isinstance(payload, dict) else {}
    passport = body.get("passport") if isinstance(body.get("passport"), dict) else None
    materialize = bool(body.get("materialize", True))
    try:
        report = sa_job.approve(project, passport, materialize=materialize)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    flag_modified(project, "prompt_overrides")
    await _commit(session, project, event="approved")
    return {"report": report, "snapshot": sa_job.snapshot(project)}


@router.get("/{project_id}/style-agent/report")
async def get_style_report(project_id: int, session: ProjectSession) -> dict[str, Any]:
    project = _project_or_404(await session.get(Project, project_id))
    rp = sa_job.report_path(project)
    ap = sa_job.style_dir(project) / "apply_report.json"
    apply_report: Any = None
    if ap.is_file():
        import json

        try:
            apply_report = json.loads(ap.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            apply_report = None
    return {"variance_report": rp.read_text(encoding="utf-8") if rp.is_file() else "", "apply_report": apply_report}


@router.get("/{project_id}/style-agent/sheet")
async def get_style_sheet(project_id: int, session: ProjectSession, video: str = Query(...), name: str = Query(...)) -> FileResponse:
    """Лист кадров для предпросмотра в панели ноды."""
    project = _project_or_404(await session.get(Project, project_id))
    base = (sa_job.analysis_dir(project) / sa_job.safe_video_name(video)).resolve()
    stem = base.with_suffix("") if base.suffix else base
    fp = (stem / "sheets" / name).resolve()
    if not str(fp).startswith(str(sa_job.analysis_dir(project).resolve())) or not fp.is_file():
        raise HTTPException(status_code=404, detail="лист не найден")
    return FileResponse(fp)
