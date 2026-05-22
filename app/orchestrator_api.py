"""Small local HTTP API for controlling the video pipeline orchestrator.

Run from the repository root:
    .\.venv\Scripts\python.exe -m uvicorn app.orchestrator_api:app --host 127.0.0.1 --port 8787

The API is intentionally local-only. It does not replace the main worker in
`app.main`; it is a control plane for creating batches, adding topics, and
starting/pausing/resuming queues. Keep `start.ps1` running for the actual
orchestrator worker.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

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


def _clean_topics(raw: list[str] | str) -> list[str]:
    if isinstance(raw, str):
        parts = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    else:
        parts = raw
    return [str(x).strip() for x in parts if str(x).strip()]


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
                ]
            )
        }

    if cmd == "status":
        return await _command_status()

    if cmd != "batch" or len(parts) < 2:
        raise HTTPException(status_code=400, detail="unknown command; send 'help'")

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
