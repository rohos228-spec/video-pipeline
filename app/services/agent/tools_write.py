"""Write/danger-tools агента: управление пайплайном (тонкие обёртки сервисов).

Импорт модуля регистрирует tools в registry.
"""

from __future__ import annotations

import json
from typing import Any

from app.db import SessionLocal
from app.models import Project
from app.services.agent.registry import tool


def _json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@tool(
    name="run_step",
    description=(
        "Запустить шаг пайплайна для проекта (как кнопка ▶ в Studio). "
        "Коды шагов: plan, script, split, excel_gpt, hero, img_pr, img, "
        "anim_pr, video, videos_check, montage. Сначала проверь get_project."
    ),
    parameters={
        "type": "object",
        "properties": {
            "project_id": {"type": "integer"},
            "step_code": {"type": "string"},
        },
        "required": ["project_id", "step_code"],
    },
    risk="write",
)
async def _run_step(args: dict[str, Any]) -> str:
    from app.services.project_steps import list_step_codes, start_step

    pid = int(args["project_id"])
    code = str(args["step_code"]).strip()
    known = {s["code"] for s in list_step_codes()} | {"excel_gpt"}
    if code not in known:
        return _json({"error": f"unknown step_code {code!r}", "known": sorted(known)})
    async with SessionLocal() as s:
        project = await s.get(Project, pid)
        if project is None:
            return _json({"error": f"project {pid} not found"})
        before = project.status.value
        new_status = await start_step(
            s, project, code, explicit_ui_start=True
        )
        await s.commit()
        return _json(
            {"ok": True, "project_id": pid, "step": code, "before": before,
             "after": new_status.value}
        )


@tool(
    name="pause_project",
    description="Поставить проект на паузу (остановить текущий шаг, user_stop).",
    parameters={
        "type": "object",
        "properties": {"project_id": {"type": "integer"}},
        "required": ["project_id"],
    },
    risk="write",
)
async def _pause_project(args: dict[str, Any]) -> str:
    from app.services.project_control import pause_project

    pid = int(args["project_id"])
    async with SessionLocal() as s:
        project = await s.get(Project, pid)
        if project is None:
            return _json({"error": f"project {pid} not found"})
        before = project.status.value
        await pause_project(s, project)
        await s.commit()
        return _json({"ok": True, "project_id": pid, "before": before,
                      "after": project.status.value})


@tool(
    name="resume_project",
    description="Снять проект с паузы (вернуть в очередь генерации).",
    parameters={
        "type": "object",
        "properties": {"project_id": {"type": "integer"}},
        "required": ["project_id"],
    },
    risk="write",
)
async def _resume_project(args: dict[str, Any]) -> str:
    from app.services.project_control import resume_project

    pid = int(args["project_id"])
    async with SessionLocal() as s:
        project = await s.get(Project, pid)
        if project is None:
            return _json({"error": f"project {pid} not found"})
        before = project.status.value
        result_status = await resume_project(s, project)
        await s.commit()
        return _json({"ok": True, "project_id": pid, "before": before,
                      "after": result_status})


@tool(
    name="set_project_streams",
    description="Потоки проекта: outsee (0-4, генерация img/video) и check (0-10, vision-проверки).",
    parameters={
        "type": "object",
        "properties": {
            "project_id": {"type": "integer"},
            "outsee_streams": {"type": "integer", "minimum": 0, "maximum": 4},
            "check_streams": {"type": "integer", "minimum": 0, "maximum": 10},
        },
        "required": ["project_id"],
    },
    risk="write",
)
async def _set_project_streams(args: dict[str, Any]) -> str:
    from app.services.check_streams import set_check_streams_meta
    from app.services.img_streams import set_img_streams_meta

    pid = int(args["project_id"])
    outsee = args.get("outsee_streams")
    check = args.get("check_streams")
    if outsee is None and check is None:
        return _json({"error": "nothing to set"})
    async with SessionLocal() as s:
        project = await s.get(Project, pid)
        if project is None:
            return _json({"error": f"project {pid} not found"})
        applied: dict[str, int] = {}
        if outsee is not None:
            applied["outsee_streams"] = set_img_streams_meta(project, int(outsee))
        if check is not None:
            applied["check_streams"] = set_check_streams_meta(project, int(check))
        await s.commit()
        return _json({"ok": True, "project_id": pid, "applied": applied})


@tool(
    name="set_worker_max_parallel",
    description="Глобальный параллелизм воркера (1-4): сколько проектов идут одновременно.",
    parameters={
        "type": "object",
        "properties": {"value": {"type": "integer", "minimum": 1, "maximum": 4}},
        "required": ["value"],
    },
    risk="write",
)
async def _set_worker_max_parallel(args: dict[str, Any]) -> str:
    from app.services import runtime_streams as rs

    cfg = rs.save_runtime_streams({"worker_max_parallel": int(args["value"])})
    return _json({"ok": True, "worker_max_parallel": cfg["worker_max_parallel"]})


@tool(
    name="reset_step",
    description=(
        "ОПАСНО: полный сброс шага и всех downstream-данных проекта "
        "(удаляет артефакты шага). Только по явной просьбе пользователя. "
        "Доступно при автономии autopilot."
    ),
    parameters={
        "type": "object",
        "properties": {
            "project_id": {"type": "integer"},
            "step_code": {"type": "string"},
        },
        "required": ["project_id", "step_code"],
    },
    risk="danger",
)
async def _reset_step(args: dict[str, Any]) -> str:
    from app.services.reset_step import reset_step

    pid = int(args["project_id"])
    code = str(args["step_code"]).strip()
    async with SessionLocal() as s:
        project = await s.get(Project, pid)
        if project is None:
            return _json({"error": f"project {pid} not found"})
        summary = await reset_step(s, project, code)
        await s.commit()
        if "error" in summary:
            return _json(summary)
        return _json(
            {"ok": True, "project_id": pid, "step": code,
             "new_status": summary.get("__project_status"),
             "wiped": summary.get("__steps_wiped")}
        )
