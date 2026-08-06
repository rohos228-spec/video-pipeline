"""Read-tools агента: наблюдение за пайплайном (risk=read).

Импорт этого модуля регистрирует tools в registry (декоратор @tool).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    HITLRequest,
    NodeRun,
    Project,
    WorkflowRun,
)
from app.services.agent.registry import tool


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@tool(
    name="list_projects",
    description="Список проектов пайплайна (id, статус, auto_mode, обновление).",
    parameters={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "default": 20},
            "status": {"type": "string", "description": "фильтр по статусу, напр. generating_videos"},
        },
    },
)
async def _list_projects(args: dict[str, Any]) -> str:
    limit = max(1, min(int(args.get("limit") or 20), 100))
    status = (args.get("status") or "").strip()
    async with SessionLocal() as s:
        q = select(Project).order_by(Project.updated_at.desc()).limit(limit)
        if status:
            q = select(Project).where(Project.status == status).order_by(
                Project.updated_at.desc()
            ).limit(limit)
        rows = (await s.execute(q)).scalars().all()
        return _json(
            [
                {
                    "id": p.id,
                    "slug": p.slug,
                    "topic": (p.topic or "")[:80],
                    "status": p.status.value,
                    "auto_mode": p.auto_mode,
                    "updated_at": str(p.updated_at),
                }
                for p in rows
            ]
        )


@tool(
    name="get_project",
    description="Карточка проекта: статус, счётчики кадров/медиа, step_failure, флаги остановки.",
    parameters={
        "type": "object",
        "properties": {"project_id": {"type": "integer"}},
        "required": ["project_id"],
    },
)
async def _get_project(args: dict[str, Any]) -> str:
    pid = int(args["project_id"])
    async with SessionLocal() as s:
        p = await s.get(Project, pid)
        if p is None:
            return _json({"error": f"project {pid} not found"})
        frames = (
            await s.execute(
                select(Frame.status, func.count())
                .where(Frame.project_id == pid)
                .group_by(Frame.status)
            )
        ).all()
        frame_counts = {st.value: n for st, n in frames}
        videos = (
            await s.execute(
                select(func.count())
                .select_from(Artifact)
                .where(
                    Artifact.project_id == pid,
                    Artifact.kind == ArtifactKind.scene_video,
                )
            )
        ).scalar() or 0
        images = (
            await s.execute(
                select(func.count())
                .select_from(Artifact)
                .where(
                    Artifact.project_id == pid,
                    Artifact.kind == ArtifactKind.scene_image,
                )
            )
        ).scalar() or 0
        meta = p.meta if isinstance(p.meta, dict) else {}
        return _json(
            {
                "id": p.id,
                "slug": p.slug,
                "topic": (p.topic or "")[:120],
                "status": p.status.value,
                "auto_mode": p.auto_mode,
                "frame_counts": frame_counts,
                "artifacts": {"scene_image": images, "scene_video": videos},
                "step_failure": meta.get("step_failure"),
                "user_stop": bool(meta.get("user_stop")),
                "gen_queue_run": meta.get("gen_queue_run"),
                "streams": {
                    "outsee": meta.get("outsee_streams") or meta.get("img_streams"),
                    "check": meta.get("check_streams"),
                },
            }
        )


@tool(
    name="get_run_nodes",
    description="Статусы нод последнего WorkflowRun проекта (что running/failed/done, тексты ошибок).",
    parameters={
        "type": "object",
        "properties": {"project_id": {"type": "integer"}},
        "required": ["project_id"],
    },
)
async def _get_run_nodes(args: dict[str, Any]) -> str:
    pid = int(args["project_id"])
    async with SessionLocal() as s:
        wr = (
            await s.execute(
                select(WorkflowRun)
                .where(WorkflowRun.project_id == pid)
                .order_by(WorkflowRun.id.desc())
                .limit(1)
            )
        ).scalars().first()
        if wr is None:
            return _json({"error": f"no workflow run for project {pid}"})
        nodes = (
            await s.execute(
                select(NodeRun)
                .where(NodeRun.workflow_run_id == wr.id)
                .order_by(NodeRun.id)
            )
        ).scalars().all()
        return _json(
            {
                "run_id": wr.id,
                "run_status": wr.status.value,
                "nodes": [
                    {
                        "node_key": n.node_key,
                        "status": n.status.value,
                        "error": (n.error or "")[:200] or None,
                        "updated_at": str(n.updated_at),
                    }
                    for n in nodes
                    if n.status.value != "pending"
                ],
            }
        )


@tool(
    name="tail_log",
    description="Хвост живого лога бэкенда (studio-live.log). Фильтр по подстроке, напр. '#53' или 'ERROR'.",
    parameters={
        "type": "object",
        "properties": {
            "lines": {"type": "integer", "default": 40},
            "grep": {"type": "string", "description": "подстрока фильтра (необязательно)"},
        },
    },
)
async def _tail_log(args: dict[str, Any]) -> str:
    from app.settings import settings

    n = max(1, min(int(args.get("lines") or 40), 300))
    needle = (args.get("grep") or "").strip()
    log_path = settings.data_dir / "studio-live.log"
    if not log_path.is_file():
        return _json({"error": f"log not found: {log_path}"})
    raw = log_path.read_bytes()[-1_500_000:].decode("utf-8", "replace")
    lines = [re.sub(r"\x1b\[[0-9;]*m", "", ln) for ln in raw.splitlines()]
    if needle:
        lines = [ln for ln in lines if needle in ln]
    return _json({"log": log_path.name, "lines": lines[-n:]})


@tool(
    name="get_runtime_streams",
    description="Глобальные потоки: worker_max_parallel, дефолтные outsee/check streams, занятость воркера.",
    parameters={"type": "object", "properties": {}},
)
async def _get_runtime_streams(args: dict[str, Any]) -> str:
    from app.services import runtime_streams as rs

    cfg = rs.load_runtime_streams()
    busy = 0
    try:
        from app.services.gen_queue import gen_queue_busy_count

        async with SessionLocal() as s:
            busy = await gen_queue_busy_count(s)
    except Exception:  # noqa: BLE001
        pass
    return _json({**cfg, "worker_busy": busy})


@tool(
    name="hitl_pending",
    description="Ожидающие решения HITL (approve/regen/reject) — по проекту или все.",
    parameters={
        "type": "object",
        "properties": {"project_id": {"type": "integer"}},
    },
)
async def _hitl_pending(args: dict[str, Any]) -> str:
    pid = args.get("project_id")
    async with SessionLocal() as s:
        q = select(HITLRequest).where(HITLRequest.status == "pending")
        if pid is not None:
            q = q.where(HITLRequest.project_id == int(pid))
        rows = (await s.execute(q.order_by(HITLRequest.id.desc()).limit(30))).scalars().all()
        return _json(
            [
                {
                    "id": h.id,
                    "project_id": h.project_id,
                    "kind": h.kind.value if hasattr(h.kind, "value") else str(h.kind),
                    "title": h.title,
                    "created_at": str(h.created_at),
                }
                for h in rows
            ]
        )


@tool(
    name="knowledge_search",
    description="Поиск по базе знаний репо (docs, промпты, код): как устроен шаг X, где код Y.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "limit": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
)
async def _knowledge_search(args: dict[str, Any]) -> str:
    from app.services import knowledge_index

    query = str(args.get("query") or "")
    limit = max(1, min(int(args.get("limit") or 5), 20))
    hits = knowledge_index.search_knowledge(query, limit=limit)
    return _json(
        [
            {
                "path": h.get("path"),
                "title": h.get("title"),
                "score": h.get("score"),
                "excerpt": (h.get("excerpt") or "")[:300],
            }
            for h in hits
        ]
    )


@tool(
    name="read_file_excerpt",
    description="Прочитать фрагмент файла репо (для изучения кода/доков). Path относительно корня репо.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "default": 1},
            "limit": {"type": "integer", "default": 120},
        },
        "required": ["path"],
    },
)
async def _read_file_excerpt(args: dict[str, Any]) -> str:
    from app.settings import settings

    rel = str(args.get("path") or "").strip().lstrip("/\\")
    root = Path(settings.data_dir).parent.resolve()
    target = (root / rel).resolve()
    if root not in target.parents and target != root:
        return _json({"error": "path escapes repo root"})
    if not target.is_file():
        return _json({"error": f"not a file: {rel}"})
    offset = max(1, int(args.get("offset") or 1))
    limit = max(1, min(int(args.get("limit") or 120), 400))
    try:
        lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as e:  # noqa: BLE001
        return _json({"error": f"read failed: {e}"})
    chunk = lines[offset - 1 : offset - 1 + limit]
    return _json(
        {
            "path": rel,
            "total_lines": len(lines),
            "offset": offset,
            "lines": [f"{offset + i}| {ln}" for i, ln in enumerate(chunk)],
        }
    )


@tool(
    name="get_frame_video_failures",
    description="Кадры проекта со счётчиком ошибок видео (video_gen_fail_count/skip в attrs).",
    parameters={
        "type": "object",
        "properties": {"project_id": {"type": "integer"}},
        "required": ["project_id"],
    },
)
async def _get_frame_video_failures(args: dict[str, Any]) -> str:
    pid = int(args["project_id"])
    async with SessionLocal() as s:
        frames = (
            await s.execute(
                select(Frame).where(Frame.project_id == pid).order_by(Frame.number)
            )
        ).scalars().all()
        bad = []
        for f in frames:
            attrs = f.attrs if isinstance(f.attrs, dict) else {}
            fails = attrs.get("video_gen_fail_count")
            skip = attrs.get("video_gen_skip")
            if fails or skip:
                bad.append(
                    {
                        "frame": f.number,
                        "status": f.status.value,
                        "fail_count": fails,
                        "skipped": bool(skip),
                        "skip_reason": skip,
                    }
                )
        done = sum(
            1
            for f in frames
            if f.status
            in (FrameStatus.video_generated, FrameStatus.video_approved, FrameStatus.done)
        )
        return _json({"total_frames": len(frames), "video_done": done, "failures": bad})
