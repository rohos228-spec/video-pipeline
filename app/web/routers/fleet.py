"""Fleet API: hub (главный ПК) + local (на каждой станции)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from sqlalchemy import select

from app.db import session_scope
from app.fleet import bundle as bundle_svc
from app.fleet.client import (
    FleetAgentError,
    agent_delete,
    agent_download_to_file,
    agent_get,
    agent_get_bytes,
    agent_post,
    agent_upload_file,
    ping_agent,
)
from app.fleet.montage_queue import (
    META_ENQUEUED,
    enqueue_for_montage,
    process_montage_queue,
    queued_projects_ordered,
)
from app.fleet.hub_probe import (
    ensure_manifest_workers,
    pick_preferred_worker_node_id,
    probe_fleet_node,
    sync_all_fleet_nodes,
    sync_fleet_node_by_id,
)
from app.fleet.self_node import is_local_fleet_node, self_node_name
from app.services.node_step_params import send_to_main_pc_for_project
from app.models import FleetNode, FleetNodeStatus, Project, ProjectStatus
from app.project_root import find_project_root
from app.settings import settings
from app.web.auth_sessions import AuthDep

router = APIRouter(prefix="/fleet", tags=["fleet"])


def _pipeline_root() -> Path:
    return find_project_root().resolve()


# ── Auth ─────────────────────────────────────────────────────────────────────


def _check_agent_token(authorization: str | None) -> None:
    expected = (settings.fleet_agent_token or "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="invalid fleet token")


def _agent_auth_dep(
    authorization: Annotated[str | None, Header()] = None,
    authorization_query: Annotated[str | None, Query(alias="authorization")] = None,
) -> None:
    _check_agent_token(authorization or authorization_query)


AgentAuth = Annotated[None, Depends(_agent_auth_dep)]


# ── Schemas ──────────────────────────────────────────────────────────────────


class FleetNodeOut(BaseModel):
    id: int
    name: str
    base_url: str
    is_main: bool
    role: str
    status: str
    last_seen: datetime | None
    hostname: str | None
    pipeline_version: str | None
    meta: dict
    hub_reachable: bool | None = None
    hub_probe_error: str | None = None


class FleetNodesListOut(BaseModel):
    nodes: list[FleetNodeOut]
    preferred_node_id: int | None = None


class FleetNodeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=4, max_length=300)
    token: str = ""
    is_main: bool = False
    role: str = "agent"


class FleetRegister(BaseModel):
    name: str
    base_url: str
    hostname: str | None = None
    role: str = "agent"
    is_main: bool = False


class PowerShellRun(BaseModel):
    command: str = Field(min_length=1, max_length=8000)
    cwd: str | None = None
    timeout_sec: int = Field(default=120, ge=5, le=600)


class FileWrite(BaseModel):
    path: str
    content_base64: str | None = None
    mkdir: bool = True


class MontagePull(BaseModel):
    run_assemble: bool = True


class MontageReadyNotify(BaseModel):
    project_id: int
    slug: str = ""
    node_name: str
    montage_ready_at: str | None = None


class HandoffCompleteBody(BaseModel):
    via: str = "pull"


def _node_out(n: FleetNode) -> FleetNodeOut:
    meta = n.meta or {}
    return FleetNodeOut(
        id=n.id,
        name=n.name,
        base_url=n.base_url,
        is_main=n.is_main,
        role=n.role,
        status=n.status.value if hasattr(n.status, "value") else str(n.status),
        last_seen=n.last_seen,
        hostname=n.hostname,
        pipeline_version=n.pipeline_version,
        meta=meta,
        hub_reachable=meta.get("hub_reachable") if "hub_reachable" in meta else None,
        hub_probe_error=meta.get("hub_probe_error"),
    )


async def _get_node(session, node_id: int) -> FleetNode:
    node = await session.get(FleetNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return node


def _raise_fleet_proxy_error(node: FleetNode, exc: Exception) -> None:
    logger.warning("fleet proxy failed {} @ {}: {}", node.name, node.base_url, exc)
    raise HTTPException(
        status_code=502,
        detail=f"Не достучаться до {node.name} ({node.base_url}): {exc}"[:400],
    ) from exc


async def _proxy_agent_get(
    node: FleetNode,
    path: str,
    *,
    params: dict[str, str] | None = None,
    timeout_sec: int = 60,
) -> dict:
    token = node.token or settings.fleet_agent_token
    try:
        return await agent_get(
            node.base_url,
            token,
            path,
            params=params,
            timeout_sec=timeout_sec,
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001
        _raise_fleet_proxy_error(node, exc)


# ── Hub: registry ────────────────────────────────────────────────────────────


@router.get("/nodes", response_model=FleetNodesListOut)
async def list_nodes(_user: AuthDep = None) -> FleetNodesListOut:
    role = (settings.fleet_role or "hub").strip().lower()
    if role == "agent":
        hub = (settings.fleet_hub_url or "").strip().rstrip("/")
        if hub and "127.0.0.1" not in hub and "localhost" not in hub:
            token = settings.fleet_agent_token or ""
            try:
                data = await agent_get(hub, token, "/api/fleet/nodes")
                if isinstance(data, dict) and isinstance(data.get("nodes"), list):
                    nodes = [FleetNodeOut.model_validate(n) for n in data["nodes"]]
                    return FleetNodesListOut(
                        nodes=nodes,
                        preferred_node_id=data.get("preferred_node_id"),
                    )
                if isinstance(data, list):
                    nodes = [FleetNodeOut.model_validate(n) for n in data]
                    return FleetNodesListOut(nodes=nodes)
            except FleetAgentError as exc:
                logger.warning("fleet agent: hub nodes list failed: {}", exc.detail)
            except Exception as exc:  # noqa: BLE001
                logger.warning("fleet agent: hub nodes list error: {}", exc)
    await ensure_manifest_workers()
    async with session_scope() as session:
        rows = (await session.execute(select(FleetNode).order_by(FleetNode.name))).scalars().all()
        nodes = [_node_out(n) for n in rows]
        preferred = pick_preferred_worker_node_id(rows)
        return FleetNodesListOut(nodes=nodes, preferred_node_id=preferred)


@router.post("/nodes", response_model=FleetNodeOut)
async def create_node(body: FleetNodeCreate) -> FleetNodeOut:
    async with session_scope() as session:
        existing = (
            await session.execute(select(FleetNode).where(FleetNode.name == body.name))
        ).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail="node name already exists")
        node = FleetNode(
            name=body.name,
            base_url=body.base_url.rstrip("/"),
            token=body.token,
            is_main=body.is_main,
            role=body.role,
            status=FleetNodeStatus.offline,
        )
        session.add(node)
        await session.commit()
        await session.refresh(node)
        return _node_out(node)


@router.delete("/nodes/{node_id}")
async def delete_node(node_id: int) -> dict:
    async with session_scope() as session:
        node = await _get_node(session, node_id)
        await session.delete(node)
        await session.commit()
    return {"ok": True}


@router.post("/register", response_model=FleetNodeOut)
async def register_heartbeat(body: FleetRegister, authorization: str | None = Header(None)) -> FleetNodeOut:
    _check_agent_token(authorization)
    async with session_scope() as session:
        node = (
            await session.execute(select(FleetNode).where(FleetNode.name == body.name))
        ).scalar_one_or_none()
        if node is None:
            node = FleetNode(
                name=body.name,
                base_url=body.base_url.rstrip("/"),
                token=settings.fleet_agent_token or "",
                is_main=body.is_main,
                role=body.role,
            )
            session.add(node)
        else:
            node.base_url = body.base_url.rstrip("/")
            node.hostname = body.hostname
            node.role = body.role
            node.is_main = body.is_main
        node.status = FleetNodeStatus.online
        node.last_seen = datetime.now(timezone.utc)
        await session.commit()
        node_id = node.id
    asyncio.create_task(sync_fleet_node_by_id(node_id))
    async with session_scope() as session:
        fresh = await session.get(FleetNode, node_id)
        if fresh is None:
            raise HTTPException(status_code=404, detail="node not found after register")
        return _node_out(fresh)


@router.post("/montage-ready")
async def agent_montage_ready_notify(
    body: MontageReadyNotify,
    authorization: str | None = Header(None),
) -> dict:
    """Agent сообщает hub: проект готов к montage — немедленный pull."""
    _check_agent_token(authorization)
    from app.fleet.pull_loop import pull_montage_from_agent

    return await pull_montage_from_agent(
        body.node_name,
        body.project_id,
        slug=body.slug,
        ready_at=str(body.montage_ready_at or "").strip(),
        force=True,
    )


@router.post("/nodes/sync-all")
async def sync_all_nodes(_user: AuthDep = None) -> dict:
    try:
        await ensure_manifest_workers()
        return await sync_all_fleet_nodes()
    except Exception as exc:  # noqa: BLE001
        logger.exception("fleet sync-all failed: {}", exc)
        raise HTTPException(status_code=500, detail=str(exc)[:300]) from exc


@router.post("/nodes/{node_id}/sync")
async def sync_node(node_id: int, _user: AuthDep = None) -> dict:
    result = await sync_fleet_node_by_id(node_id)
    return result


# ── Hub → agent proxy ────────────────────────────────────────────────────────


async def _proxy_node(node_id: int) -> FleetNode:
    async with session_scope() as session:
        return await _get_node(session, node_id)


@router.get("/nodes/{node_id}/pipeline")
async def node_pipeline(node_id: int, _user: AuthDep = None) -> dict:
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return await local_pipeline()
    base_url = (node.base_url or "").strip().rstrip("/")
    if not base_url:
        raise HTTPException(
            status_code=400,
            detail=f"У станции «{node.name}» пустой base_url — hub обновит URL при heartbeat",
        )
    meta = node.meta or {}
    if meta.get("hub_reachable") is False:
        await sync_fleet_node_by_id(node_id)
        node = await _proxy_node(node_id)
        base_url = (node.base_url or "").strip().rstrip("/")
    token = node.token or settings.fleet_agent_token
    try:
        data = await agent_get(base_url, token, "/api/fleet/local/pipeline")
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001
        _raise_fleet_proxy_error(node, exc)
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=502,
            detail=f"Станция {node.name} вернула неожиданный ответ ({type(data).__name__})",
        )
    return data


@router.get("/nodes/{node_id}/files")
async def node_files(node_id: int, path: str = ".", _user: AuthDep = None) -> dict:
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return await local_files(path=path)
    try:
        return await _proxy_agent_get(
            node, "/api/fleet/local/files", params={"path": path}
        )
    except HTTPException:
        raise


@router.get("/nodes/{node_id}/files/download")
async def node_files_download(node_id: int, path: str, _user: AuthDep = None):
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return await local_files_download(path=path)
    token = node.token or settings.fleet_agent_token
    try:
        blob = await agent_get_bytes(
            node.base_url,
            token,
            "/api/fleet/local/files/download",
            params={"path": path},
            timeout_sec=600,
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001
        _raise_fleet_proxy_error(node, exc)
    filename = Path(path.replace("\\", "/")).name or "download"
    return Response(
        content=blob,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/nodes/{node_id}/files/content")
async def node_files_content(node_id: int, path: str, _user: AuthDep = None) -> dict:
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return await local_files_content(path=path)
    try:
        return await _proxy_agent_get(
            node, "/api/fleet/local/files/content", params={"path": path}
        )
    except HTTPException:
        raise


@router.delete("/nodes/{node_id}/files")
async def node_delete_file(node_id: int, path: str, _user: AuthDep = None) -> dict:
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return await local_delete_file(path=path)
    token = node.token or settings.fleet_agent_token
    try:
        return await agent_delete(
            node.base_url,
            token,
            "/api/fleet/local/files",
            params={"path": path},
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001
        _raise_fleet_proxy_error(node, exc)


@router.post("/nodes/{node_id}/files/upload")
async def node_upload_file(
    node_id: int,
    path: str,
    file: UploadFile = File(...),
    _user: AuthDep = None,
) -> dict:
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return await local_upload_file(path=path, file=file)
    token = node.token or settings.fleet_agent_token
    content = await file.read()
    try:
        upload_path = (
            "/api/fleet/local/files/upload"
            f"?path={path.replace(chr(92), '/')}"
        )
        return await agent_upload_file(
            node.base_url,
            token,
            upload_path,
            file_bytes=content,
            filename=file.filename or Path(path).name,
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001
        _raise_fleet_proxy_error(node, exc)


@router.post("/nodes/{node_id}/powershell")
async def node_powershell(node_id: int, body: PowerShellRun, _user: AuthDep = None) -> dict:
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return await local_powershell(body)
    token = node.token or settings.fleet_agent_token
    try:
        return await agent_post(
            node.base_url,
            token,
            "/api/fleet/local/powershell",
            json_body=body.model_dump(),
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    except Exception as exc:  # noqa: BLE001
        _raise_fleet_proxy_error(node, exc)


@router.post("/nodes/{node_id}/powershell/stream")
async def node_powershell_stream(node_id: int, body: PowerShellRun, _user: AuthDep = None):
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return StreamingResponse(
            _powershell_stream_events(body),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return StreamingResponse(
        _proxy_agent_ps_stream(node, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/nodes/{node_id}/logs/stream")
async def node_pipeline_logs_stream(node_id: int, _user: AuthDep = None):
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return StreamingResponse(
            _pipeline_log_stream_events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return StreamingResponse(
        _proxy_agent_log_stream(node),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


_pull_jobs: set[str] = set()


async def _pull_remote_project_to_main(
    node: FleetNode,
    project_id: int,
    *,
    run_assemble: bool,
) -> dict:
    from app.fleet.transfer_state import update_fleet_transfer

    token = node.token or settings.fleet_agent_token
    import tempfile

    await update_fleet_transfer(
        project_id,
        phase="download",
        direction="from_agent",
        percent=0,
        message=f"Загрузка с {node.name}…",
        source_node=node.name or "",
        target=node.base_url or "",
    )

    fd, tmp_name = tempfile.mkstemp(suffix=".tar.gz", prefix="fleet-pull-")
    os.close(fd)
    bundle_path = Path(tmp_name)
    try:
        logger.info(
            "fleet pull: downloading bundle {}#{} from {} …",
            node.name,
            project_id,
            node.base_url,
        )
        size = await agent_download_to_file(
            node.base_url,
            token,
            f"/api/fleet/local/projects/{project_id}/export-bundle",
            bundle_path,
            timeout_sec=3600,
            progress_label=f"[#{project_id}] fleet pull {node.name}",
            project_id=project_id,
        )
        logger.info(
            "fleet pull: downloaded {} bytes from {}#{}",
            size,
            node.name,
            project_id,
        )
    except FleetAgentError as exc:
        bundle_path.unlink(missing_ok=True)
        raise RuntimeError(f"agent HTTP {exc.status}: {exc.detail}") from exc
    except Exception as exc:  # noqa: BLE001
        bundle_path.unlink(missing_ok=True)
        from app.fleet.transfer_state import FleetTransferCancelled

        if isinstance(exc, FleetTransferCancelled):
            raise
        raise

    if size <= 0:
        bundle_path.unlink(missing_ok=True)
        raise RuntimeError("empty bundle from agent")

    try:
        async with session_scope() as session:
            project = await bundle_svc.import_project_bundle_file(
                session, bundle_path, run_assemble=False
            )
            meta = dict(project.meta or {})
            meta["fleet_source_node"] = node.name
            meta["fleet_source_project_id"] = project_id
            project.meta = meta
            queued = False
            if run_assemble:
                queued = await enqueue_for_montage(session, project, source_node=node.name)
                await process_montage_queue(session)
            await session.commit()
            pull_key = f"{node.name}:{project.slug}"
            from app.fleet import pull_loop as pull_svc

            ready_at = str((project.meta or {}).get("montage_ready_at") or "")
            if ready_at:
                pull_svc._pulled_versions[pull_key] = ready_at
            hub_pid = project.id
            montage_msg = (
                f"Загружено с {node.name} → монтаж на hub (#{hub_pid})"
                if queued
                else f"Загружено с {node.name} → hub #{hub_pid} (очередь не запущена)"
            )
            await update_fleet_transfer(
                hub_pid,
                phase="done",
                direction="from_agent",
                percent=100,
                message=montage_msg,
                source_node=node.name or "",
                slug=project.slug,
                status="done",
            )
            # Дублируем на worker id — UI «Сеть» слушает оба.
            await update_fleet_transfer(
                project_id,
                phase="done",
                direction="from_agent",
                percent=100,
                message=montage_msg,
                source_node=node.name or "",
                slug=project.slug,
                status="done",
            )
            return {
                "ok": True,
                "project_id": project.id,
                "slug": project.slug,
                "queued": queued,
            }
    finally:
        bundle_path.unlink(missing_ok=True)


async def _pull_remote_project_background(
    job_key: str,
    node: FleetNode,
    project_id: int,
    *,
    run_assemble: bool,
) -> None:
    try:
        result = await _pull_remote_project_to_main(
            node, project_id, run_assemble=run_assemble
        )
        logger.info("fleet pull background done {}: {}", job_key, result)
    except Exception as exc:  # noqa: BLE001
        logger.exception("fleet pull background failed {}: {}", job_key, exc)
        from app.fleet.transfer_state import FleetTransferCancelled, update_fleet_transfer

        if isinstance(exc, FleetTransferCancelled) or "cancelled" in str(exc).lower():
            await update_fleet_transfer(
                project_id,
                phase="cancelled",
                direction="from_agent",
                percent=0,
                message="⏹ Остановлено пользователем",
                source_node=node.name or "",
                status="error",
            )
        else:
            await update_fleet_transfer(
                project_id,
                phase="error",
                direction="from_agent",
                percent=0,
                message=str(exc)[:200],
                source_node=node.name or "",
                status="error",
            )
    finally:
        _pull_jobs.discard(job_key)


@router.post("/nodes/{node_id}/projects/{project_id}/pull-to-main")
async def pull_project_to_main(
    node_id: int, project_id: int, body: MontagePull, _user: AuthDep = None
) -> dict:
    """Скачать bundle с agent или запустить монтаж локально (hub+worker)."""
    node = await _proxy_node(node_id)

    if is_local_fleet_node(node):
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise HTTPException(status_code=404, detail="project not found")
            meta = dict(project.meta or {})
            meta["montage_ready"] = True
            meta["fleet_local_montage"] = True
            project.meta = meta
            queued = False
            if body.run_assemble:
                queued = await enqueue_for_montage(
                    session, project, source_node=node.name or self_node_name()
                )
                await process_montage_queue(session)
            await session.commit()
            return {
                "ok": True,
                "project_id": project.id,
                "slug": project.slug,
                "local": True,
                "queued": queued,
            }

    job_key = f"{node_id}:{project_id}"
    if job_key in _pull_jobs:
        return {
            "ok": True,
            "started": False,
            "reason": "already running",
            "message": "Загрузка уже идёт — смотри полоску прогресса на канвасе.",
        }

    _pull_jobs.add(job_key)
    from app.fleet.transfer_state import register_transfer_task

    task = asyncio.create_task(
        _pull_remote_project_background(
            job_key,
            node,
            project_id,
            run_assemble=body.run_assemble,
        )
    )
    register_transfer_task(project_id, task)
    return {
        "ok": True,
        "started": True,
        "project_id": project_id,
        "node": node.name,
        "message": (
            f"Загрузка проекта #{project_id} с {node.name}… "
            "Прогресс — полоска «Передача bundle» во вкладке Сеть."
        ),
    }


# ── Local agent endpoints (на каждой станции) ────────────────────────────────


def _safe_path(base: Path, rel: str) -> Path:
    rel = rel.replace("\\", "/").lstrip("/")
    target = (base / rel).resolve()
    base_res = base.resolve()
    if not str(target).startswith(str(base_res)):
        raise HTTPException(status_code=400, detail="path escapes sandbox")
    return target


def _rel_path(root: Path, target: Path) -> str:
    rel = str(target.relative_to(root)).replace("\\", "/")
    return rel or "."


_TEXT_VIEW_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".py",
    ".ps1",
    ".env",
    ".log",
    ".yaml",
    ".yml",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".tsx",
    ".csv",
    ".xml",
    ".ini",
    ".cfg",
    ".toml",
}
_MAX_VIEW_BYTES = 512_000


def _powershell_stream_argv(user_command: str) -> list[str]:
    """Запуск PS с построчным flush — иначе stdout буферизуется до конца процесса."""
    script = (
        "$ProgressPreference='SilentlyContinue'\n"
        "$ErrorActionPreference='Continue'\n"
        "function __FleetExec__ {\n"
        f"{user_command}\n"
        "}\n"
        "__FleetExec__ 2>&1 | ForEach-Object {\n"
        "  if ($_ -is [System.Management.Automation.ErrorRecord]) {\n"
        "    $line = $_.Exception.Message\n"
        "  } else {\n"
        "    $line = ($_.ToString()).TrimEnd()\n"
        "  }\n"
        "  if ($line) {\n"
        "    [Console]::Out.WriteLine($line)\n"
        "    [Console]::Out.Flush()\n"
        "  }\n"
        "}\n"
        "if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded]


def _is_clixml_noise(text: str) -> bool:
    t = text.strip()
    return t.startswith("#< CLIXML") or t.startswith("<Objs ") or "<Objs Version=" in t


async def _powershell_stream_events(body: PowerShellRun) -> AsyncIterator[str]:
    if platform.system() != "Windows":
        yield f"data: {json.dumps({'type': 'stderr', 'text': 'powershell only on Windows'}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'exit', 'code': 1})}\n\n"
        return

    cwd = body.cwd or str(_pipeline_root())
    proc = await asyncio.create_subprocess_exec(
        *_powershell_stream_argv(body.command),
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()

    async def _reader(stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            await queue.put(("stdout", None))
            return
        while True:
            line = await stream.readline()
            if not line:
                await queue.put(("stdout", None))
                return
            text = line.decode("utf-8", errors="replace")
            if _is_clixml_noise(text):
                continue
            await queue.put(("stdout", text))

    asyncio.create_task(_reader(proc.stdout))
    finished = False
    while not finished or not queue.empty():
        try:
            tag, text = await asyncio.wait_for(queue.get(), timeout=0.2)
        except asyncio.TimeoutError:
            if proc.returncode is not None and queue.empty():
                break
            yield ": keepalive\n\n"
            await asyncio.sleep(0)
            continue
        if text is None:
            finished = True
            continue
        yield f"data: {json.dumps({'type': tag, 'text': text}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0)
    code = await proc.wait()
    yield f"data: {json.dumps({'type': 'exit', 'code': code})}\n\n"


def _sse_payload(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _list_log_candidates(data_dir: Path | None = None) -> list[Path]:
    root = (data_dir or settings.data_dir).resolve()
    found: list[Path] = []
    for name in ("studio-live.log", "backend.log"):
        p = root / name
        if p.is_file():
            found.append(p)
    found.extend(root.glob("backend-*.log"))
    monitor = root / "monitor" / "logs"
    if monitor.is_dir():
        found.extend(monitor.glob("pipeline_*.log"))
    return found


def _resolve_backend_log_path() -> Path:
    """Самый свежий лог — studio-live.log (текущий процесс) или backend-*.log."""
    data_dir = settings.data_dir.resolve()
    live = data_dir / "studio-live.log"
    candidates = _list_log_candidates(data_dir)
    if live.is_file():
        return live
    if candidates:
        return max(candidates, key=lambda p: p.stat().st_mtime)
    return data_dir / "backend.log"


def _read_log_chunk(path: Path, pos: int) -> tuple[str, int]:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        handle.seek(pos)
        chunk = handle.read()
        return chunk, handle.tell()


async def _pipeline_log_stream_events() -> AsyncIterator[str]:
    """Live tail backend / pipeline log file (loguru + worker output)."""
    log_path = _resolve_backend_log_path()
    pos = 0
    inode: int | None = None
    last_mtime = 0.0
    idle_ticks = 0

    if log_path.exists():
        st = log_path.stat()
        inode = st.st_ino
        last_mtime = st.st_mtime
        pos = max(0, st.st_size - 131072)

    yield _sse_payload(
        {
            "type": "meta",
            "text": f"tail: {log_path.name} ({log_path.parent.name}/)\n",
        }
    )

    while True:
        try:
            latest = _resolve_backend_log_path()
            if latest != log_path:
                log_path = latest
                pos = 0
                inode = None
                last_mtime = 0.0
                if log_path.exists():
                    st = log_path.stat()
                    inode = st.st_ino
                    last_mtime = st.st_mtime
                    pos = max(0, st.st_size - 131072)
                yield _sse_payload({"type": "meta", "text": f"tail: {log_path.name}\n"})

            if not log_path.exists():
                yield _sse_payload({"type": "stderr", "text": f"ожидание лога {log_path.name}…\n"})
                await asyncio.sleep(1.0)
                continue

            st = log_path.stat()
            if inode is not None and st.st_ino != inode:
                pos = max(0, st.st_size - 131072)
            elif st.st_mtime > last_mtime and st.st_size < pos:
                pos = 0
            inode = st.st_ino
            last_mtime = st.st_mtime
            if st.st_size < pos:
                pos = max(0, st.st_size - 131072)

            path_snapshot = log_path
            read_pos = pos
            chunk, new_pos = await asyncio.to_thread(_read_log_chunk, path_snapshot, read_pos)
            if chunk:
                pos = new_pos
                idle_ticks = 0
                for line in chunk.splitlines(keepends=True):
                    if line.strip():
                        yield _sse_payload({"type": "stdout", "text": line})
                await asyncio.sleep(0)
            else:
                idle_ticks += 1
                if idle_ticks % 8 == 0:
                    newer = _resolve_backend_log_path()
                    if newer != log_path:
                        continue
                yield ": keepalive\n\n"
                await asyncio.sleep(0.35)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield _sse_payload({"type": "stderr", "text": f"tail error: {exc}\n"})
            await asyncio.sleep(1.0)


async def _proxy_agent_log_stream(node: FleetNode) -> AsyncIterator[bytes]:
    import aiohttp

    token = node.token or settings.fleet_agent_token
    url = node.base_url.rstrip("/") + "/api/fleet/local/logs/stream"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    timeout = aiohttp.ClientTimeout(total=None, sock_read=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers) as resp:
            if resp.status >= 400:
                text = await resp.text()
                payload = json.dumps({"type": "stderr", "text": text[:500]}, ensure_ascii=False)
                yield f"data: {payload}\n\n".encode("utf-8")
                return
            async for chunk in resp.content.iter_any():
                if chunk:
                    yield chunk


async def _proxy_agent_ps_stream(node: FleetNode, body: PowerShellRun) -> AsyncIterator[bytes]:
    import aiohttp

    token = node.token or settings.fleet_agent_token
    url = node.base_url.rstrip("/") + "/api/fleet/local/powershell/stream"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    timeout = aiohttp.ClientTimeout(total=body.timeout_sec + 30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body.model_dump(), headers=headers) as resp:
            if resp.status >= 400:
                text = await resp.text()
                payload = json.dumps({"type": "stderr", "text": text[:500]}, ensure_ascii=False)
                yield f"data: {payload}\n\n".encode("utf-8")
                yield f"data: {json.dumps({'type': 'exit', 'code': resp.status})}\n\n".encode("utf-8")
                return
            async for chunk in resp.content.iter_any():
                if chunk:
                    yield chunk


@router.get("/local/info")
async def local_info(_auth: AgentAuth = None) -> dict:
    from app.web.studio_version import read_studio_version

    ver = read_studio_version()
    return {
        "name": settings.fleet_node_name or platform.node(),
        "hostname": platform.node(),
        "role": settings.fleet_role,
        "is_main": settings.fleet_is_main,
        "studio_version": ver.get("label") or ver.get("version"),
        "asr_backend": settings.asr_backend,
        "data_dir": str(settings.data_dir),
        "pipeline_root": str(_pipeline_root()),
    }


def _json_safe(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _project_error_message(meta: dict) -> str | None:
    fs = meta.get("step_failure")
    fs_dict = dict(fs) if isinstance(fs, dict) else {}
    blocked = meta.get("assemble_blocked") or meta.get("montage_blocked")
    if blocked:
        return str(blocked)[:400]
    last = fs_dict.get("last_error")
    if last:
        return str(last)[:400]
    return None


def _build_transfer_index() -> tuple[dict[int, dict], dict[str, dict]]:
    from app.fleet.transfer_state import list_active_transfers

    by_id: dict[int, dict] = {}
    by_slug: dict[str, dict] = {}
    for rec in list_active_transfers():
        pid = int(rec.get("project_id") or 0)
        if pid:
            by_id[pid] = rec
        slug = str(rec.get("slug") or "").strip()
        if slug:
            by_slug[slug] = rec
    return by_id, by_slug


def _fleet_montage_ui(
    project: Project,
    *,
    montage_queued: bool,
    queue_pos: int | None,
    transfer_by_id: dict[int, dict],
    transfer_by_slug: dict[str, dict],
    is_hub_node: bool,
) -> dict[str, object]:
    meta = project.meta or {}
    status = project.status
    slug = project.slug or ""
    err = _project_error_message(meta)

    tr = transfer_by_id.get(project.id) or transfer_by_slug.get(slug)
    if tr and tr.get("status") == "active":
        pct = int(tr.get("percent") or 0)
        return {
            "fleet_stage": "downloading",
            "fleet_stage_label": f"Скачивание на hub · {pct}%",
            "error_message": None,
            "transfer_percent": pct,
            "can_pull_to_hub": False,
            "show_open_hub": False,
        }

    if status == ProjectStatus.paused or status == ProjectStatus.failed:
        return {
            "fleet_stage": "error",
            "fleet_stage_label": "Ошибка · пауза",
            "error_message": err or "Проект на паузе — откройте на канвасе",
            "transfer_percent": None,
            "can_pull_to_hub": False,
            "show_open_hub": is_hub_node,
        }

    if err and status == ProjectStatus.assembling:
        return {
            "fleet_stage": "error",
            "fleet_stage_label": "Ошибка монтажа",
            "error_message": err,
            "transfer_percent": None,
            "can_pull_to_hub": False,
            "show_open_hub": is_hub_node,
        }

    if status == ProjectStatus.assembling:
        return {
            "fleet_stage": "assembling",
            "fleet_stage_label": "Монтаж на hub…",
            "error_message": None,
            "transfer_percent": None,
            "can_pull_to_hub": False,
            "show_open_hub": is_hub_node,
        }

    if status in {ProjectStatus.assembled, ProjectStatus.published, ProjectStatus.publishing}:
        return {
            "fleet_stage": "done",
            "fleet_stage_label": "Монтаж готов",
            "error_message": None,
            "transfer_percent": None,
            "can_pull_to_hub": False,
            "show_open_hub": is_hub_node,
        }

    if montage_queued:
        pos = queue_pos or 0
        label = f"В очереди hub · #{pos}" if pos else "В очереди hub"
        return {
            "fleet_stage": "queued",
            "fleet_stage_label": label,
            "error_message": None,
            "transfer_percent": None,
            "can_pull_to_hub": False,
            "show_open_hub": is_hub_node,
        }

    montage_ready = bool(meta.get("montage_ready")) or status in bundle_svc.MONTAGE_READY_STATUSES
    handoff_done = bool(meta.get("fleet_handoff_complete"))

    if is_hub_node:
        if meta.get("fleet_source_node"):
            if montage_ready and status == ProjectStatus.music_ready:
                return {
                    "fleet_stage": "on_hub",
                    "fleet_stage_label": "На hub · ждёт монтажа",
                    "error_message": err,
                    "transfer_percent": None,
                    "can_pull_to_hub": False,
                    "show_open_hub": True,
                }
        if err:
            return {
                "fleet_stage": "error",
                "fleet_stage_label": "Ошибка",
                "error_message": err,
                "transfer_percent": None,
                "can_pull_to_hub": False,
                "show_open_hub": True,
            }

    if not is_hub_node and handoff_done:
        return {
            "fleet_stage": "sent",
            "fleet_stage_label": "Отправлен на hub",
            "error_message": None,
            "transfer_percent": None,
            "can_pull_to_hub": False,
            "show_open_hub": True,
        }

    if montage_ready and not handoff_done:
        return {
            "fleet_stage": "ready",
            "fleet_stage_label": "Готов · отправить на hub",
            "error_message": None,
            "transfer_percent": None,
            "can_pull_to_hub": True,
            "show_open_hub": False,
        }

    if montage_ready:
        return {
            "fleet_stage": "ready",
            "fleet_stage_label": "Готов к монтажу",
            "error_message": None,
            "transfer_percent": None,
            "can_pull_to_hub": not is_hub_node,
            "show_open_hub": is_hub_node,
        }

    return {
        "fleet_stage": "generating",
        "fleet_stage_label": "Генерация на воркере",
        "error_message": None,
        "transfer_percent": None,
        "can_pull_to_hub": False,
        "show_open_hub": False,
    }


def _fleet_pipeline_project_row(
    project: Project,
    *,
    queue_pos: int | None,
    montage_queued: bool,
    transfer_by_id: dict[int, dict],
    transfer_by_slug: dict[str, dict],
    is_hub_node: bool,
) -> dict:
    meta = project.meta or {}
    status = project.status
    status_str = status.value if hasattr(status, "value") else str(status or "unknown")
    ready_at = meta.get("montage_ready_at")
    ui = _fleet_montage_ui(
        project,
        montage_queued=montage_queued,
        queue_pos=queue_pos,
        transfer_by_id=transfer_by_id,
        transfer_by_slug=transfer_by_slug,
        is_hub_node=is_hub_node,
    )
    return {
        "id": project.id,
        "slug": project.slug,
        "topic": project.topic,
        "status": status_str,
        "montage_ready": bool(meta.get("montage_ready"))
        or status in bundle_svc.MONTAGE_READY_STATUSES,
        "montage_ready_at": _json_safe(ready_at),
        "fleet_montage_deferred": bool(meta.get("fleet_montage_deferred")),
        "montage_handoff_pending": bool(
            meta.get("fleet_montage_deferred")
            and meta.get("montage_ready")
            and not meta.get("fleet_handoff_complete")
        ),
        "fleet_handoff_complete": bool(meta.get("fleet_handoff_complete")),
        "montage_queued": montage_queued,
        "montage_queue_position": queue_pos,
        "send_to_main_pc": send_to_main_pc_for_project(project),
        "fleet_source_node": _json_safe(meta.get("fleet_source_node")),
        "fleet_source_project_id": meta.get("fleet_source_project_id"),
        **ui,
    }


@router.get("/local/pipeline")
async def local_pipeline(_auth: AgentAuth = None) -> dict:
    try:
        from app.fleet.self_node import is_local_fleet_node, self_fleet_node

        self_node = await self_fleet_node()
        is_hub = bool(self_node and (self_node.is_main or settings.fleet_montage_hub))
        transfer_by_id, transfer_by_slug = _build_transfer_index()
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Project)
                    .order_by(Project.updated_at.desc())
                    .limit(50)
                )
            ).scalars().all()
            queued = await queued_projects_ordered(session)
            queue_map = {row.id: idx for idx, row in enumerate(queued, start=1)}
            projects: list[dict] = []
            for project in rows:
                try:
                    meta = project.meta or {}
                    montage_queued = (
                        bool(meta.get(META_ENQUEUED))
                        and project.status == ProjectStatus.music_ready
                    )
                    queue_pos = queue_map.get(project.id) if montage_queued else None
                    projects.append(
                        _fleet_pipeline_project_row(
                            project,
                            queue_pos=queue_pos,
                            montage_queued=montage_queued,
                            transfer_by_id=transfer_by_id,
                            transfer_by_slug=transfer_by_slug,
                            is_hub_node=is_hub,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "fleet local_pipeline skip project #{}: {}",
                        getattr(project, "id", "?"),
                        exc,
                    )
                    projects.append(
                        {
                            "id": getattr(project, "id", 0),
                            "slug": getattr(project, "slug", "?"),
                            "topic": getattr(project, "topic", None),
                            "status": "error",
                            "montage_ready": False,
                            "montage_queued": False,
                            "send_to_main_pc": True,
                            "pipeline_error": str(exc)[:200],
                        }
                    )
        return {"projects": projects, "count": len(projects)}
    except Exception as exc:  # noqa: BLE001
        logger.exception("fleet local_pipeline failed")
        raise HTTPException(status_code=500, detail=f"local_pipeline: {exc}") from exc


@router.get("/local/files")
async def local_files(path: str = ".", _auth: AgentAuth = None) -> dict:
    root = _pipeline_root()
    target = _safe_path(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    if target.is_file():
        return {
            "type": "file",
            "path": _rel_path(root, target),
            "size": target.stat().st_size,
        }
    entries = []
    for child in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        entries.append(
            {
                "name": child.name,
                "type": "dir" if child.is_dir() else "file",
                "size": child.stat().st_size if child.is_file() else None,
            }
        )
    return {"type": "dir", "path": _rel_path(root, target), "entries": entries}


@router.get("/local/files/download")
async def local_files_download(path: str, _auth: AgentAuth = None):
    root = _pipeline_root()
    target = _safe_path(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(
        path=target,
        filename=target.name,
        media_type="application/octet-stream",
    )


@router.get("/local/files/content")
async def local_files_content(path: str, _auth: AgentAuth = None) -> dict:
    root = _pipeline_root()
    target = _safe_path(root, path)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="file not found")
    size = target.stat().st_size
    ext = target.suffix.lower()
    if ext not in _TEXT_VIEW_EXTENSIONS and size > 0:
        raise HTTPException(status_code=415, detail="binary file — use download")
    if size > _MAX_VIEW_BYTES:
        raise HTTPException(status_code=413, detail=f"file too large (max {_MAX_VIEW_BYTES} bytes)")
    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = target.read_text(encoding="utf-8", errors="replace")
    return {
        "path": _rel_path(root, target),
        "size": size,
        "content": text,
        "encoding": "utf-8",
    }


@router.delete("/local/files")
async def local_delete_file(path: str, _auth: AgentAuth = None) -> dict:
    root = _pipeline_root()
    target = _safe_path(root, path)
    if not target.exists():
        raise HTTPException(status_code=404, detail="not found")
    if target.is_dir():
        import shutil

        shutil.rmtree(target)
    else:
        target.unlink()
    return {"ok": True}


@router.post("/local/files/upload")
async def local_upload_file(
    path: str,
    file: UploadFile = File(...),
    _auth: AgentAuth = None,
) -> dict:
    root = _pipeline_root()
    target = _safe_path(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    target.write_bytes(content)
    return {"ok": True, "path": _rel_path(root, target), "size": len(content)}


@router.get("/local/logs/stream")
async def local_pipeline_logs_stream(_auth: AgentAuth = None):
    return StreamingResponse(
        _pipeline_log_stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/local/powershell/stream")
async def local_powershell_stream(body: PowerShellRun, _auth: AgentAuth = None):
    return StreamingResponse(
        _powershell_stream_events(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/local/powershell")
async def local_powershell(body: PowerShellRun, _auth: AgentAuth = None) -> dict:
    if platform.system() != "Windows":
        raise HTTPException(status_code=400, detail="powershell only on Windows")
    cwd = body.cwd or str(_pipeline_root())
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            body.command,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=body.timeout_sec,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": proc.stdout[-8000:] if proc.stdout else "",
        "stderr": proc.stderr[-4000:] if proc.stderr else "",
    }


@router.post("/local/projects/{project_id}/mark-montage-ready")
async def local_mark_montage_ready(project_id: int, _auth: AgentAuth = None) -> dict:
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        project.meta = bundle_svc.mark_montage_ready(project.meta)
        await session.commit()
        return {"ok": True, "project_id": project_id, "slug": project.slug}


@router.get("/local/projects/{project_id}/export-bundle")
async def local_export_bundle(project_id: int, _auth: AgentAuth = None):
    import asyncio

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        data_dir = project.data_dir.resolve()
        meta = dict(project.meta or {})
        manifest = {
            "slug": project.slug,
            "topic": project.topic,
            "status": project.status.value
            if hasattr(project.status, "value")
            else str(project.status),
            "meta": meta,
        }
        slug = project.slug
        pid = project.id
        ready_at = str(meta.get("montage_ready_at") or "")

    try:
        bundle_path, filename, from_cache = await asyncio.to_thread(
            bundle_svc.get_or_build_bundle_file,
            project_id=pid,
            slug=slug,
            data_dir=data_dir,
            manifest=manifest,
            ready_at=ready_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    filename = f"{slug}-fleet-bundle.tar.gz"
    total = bundle_path.stat().st_size
    prog: dict[str, int | float] = {"last_pct": -1, "last_ts": 0.0}

    async def _stream() -> AsyncIterator[bytes]:
        from app.fleet.transfer_state import check_transfer_cancelled

        sent = 0
        logger.info("[#{}] fleet SEND hub START ({:.0f} MB)", pid, total / (1024 * 1024))
        with bundle_path.open("rb") as fh:
            while chunk := fh.read(8 * 1024 * 1024):
                check_transfer_cancelled(pid)
                sent += len(chunk)
                from app.fleet.client import _log_transfer_progress

                _log_transfer_progress(
                    f"[#{pid}] fleet SEND hub",
                    sent,
                    total,
                    state=prog,
                    direction="send",
                    phase="send",
                    project_id=pid,
                )
                yield chunk
        logger.info("[#{}] fleet SEND hub DONE", pid)

    return StreamingResponse(
        _stream(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/local/projects/{project_id}/handoff-complete")
async def local_handoff_complete(
    project_id: int,
    body: HandoffCompleteBody | None = None,
    _auth: AgentAuth = None,
) -> dict:
    """Agent: hub подтвердил забор/push — снять handoff, не тянуть снова auto-pull."""
    from app.fleet.montage_handoff import mark_handoff_complete

    via = (body.via if body else "pull") or "pull"
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        await mark_handoff_complete(session, project, via=via)
        await session.commit()
        return {"ok": True, "project_id": project_id, "slug": project.slug}


@router.post("/local/projects/{project_id}/push-to-hub")
async def local_push_to_hub(project_id: int, _auth: AgentAuth = None) -> dict:
    """Agent: упаковать и отправить bundle на hub (без pull/Fleet UI)."""
    from app.fleet.push_to_hub import push_project_bundle_to_hub

    logger.info("[#{}] ▶ push-to-hub START (смотри upload XX% ниже)", project_id)
    try:
        result = await push_project_bundle_to_hub(project_id)
        logger.info("[#{}] ✓ push-to-hub DONE: {}", project_id, result)
        return result
    except ValueError as exc:
        logger.error("[#{}] ✗ push-to-hub FAIL: {}", project_id, exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/import-bundle")
async def import_bundle_upload(
    file: UploadFile = File(...),
    run_assemble: bool = Form(True),
    source_node: str = Form(""),
    source_project_id: int = Form(0),
    authorization: str | None = Header(None),
) -> dict:
    """Hub: принять bundle от agent (push, без WEB auth — fleet token)."""
    _check_agent_token(authorization)
    if (settings.fleet_role or "hub").strip().lower() != "hub":
        raise HTTPException(status_code=400, detail="import-bundle only on hub")
    if not settings.fleet_montage_hub:
        raise HTTPException(status_code=400, detail="FLEET_MONTAGE_HUB=false")

    import tempfile

    fd, tmp_name = tempfile.mkstemp(suffix=".tar.gz", prefix="fleet-import-")
    os.close(fd)
    bundle_path = Path(tmp_name)
    prog: dict[str, int | float] = {"last_pct": -1, "last_ts": 0.0}
    try:
        total = 0
        expected = int(getattr(file, "size", None) or 0)
        logger.info(
            "▶ fleet import-bundle RECEIVE START from {} project #{} ({:.0f} MB expected)",
            source_node or "?",
            source_project_id or "?",
            expected / (1024 * 1024) if expected else 0,
        )
        if source_project_id:
            from app.fleet.transfer_state import update_fleet_transfer

            await update_fleet_transfer(
                source_project_id,
                phase="receive",
                direction="to_hub",
                percent=0,
                total_mb=expected / (1024 * 1024) if expected else 0,
                message=f"Приём bundle с {source_node or 'agent'}…",
                source_node=source_node or "",
            )
        with bundle_path.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                if source_project_id:
                    from app.fleet.transfer_state import check_transfer_cancelled

                    check_transfer_cancelled(source_project_id)
                out.write(chunk)
                total += len(chunk)
                if expected:
                    from app.fleet.client import _log_transfer_progress

                    _log_transfer_progress(
                        f"[#{source_project_id}] fleet import",
                        total,
                        expected,
                        state=prog,
                        direction="receive",
                        phase="receive",
                    )
                elif total % (100 * 1024 * 1024) < len(chunk):
                    logger.info(
                        "[#{}] fleet import receive {:.0f} MB...",
                        source_project_id,
                        total / (1024 * 1024),
                    )
        logger.info(
            "✓ fleet import-bundle RECEIVE DONE {} MB from {} (#{})",
            round(total / (1024 * 1024), 1),
            source_node or "?",
            source_project_id or "?",
        )
        async with session_scope() as session:
            project = await bundle_svc.import_project_bundle_file(
                session, bundle_path, run_assemble=False
            )
            meta = dict(project.meta or {})
            if source_node:
                meta["fleet_source_node"] = source_node
            if source_project_id:
                meta["fleet_source_project_id"] = source_project_id
            project.meta = meta
            queued = False
            if run_assemble:
                queued = await enqueue_for_montage(
                    session, project, source_node=source_node or "agent"
                )
                await process_montage_queue(session)
            await session.commit()
            if source_project_id:
                from app.fleet.transfer_state import update_fleet_transfer

                await update_fleet_transfer(
                    source_project_id,
                    phase="done",
                    direction="to_hub",
                    percent=100,
                    total_mb=total / (1024 * 1024),
                    sent_mb=total / (1024 * 1024),
                    message=f"Принято на hub → #{project.id} {project.slug}",
                    source_node=source_node or "",
                    slug=project.slug,
                    status="done",
                )
                await update_fleet_transfer(
                    project.id,
                    phase="done",
                    direction="to_hub",
                    percent=100,
                    message=f"Импортирован с {source_node or 'agent'}",
                    slug=project.slug,
                    status="done",
                )
            if source_project_id and source_node:
                token = settings.fleet_agent_token or ""
                node = (
                    await session.execute(
                        select(FleetNode).where(FleetNode.name == source_node)
                    )
                ).scalar_one_or_none()
                agent_url = node.base_url if node else ""
                if agent_url:
                    try:
                        from app.fleet.client import agent_post

                        await agent_post(
                            agent_url,
                            token,
                            f"/api/fleet/local/projects/{source_project_id}/handoff-complete",
                            json_body={"via": "push"},
                            timeout_sec=30,
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "[#{}] handoff-complete notify agent failed: {}",
                            source_project_id,
                            exc,
                        )
        return {
            "ok": True,
            "project_id": project.id,
            "slug": project.slug,
            "queued": queued,
            "bytes": total,
        }
    finally:
        bundle_path.unlink(missing_ok=True)


@router.get("/transfers/active")
async def fleet_transfers_active() -> dict:
    """Активные передачи bundle — для UI (канвас, fleet panel)."""
    from app.fleet.transfer_state import list_active_transfers

    return {"transfers": list_active_transfers()}


@router.get("/config")
async def fleet_config() -> dict:
    public = settings.fleet_public_url or settings.fleet_agent_base_url
    localhost_public = public.startswith("http://127.0.0.1") or public.startswith(
        "http://localhost"
    )
    return {
        "enabled": settings.fleet_enabled,
        "role": settings.fleet_role,
        "is_main": settings.fleet_is_main,
        "hub_url": settings.fleet_hub_url,
        "node_name": settings.fleet_node_name or platform.node(),
        "montage_hub": settings.fleet_montage_hub,
        "hub_is_worker": settings.fleet_hub_is_worker,
        "self_node": self_node_name(),
        "public_url": public,
        "public_url_localhost": localhost_public,
        "auth_required": settings.web_auth_enabled,
        "montage_max_parallel": settings.fleet_montage_max_parallel,
    }
