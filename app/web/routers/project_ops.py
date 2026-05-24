"""Операции проекта для веб-студии: xlsx, ассеты, пауза, сброс шага."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Project, ProjectStatus
from app.services.event_bus import publish_project_event
from app.services.project_control import pause_project as pause_project_svc
from app.services.project_control import resume_project as resume_project_svc
from app.services.project_control import stop_project_running
from app.services.reset_step import reset_step
from app.services.run_sync import ensure_run_for_project, _get_default_workflow_id
from app.services.xlsx_sync import reload_from_xlsx
from app.settings import settings
from app.storage import ProjectSheet
from app.web.deps import get_session
from app.web.project_dto import project_to_detail
from app.web.schemas import ProjectDetail

router = APIRouter(prefix="/projects", tags=["project-ops"])


def _project_or_404(project: Project | None) -> Project:
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return project


@router.post("/{project_id}/pause", response_model=ProjectDetail)
async def pause_project(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> Project:
    p = _project_or_404(await session.get(Project, project_id))
    await pause_project_svc(session, p)
    await session.commit()
    await session.refresh(p)
    await publish_project_event(project_id, event_type="project_updated", payload={"paused": True})
    return p


@router.post("/{project_id}/resume", response_model=ProjectDetail)
async def resume_project(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> Project:
    p = _project_or_404(await session.get(Project, project_id))
    await resume_project_svc(session, p)
    await session.commit()
    await session.refresh(p)
    await publish_project_event(project_id, event_type="project_updated", payload={"resumed": True})
    return p


@router.post("/{project_id}/stop")
async def stop_project(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> dict:
    p = _project_or_404(await session.get(Project, project_id))
    info = await stop_project_running(session, p)
    if not info["ok"]:
        raise HTTPException(status_code=400, detail=info["message"])
    await session.commit()
    await session.refresh(p)
    await publish_project_event(
        project_id,
        event_type="project_updated",
        payload={"stopped": True, "message": info["message"]},
    )
    return {
        "project": project_to_detail(p),
        "message": info["message"],
        "generation_still_active": info["generation_still_active"],
        "xlsx_stopped": info["xlsx_stopped"],
    }


@router.post("/{project_id}/mass-lanes/start")
async def start_mass_lanes(
    project_id: int,
    payload: dict,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Массовая генерация: N проектов-копий с auto_mode (как массовый батч в TG)."""
    from app.web.routers.projects import _slugify

    template = _project_or_404(await session.get(Project, project_id))
    topics: list[str] = [str(t).strip() for t in (payload.get("topics") or []) if str(t).strip()]
    count = int(payload.get("count") or len(topics) or 1)
    if not topics:
        base = template.topic or "ролик"
        topics = [f"{base} — поток {i + 1}" for i in range(count)]
    copy_fields = (
        "hero_mode",
        "image_generator",
        "aspect_ratio",
        "image_resolution",
        "image_relax",
        "video_generator",
        "video_resolution",
        "video_relax",
        "hero_count",
        "hero_descriptions",
        "hero_variations",
        "hero_variation_modifiers",
        "item_descriptions",
        "item_variations",
        "enrich_slots_count",
        "prompt_overrides",
        "gpt_text_overrides",
    )
    meta_template = dict(template.meta or {})
    wf_id = await _get_default_workflow_id()
    created: list[dict] = []
    for i, topic in enumerate(topics):
        slug_base = _slugify(topic)
        slug = slug_base
        suffix = 2
        while (await session.execute(select(Project).where(Project.slug == slug))).scalar_one_or_none():
            slug = f"{slug_base}-{suffix}"
            suffix += 1
        kwargs = {f: getattr(template, f) for f in copy_fields}
        p = Project(
            slug=slug,
            topic=topic,
            status=ProjectStatus.new,
            auto_mode=True,
            meta={
                **meta_template,
                "graph_executor": True,
                "mass_lane": i + 1,
                "mass_parent_id": project_id,
            },
            **kwargs,
        )
        session.add(p)
        await session.flush()
        if wf_id:
            await ensure_run_for_project(p.id, wf_id)
        created.append({"id": p.id, "topic": p.topic, "slug": p.slug})
    await session.commit()
    return {"created": created, "count": len(created)}


@router.post("/{project_id}/steps/{step_code}/reset", response_model=ProjectDetail)
async def reset_project_step(
    project_id: int,
    step_code: str,
    session: AsyncSession = Depends(get_session),
) -> Project:
    p = _project_or_404(await session.get(Project, project_id))
    try:
        summary = await reset_step(session, p, step_code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if summary.get("error"):
        raise HTTPException(status_code=400, detail=str(summary["error"]))
    await session.commit()
    await session.refresh(p)
    await publish_project_event(
        project_id,
        event_type="project_updated",
        payload={"reset_step": step_code},
    )
    return p


@router.get("/{project_id}/xlsx")
async def download_xlsx(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> FileResponse:
    p = _project_or_404(await session.get(Project, project_id))
    xlsx = p.data_dir / "project.xlsx"
    if not xlsx.exists():
        sheet = ProjectSheet(file_path=xlsx)
        sheet.ensure_initialized(project_id=p.id, slug=p.slug)
    if not xlsx.exists():
        raise HTTPException(status_code=404, detail="project.xlsx not found")
    return FileResponse(
        path=str(xlsx),
        filename=f"{p.slug}-project.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@router.post("/{project_id}/xlsx/reload", response_model=ProjectDetail)
async def reload_xlsx(
    project_id: int, session: AsyncSession = Depends(get_session)
) -> Project:
    p = _project_or_404(await session.get(Project, project_id))
    xlsx = p.data_dir / "project.xlsx"
    if not xlsx.exists():
        raise HTTPException(status_code=404, detail="project.xlsx not found")
    await reload_from_xlsx(session, p, xlsx)
    await session.commit()
    await session.refresh(p)
    await publish_project_event(project_id, event_type="project_updated", payload={"xlsx": "reloaded"})
    return p


@router.post("/{project_id}/xlsx/upload", response_model=ProjectDetail)
async def upload_xlsx(
    project_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> Project:
    p = _project_or_404(await session.get(Project, project_id))
    if not file.filename or not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="need .xlsx file")
    dest = p.data_dir / "project.xlsx"
    dest.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    dest.write_bytes(content)
    await reload_from_xlsx(session, p, dest)
    await session.commit()
    await session.refresh(p)
    await publish_project_event(project_id, event_type="project_updated", payload={"xlsx": "uploaded"})
    return p


@router.get("/{project_id}/xlsx/preview")
async def preview_xlsx(
    project_id: int,
    sheet: str | None = Query(None),
    max_rows: int = Query(40, ge=1, le=500),
    max_cols: int = Query(80, ge=1, le=200),
    row: int | None = Query(None, ge=1, le=500),
    raw: bool = Query(False),
    session: AsyncSession = Depends(get_session),
) -> dict:
    p = _project_or_404(await session.get(Project, project_id))
    xlsx = p.data_dir / "project.xlsx"
    if not xlsx.exists():
        return {
            "path": str(xlsx),
            "sheets": [],
            "active_sheet": "",
            "headers": [],
            "rows": [],
            "cells": [],
        }
    from openpyxl import load_workbook

    wb = load_workbook(xlsx, read_only=True, data_only=True)
    sheets = wb.sheetnames
    active = sheet if sheet in sheets else (sheets[0] if sheets else "")

    if row is not None and active:
        ws = wb[active]
        cells: list[str] = []
        for col in range(1, min(ws.max_column, max_cols) + 1):
            v = ws.cell(row=row, column=col).value
            cells.append("" if v is None else str(v))
        wb.close()
        return {
            "path": str(xlsx),
            "sheets": sheets,
            "active_sheet": active,
            "row": row,
            "cells": cells,
        }

    headers: list[str] = []
    rows: list[list[str]] = []
    if active:
        ws = wb[active]
        limit_cols = min(ws.max_column or 1, max_cols)
        for i, row_vals in enumerate(ws.iter_rows(values_only=True)):
            cells = [
                "" if c is None else str(c)
                for c in (list(row_vals) + [""] * limit_cols)[:limit_cols]
            ]
            if raw:
                rows.append(cells)
            elif i == 0:
                headers = cells
                continue
            else:
                rows.append(cells)
            if len(rows) >= max_rows:
                break
    wb.close()
    return {
        "path": str(xlsx),
        "sheets": sheets,
        "active_sheet": active,
        "headers": headers if not raw else [],
        "rows": rows,
    }


@router.get("/{project_id}/assets")
async def list_project_assets(
    project_id: int,
    kind: str = Query("all", pattern="^(all|hero|items|images|videos|audio|final|text)$"),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    p = _project_or_404(await session.get(Project, project_id))
    out: list[dict] = []

    # DB artifacts
    if kind in ("all", "hero", "items", "images", "videos", "audio", "final"):
        from sqlalchemy import select

        arts = (
            await session.execute(select(Artifact).where(Artifact.project_id == project_id))
        ).scalars().all()
        kind_map = {
            "hero": {ArtifactKind.hero_reference},
            "items": {ArtifactKind.item_reference},
            "images": {ArtifactKind.scene_image},
            "videos": {ArtifactKind.scene_video},
            "audio": {ArtifactKind.audio, ArtifactKind.subtitle},
            "final": {ArtifactKind.final_video},
        }
        for a in arts:
            if kind != "all" and a.kind not in kind_map.get(kind, set()):
                continue
            rel = _rel_path(a.path) if a.path else None
            out.append(
                {
                    "source": "artifact",
                    "id": a.uuid,
                    "kind": a.kind.value if hasattr(a.kind, "value") else str(a.kind),
                    "path": a.path,
                    "preview_url": f"/api/artifacts/{a.uuid}/file" if a.uuid else None,
                    "frame_id": a.frame_id,
                    "meta": a.meta or {},
                }
            )

    # Disk scan (fallback)
    base = p.data_dir
    subdirs = {
        "hero": ["characters", "hero"],
        "items": ["items", "objects"],
        "images": ["scenes", "images"],
        "videos": ["videos", "clips"],
        "audio": ["audio", "subs"],
        "final": ["final"],
        "text": [],
    }
    if kind in ("all", "text"):
        for name in ("voiceover.txt", "script.txt", "general_plan.txt"):
            fp = base / name
            if fp.exists():
                out.append(
                    {
                        "source": "file",
                        "id": name,
                        "kind": "text",
                        "path": str(fp),
                        "preview_url": f"/api/files?path={fp}",
                        "label": name,
                    }
                )
        plan = base / "project.xlsx"
        if plan.exists():
            out.append(
                {
                    "source": "file",
                    "id": "project.xlsx",
                    "kind": "xlsx",
                    "path": str(plan),
                    "preview_url": None,
                    "label": "Таблица проекта",
                }
            )

    scan_kind = kind if kind in subdirs else None
    if scan_kind or kind == "all":
        keys = [scan_kind] if scan_kind else list(subdirs.keys())
        for k in keys:
            for sub in subdirs.get(k, []):
                d = base / sub
                if not d.is_dir():
                    continue
                for fp in sorted(d.rglob("*")):
                    if not fp.is_file():
                        continue
                    if fp.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".wav", ".mp3"}:
                        continue
                    rel = _rel_path(str(fp))
                    out.append(
                        {
                            "source": "file",
                            "id": rel,
                            "kind": k,
                            "path": str(fp),
                            "preview_url": f"/api/files?path={fp}",
                            "label": fp.name,
                        }
                    )
    return out


@router.post("/{project_id}/assets/hero/replace")
async def replace_hero_image(
    project_id: int,
    file: UploadFile = File(...),
    replace_path: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Заменить reference-картинку персонажа (файл в data/.../characters/)."""
    p = _project_or_404(await session.get(Project, project_id))
    if not file.filename:
        raise HTTPException(status_code=400, detail="empty filename")
    ext = Path(file.filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise HTTPException(status_code=400, detail="need image file (.png, .jpg, .webp)")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="empty file")

    chars_dir = p.data_dir / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)

    dest: Path
    if replace_path:
        candidate = Path(replace_path)
        if not candidate.is_absolute():
            candidate = Path(settings.data_dir) / replace_path
        try:
            candidate.resolve().relative_to(p.data_dir.resolve())
        except ValueError as e:
            raise HTTPException(status_code=400, detail="replace_path outside project") from e
        dest = candidate
        dest.parent.mkdir(parents=True, exist_ok=True)
    else:
        stem = Path(file.filename).stem or "hero"
        dest = chars_dir / f"{stem}{ext}"

    dest.write_bytes(content)
    await publish_project_event(
        project_id,
        event_type="project_updated",
        payload={"hero_replaced": str(dest.name)},
    )
    rel = _rel_path(str(dest))
    return {
        "path": str(dest),
        "preview_url": f"/api/files?path={dest}",
        "id": rel,
    }


def _rel_path(path: str | None) -> str:
    if not path:
        return ""
    try:
        p = Path(path)
        data = Path(settings.data_dir).resolve()
        return str(p.resolve().relative_to(data))
    except Exception:
        return path
