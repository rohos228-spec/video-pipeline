"""Small local HTTP API for controlling the video pipeline orchestrator.

Run from the repository root:
    .\.venv\Scripts\python.exe -m uvicorn app.orchestrator_api:app --host 127.0.0.1 --port 8787

The API is intentionally local-only. It does not replace the main worker in
`app.main`; it is a control plane for creating batches, adding topics, and
starting/pausing/resuming queues. Keep `start.ps1` running for the actual
orchestrator worker.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from typing import Any

import aiohttp
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.db import engine, session_scope
from app.models import Base, BatchProject, Project
from app.prompts_loader import sync_prompts_from_files
from app.services import batches as batches_svc

app = FastAPI(title="video-pipeline orchestrator API", version="1.0")


class BatchCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    template_project_id: int | None = None


class TopicsRequest(BaseModel):
    topics: list[str] | str


class CommandRequest(BaseModel):
    text: str = Field(..., min_length=1)


AI_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "status",
                "create_batch",
                "add_topics",
                "start_batch",
                "pause_batch",
                "resume_batch",
                "list_projects",
                "help",
                "unknown",
            ],
        },
        "batch_id": {"type": ["integer", "null"]},
        "name": {"type": ["string", "null"]},
        "topics": {"type": "array", "items": {"type": "string"}},
        "message": {"type": "string"},
    },
    "required": ["action", "batch_id", "name", "topics", "message"],
}


def _clean_topics(raw: list[str] | str) -> list[str]:
    if isinstance(raw, str):
        parts = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    else:
        parts = raw
    return [str(x).strip() for x in parts if str(x).strip()]


def _env_value(name: str) -> str | None:
    val = os.getenv(name)
    if val:
        return val.strip().strip('"').strip("'")
    env_path = os.getcwd()
    env_file = os.path.join(env_path, ".env")
    if not os.path.exists(env_file):
        return None
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    return None


def _batch_dict(batch: BatchProject, progress: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": batch.id,
        "name": batch.name,
        "slug": batch.slug,
        "status": batch.status.value,
        "data_dir": str(batch.data_dir),
        "prompts_dir": str(batch.prompts_dir),
        "topics_xlsx": str(batch.topics_xlsx_path),
        "template_project_id": batch.template_project_id,
        "progress": progress,
    }


def _project_dict(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "topic": project.topic,
        "slug": project.slug,
        "status": project.status.value,
        "batch_id": project.batch_id,
        "batch_position": project.batch_position,
        "auto_mode": project.auto_mode,
        "data_dir": str(project.data_dir),
    }


async def _get_batch_or_404(batch_id: int) -> dict[str, Any]:
    async with session_scope() as session:
        batch = await batches_svc.get_batch(session, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
        progress = await batches_svc.batch_progress(session, batch)
        return _batch_dict(batch, progress)


@app.on_event("startup")
async def _startup() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await sync_prompts_from_files()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"ok": "true"}


@app.get("/batches")
async def list_batches() -> dict[str, Any]:
    async with session_scope() as session:
        batches = await batches_svc.list_batches(session)
        rows = []
        for batch in batches:
            rows.append(_batch_dict(batch, await batches_svc.batch_progress(session, batch)))
        return {"batches": rows}


@app.post("/batches")
async def create_batch(req: BatchCreateRequest) -> dict[str, Any]:
    async with session_scope() as session:
        batch = await batches_svc.create_batch(
            session,
            name=req.name,
            template_project_id=req.template_project_id,
        )
        progress = await batches_svc.batch_progress(session, batch)
        return {"batch": _batch_dict(batch, progress)}


@app.get("/batches/{batch_id}")
async def get_batch(batch_id: int) -> dict[str, Any]:
    batch = await _get_batch_or_404(batch_id)
    return {"batch": batch}


@app.post("/batches/{batch_id}/topics")
async def add_topics(batch_id: int, req: TopicsRequest) -> dict[str, Any]:
    topics = _clean_topics(req.topics)
    if not topics:
        raise HTTPException(status_code=400, detail="topics are empty")
    async with session_scope() as session:
        batch = await batches_svc.get_batch(session, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
        projects = await batches_svc.add_topics(session, batch, topics)
        progress = await batches_svc.batch_progress(session, batch)
        return {
            "created": [_project_dict(p) for p in projects],
            "batch": _batch_dict(batch, progress),
        }


@app.get("/batches/{batch_id}/projects")
async def list_batch_projects(batch_id: int) -> dict[str, Any]:
    async with session_scope() as session:
        batch = await batches_svc.get_batch(session, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
        projects = await batches_svc.get_batch_subprojects(session, batch_id)
        return {"projects": [_project_dict(p) for p in projects]}


@app.post("/batches/{batch_id}/start")
async def start_batch(batch_id: int) -> dict[str, Any]:
    async with session_scope() as session:
        batch = await batches_svc.start_batch_queue(session, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
        progress = await batches_svc.batch_progress(session, batch)
        return {"batch": _batch_dict(batch, progress)}


@app.post("/batches/{batch_id}/pause")
async def pause_batch(batch_id: int) -> dict[str, Any]:
    async with session_scope() as session:
        batch = await batches_svc.pause_batch_queue(session, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
        progress = await batches_svc.batch_progress(session, batch)
        return {"batch": _batch_dict(batch, progress)}


@app.post("/batches/{batch_id}/resume")
async def resume_batch(batch_id: int) -> dict[str, Any]:
    async with session_scope() as session:
        batch = await batches_svc.resume_batch_queue(session, batch_id)
        if batch is None:
            raise HTTPException(status_code=404, detail=f"batch {batch_id} not found")
        progress = await batches_svc.batch_progress(session, batch)
        return {"batch": _batch_dict(batch, progress)}


def _format_batches(rows: Iterable[dict[str, Any]]) -> str:
    lines = []
    for b in rows:
        p = b.get("progress") or {}
        lines.append(
            f"#{b['id']} {b['name']} [{b['status']}] "
            f"total={p.get('total', 0)} done={p.get('done', 0)} "
            f"ready={p.get('ready', 0)} running={p.get('in_progress', 0)}"
        )
    return "\n".join(lines) if lines else "No batches yet."


async def _command_status() -> dict[str, Any]:
    data = await list_batches()
    return {"message": _format_batches(data["batches"]), "data": data}


def _extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str):
        return payload["output_text"]
    parts: list[str] = []
    for item in payload.get("output") or []:
        for content in item.get("content") or []:
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                parts.append(str(content["text"]))
    return "\n".join(parts).strip()


async def _ai_plan_command(text: str) -> dict[str, Any]:
    api_key = _env_value("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="OPENAI_API_KEY не задан. Добавь его в .env и перезапусти API.",
        )

    model = _env_value("ORCHESTRATOR_AI_MODEL") or "gpt-4.1-mini"
    instructions = (
        "Ты управляешь локальным API оркестратора video-pipeline. "
        "Преобразуй русский или английский текст пользователя в одно безопасное JSON-действие. "
        "Если пользователь хочет создать массовую генерацию, action=create_batch. "
        "Если дает список тем для уже созданного массового проекта, action=add_topics. "
        "Если просит запустить/поставить на паузу/продолжить очередь, используй batch_id. "
        "Если batch_id не указан и действие требует id, верни action=unknown и объясни, что нужен id. "
        "Не придумывай id. Не выполняй удаление файлов. JSON only."
    )
    body = {
        "model": model,
        "instructions": instructions,
        "input": text,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "orchestrator_action",
                "strict": True,
                "schema": AI_ACTION_SCHEMA,
            }
        },
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession() as client:
        async with client.post(
            "https://api.openai.com/v1/responses",
            headers=headers,
            json=body,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            raw = await resp.text()
            if resp.status >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"OpenAI API error {resp.status}: {raw[:1000]}",
                )
            payload = json.loads(raw)

    out = _extract_output_text(payload)
    if not out:
        raise HTTPException(status_code=502, detail="OpenAI API вернул пустой ответ")
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=502,
            detail=f"OpenAI API вернул не JSON: {e}: {out[:1000]}",
        ) from e


async def _execute_ai_action(plan: dict[str, Any]) -> dict[str, Any]:
    action = plan.get("action")
    batch_id = plan.get("batch_id")
    name = (plan.get("name") or "").strip()
    topics = _clean_topics(plan.get("topics") or [])

    if action == "status":
        return await _command_status()
    if action == "help":
        return await command(CommandRequest(text="help"))
    if action == "create_batch":
        if not name:
            raise HTTPException(status_code=400, detail="AI не указал name для create_batch")
        data = await create_batch(BatchCreateRequest(name=name))
        b = data["batch"]
        return {"message": f"AI: создан batch #{b['id']}: {b['name']}", "data": data, "ai": plan}
    if action == "add_topics":
        if not isinstance(batch_id, int):
            raise HTTPException(status_code=400, detail="Для добавления тем нужен batch_id")
        if not topics:
            raise HTTPException(status_code=400, detail="AI не указал темы")
        data = await add_topics(batch_id, TopicsRequest(topics=topics))
        return {
            "message": f"AI: добавлено {len(data['created'])} тем в batch #{batch_id}",
            "data": data,
            "ai": plan,
        }
    if action == "start_batch":
        if not isinstance(batch_id, int):
            raise HTTPException(status_code=400, detail="Для старта нужен batch_id")
        data = await start_batch(batch_id)
        return {"message": f"AI: запущен batch #{batch_id}", "data": data, "ai": plan}
    if action == "pause_batch":
        if not isinstance(batch_id, int):
            raise HTTPException(status_code=400, detail="Для паузы нужен batch_id")
        data = await pause_batch(batch_id)
        return {"message": f"AI: batch #{batch_id} на паузе", "data": data, "ai": plan}
    if action == "resume_batch":
        if not isinstance(batch_id, int):
            raise HTTPException(status_code=400, detail="Для продолжения нужен batch_id")
        data = await resume_batch(batch_id)
        return {"message": f"AI: batch #{batch_id} продолжен", "data": data, "ai": plan}
    if action == "list_projects":
        if not isinstance(batch_id, int):
            raise HTTPException(status_code=400, detail="Для списка проектов нужен batch_id")
        data = await list_batch_projects(batch_id)
        rows = [
            f"#{p['id']} pos={p['batch_position']} [{p['status']}] {p['topic']}"
            for p in data["projects"]
        ]
        return {
            "message": "\n".join(rows) if rows else "No projects in this batch.",
            "data": data,
            "ai": plan,
        }

    msg = plan.get("message") or "AI не понял команду. Уточни batch id и действие."
    raise HTTPException(status_code=400, detail=msg)


@app.post("/ai-command")
async def ai_command(req: CommandRequest) -> dict[str, Any]:
    plan = await _ai_plan_command(req.text.strip())
    return await _execute_ai_action(plan)


@app.post("/command")
async def command(req: CommandRequest) -> dict[str, Any]:
    text = req.text.strip()
    first_line, _, rest = text.partition("\n")
    parts = first_line.strip().split()
    if not parts:
        raise HTTPException(status_code=400, detail="empty command")
    cmd = parts[0].lower()

    if cmd in {"help", "?"}:
        return {
            "message": "\n".join(
                [
                    "Commands:",
                    "  status",
                    "  batch new <name>",
                    "  batch topics <id>  (topics on next lines)",
                    "  batch start <id>",
                    "  batch pause <id>",
                    "  batch resume <id>",
                    "  batch projects <id>",
                    "",
                    "You can also write normal text, for example:",
                    "  создай массовый проект про коттеджи",
                    "  добавь в batch 1 темы: кухня, спальня, гостиная",
                    "  запусти массовый проект 1",
                ]
            )
        }

    if cmd == "status":
        return await _command_status()

    if cmd != "batch" or len(parts) < 2:
        return await ai_command(req)

    action = parts[1].lower()
    if action == "new":
        name = first_line.split(None, 2)[2].strip() if len(parts) >= 3 else ""
        if not name:
            raise HTTPException(status_code=400, detail="batch name is empty")
        created = await create_batch(BatchCreateRequest(name=name))
        b = created["batch"]
        return {"message": f"Created batch #{b['id']}: {b['name']}", "data": created}

    if len(parts) < 3 or not parts[2].isdigit():
        raise HTTPException(status_code=400, detail="batch id is required")
    batch_id = int(parts[2])

    if action == "topics":
        topics = _clean_topics(rest)
        added = await add_topics(batch_id, TopicsRequest(topics=topics))
        return {
            "message": f"Added {len(added['created'])} topic(s) to batch #{batch_id}",
            "data": added,
        }
    if action == "start":
        data = await start_batch(batch_id)
        return {"message": f"Started batch #{batch_id}", "data": data}
    if action == "pause":
        data = await pause_batch(batch_id)
        return {"message": f"Paused batch #{batch_id}", "data": data}
    if action == "resume":
        data = await resume_batch(batch_id)
        return {"message": f"Resumed batch #{batch_id}", "data": data}
    if action == "projects":
        data = await list_batch_projects(batch_id)
        rows = [
            f"#{p['id']} pos={p['batch_position']} [{p['status']}] {p['topic']}"
            for p in data["projects"]
        ]
        return {"message": "\n".join(rows) if rows else "No projects in this batch.", "data": data}

    raise HTTPException(status_code=400, detail="unknown batch command; send 'help'")
