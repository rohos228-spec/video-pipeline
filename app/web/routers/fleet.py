"""Fleet API: hub (главный ПК) + local (на каждой станции)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import platform
import subprocess
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, AsyncIterator

from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.fleet import bundle as bundle_svc
from app.fleet.client import (
    FleetAgentError,
    agent_delete,
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
    queue_position_for_project,
)
from app.fleet.self_node import (
    is_local_fleet_node,
    is_localhost_fleet_url,
    self_node_name,
)
from app.services.node_step_params import send_to_main_pc_for_project
from app.models import FleetNode, FleetNodeStatus, Project, ProjectStatus
from app.project_root import find_project_root
from app.settings import settings
from app.web.auth_sessions import AuthDep

router = APIRouter(prefix="/fleet", tags=["fleet"])


def _pipeline_root() -> Path:
    return find_project_root().resolve()


# ── Auth ─────────────────────────────────────────────────────────────────────

_agent_bearer = HTTPBearer(auto_error=False)


def _validate_agent_token_value(authorization: str | None) -> None:
    expected = (settings.fleet_agent_token or "").strip()
    if not expected:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    if token != expected:
        raise HTTPException(status_code=403, detail="invalid fleet token")


async def require_agent_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_agent_bearer),
) -> None:
    if credentials is None:
        _validate_agent_token_value(None)
        return
    _validate_agent_token_value(f"Bearer {credentials.credentials}")


AgentAuth = Annotated[None, Depends(require_agent_auth)]


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


class PushToHub(BaseModel):
    run_assemble: bool | None = None


def _node_out(n: FleetNode) -> FleetNodeOut:
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
        meta=n.meta or {},
    )


async def _get_node(session, node_id: int) -> FleetNode:
    node = await session.get(FleetNode, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="node not found")
    return node


# ── Hub: registry ────────────────────────────────────────────────────────────


@router.get("/nodes", response_model=list[FleetNodeOut])
async def list_nodes(_user: AuthDep = None) -> list[FleetNodeOut]:
    async with session_scope() as session:
        rows = (await session.execute(select(FleetNode).order_by(FleetNode.name))).scalars().all()
        return [_node_out(n) for n in rows]


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
    _validate_agent_token_value(authorization)
    async with session_scope() as session:
        node = (
            await session.execute(select(FleetNode).where(FleetNode.name == body.name))
        ).scalar_one_or_none()
        if node is None:
            is_main = body.is_main
            if (body.role or "").strip().lower() == "agent":
                is_main = False
            node = FleetNode(
                name=body.name,
                base_url=body.base_url.rstrip("/"),
                token=settings.fleet_agent_token or "",
                is_main=is_main,
                role=body.role,
            )
            session.add(node)
        else:
            node.hostname = body.hostname
            node.role = body.role
            node.is_main = body.is_main
        if (body.role or "").strip().lower() == "agent":
            node.is_main = False
        incoming_url = body.base_url.rstrip("/")
        role_agent = (body.role or "").strip().lower() == "agent"
        # Не перезаписывать рабочий Tailscale URL на 127.0.0.1 — hub тогда видит себя, не agent.
        if role_agent and is_localhost_fleet_url(incoming_url):
            if node.base_url and not is_localhost_fleet_url(node.base_url):
                logger.warning(
                    "fleet register {}: игнорируем localhost base_url, оставляем {}",
                    body.name,
                    node.base_url,
                )
            else:
                node.base_url = incoming_url
                logger.warning(
                    "fleet register {}: base_url={} — задайте FLEET_PUBLIC_URL=http://<tailscale-ip>:8765",
                    body.name,
                    node.base_url,
                )
        else:
            node.base_url = incoming_url
        node.status = FleetNodeStatus.online
        node.last_seen = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(node)
        return _node_out(node)


@router.post("/nodes/{node_id}/sync")
async def sync_node(node_id: int, _user: AuthDep = None) -> dict:
    async with session_scope() as session:
        node = await _get_node(session, node_id)
        if is_local_fleet_node(node):
            from app.web.studio_version import read_studio_version

            ver = read_studio_version()
            node.status = FleetNodeStatus.online
            node.last_seen = datetime.now(timezone.utc)
            node.hostname = platform.node()
            node.pipeline_version = ver.get("label") or str(ver.get("version"))
            await session.commit()
            return {
                "ok": True,
                "info": {
                    "name": node.name,
                    "hostname": platform.node(),
                    "local": True,
                    "studio_version": node.pipeline_version,
                },
            }
        info = await ping_agent(node.base_url, node.token or settings.fleet_agent_token)
        if info:
            node.status = FleetNodeStatus.online
            node.last_seen = datetime.now(timezone.utc)
            node.hostname = info.get("hostname")
            node.pipeline_version = info.get("studio_version")
        else:
            node.status = FleetNodeStatus.offline
        await session.commit()
        return {"ok": bool(info), "info": info}


# ── Hub → agent proxy ────────────────────────────────────────────────────────


async def _proxy_node(node_id: int) -> FleetNode:
    async with session_scope() as session:
        return await _get_node(session, node_id)


@router.get("/nodes/{node_id}/pipeline")
async def node_pipeline(node_id: int, _user: AuthDep = None) -> dict:
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return await local_pipeline()
    if is_localhost_fleet_url(node.base_url):
        raise HTTPException(
            status_code=502,
            detail=(
                f"Станция {node.name} зарегистрирована как {node.base_url}. "
                "На воркере задайте FLEET_PUBLIC_URL=http://<tailscale-ip>:8765 и перезапустите Studio."
            ),
        )
    token = node.token or settings.fleet_agent_token
    try:
        return await agent_get(node.base_url, token, "/api/fleet/local/pipeline")
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


@router.get("/nodes/{node_id}/files")
async def node_files(node_id: int, path: str = ".", _user: AuthDep = None) -> dict:
    node = await _proxy_node(node_id)
    if is_local_fleet_node(node):
        return await local_files(path=path)
    token = node.token or settings.fleet_agent_token
    try:
        return await agent_get(
            node.base_url, token, "/api/fleet/local/files", params={"path": path}
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


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
    token = node.token or settings.fleet_agent_token
    try:
        return await agent_get(
            node.base_url,
            token,
            "/api/fleet/local/files/content",
            params={"path": path},
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


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
        return await agent_upload_file(
            node.base_url,
            token,
            path,
            file.filename or Path(path).name,
            content,
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc


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

    token = node.token or settings.fleet_agent_token
    try:
        blob = await agent_get_bytes(
            node.base_url,
            token,
            f"/api/fleet/local/projects/{project_id}/export-bundle",
            timeout_sec=600,
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    if not blob:
        raise HTTPException(status_code=502, detail="empty bundle from agent")

    async with session_scope() as session:
        project = await bundle_svc.import_project_bundle(
            session, bytes(blob), run_assemble=False
        )
        meta = dict(project.meta or {})
        meta["fleet_source_node"] = node.name
        meta["fleet_source_project_id"] = project_id
        project.meta = meta
        queued = False
        if body.run_assemble:
            queued = await enqueue_for_montage(session, project, source_node=node.name)
            await process_montage_queue(session)
        await session.commit()
        return {"ok": True, "project_id": project.id, "slug": project.slug, "queued": queued}


@router.post("/import-bundle")
async def import_bundle_from_agent(
    file: UploadFile = File(...),
    run_assemble: bool = False,
    source_node: str | None = None,
    source_project_id: int | None = None,
    _auth: AgentAuth = None,
) -> dict:
    """Hub: принять bundle с agent (push-to-hub)."""
    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="empty bundle")
    async with session_scope() as session:
        project = await bundle_svc.import_project_bundle(session, blob, run_assemble=False)
        meta = dict(project.meta or {})
        meta["fleet_imported"] = True
        if source_node:
            meta["fleet_source_node"] = source_node
        if source_project_id is not None:
            meta["fleet_source_project_id"] = source_project_id
        project.meta = meta
        queued = False
        if run_assemble:
            queued = await enqueue_for_montage(session, project, source_node=source_node)
            await process_montage_queue(session)
        await session.commit()
        return {
            "ok": True,
            "project_id": project.id,
            "slug": project.slug,
            "queued": queued,
            "size_mb": round(len(blob) / (1024 * 1024), 2),
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
async def local_info() -> dict:
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


@router.get("/local/pipeline")
async def local_pipeline() -> dict:
    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Project)
                .order_by(Project.updated_at.desc())
                .limit(50)
            )
        ).scalars().all()
        projects = []
        for project in rows:
            meta = project.meta or {}
            montage_queued = bool(meta.get(META_ENQUEUED)) and project.status == ProjectStatus.music_ready
            queue_pos = (
                await queue_position_for_project(session, project) if montage_queued else None
            )
            projects.append(
                {
                    "id": project.id,
                    "slug": project.slug,
                    "topic": project.topic,
                    "status": project.status.value
                    if hasattr(project.status, "value")
                    else str(project.status),
                    "montage_ready": bool(meta.get("montage_ready"))
                    or project.status in bundle_svc.MONTAGE_READY_STATUSES,
                    "exportable": True,
                    "montage_queued": montage_queued,
                    "montage_queue_position": queue_pos,
                    "send_to_main_pc": send_to_main_pc_for_project(project),
                }
            )
    return {"projects": projects, "count": len(projects)}


@router.get("/local/files")
async def local_files(path: str = ".") -> dict:
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
async def local_files_download(path: str):
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
async def local_files_content(path: str) -> dict:
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
async def local_delete_file(path: str) -> dict:
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
) -> dict:
    root = _pipeline_root()
    target = _safe_path(root, path)
    target.parent.mkdir(parents=True, exist_ok=True)
    content = await file.read()
    target.write_bytes(content)
    return {"ok": True, "path": _rel_path(root, target), "size": len(content)}


@router.get("/local/logs/stream")
async def local_pipeline_logs_stream():
    return StreamingResponse(
        _pipeline_log_stream_events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/local/powershell/stream")
async def local_powershell_stream(body: PowerShellRun):
    return StreamingResponse(
        _powershell_stream_events(body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/local/powershell")
async def local_powershell(body: PowerShellRun) -> dict:
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
async def local_mark_montage_ready(project_id: int) -> dict:
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        project.meta = bundle_svc.mark_montage_ready(project.meta)
        await session.commit()
        return {"ok": True, "project_id": project_id, "slug": project.slug}


@router.get("/local/projects/{project_id}/export-bundle")
async def local_export_bundle(project_id: int):
    from fastapi.responses import Response

    async with session_scope() as session:
        blob, filename = await bundle_svc.export_project_bundle(session, project_id)
    return Response(
        content=blob,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/local/projects/{project_id}/push-to-hub")
async def local_push_to_hub(
    project_id: int,
    body: PushToHub | None = None,
) -> dict:
    """Agent: экспорт bundle на hub (любой статус проекта)."""
    hub = (settings.fleet_hub_url or "").strip().rstrip("/")
    if not hub:
        raise HTTPException(status_code=400, detail="FLEET_HUB_URL not configured")
    role = (settings.fleet_role or "hub").strip().lower()
    if role != "agent":
        raise HTTPException(status_code=400, detail="push-to-hub only on agent stations")

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        meta = project.meta or {}
        montage_ready = bool(meta.get("montage_ready")) or (
            project.status in bundle_svc.MONTAGE_READY_STATUSES
        )
        run_assemble = (
            body.run_assemble
            if body is not None and body.run_assemble is not None
            else montage_ready
        )
        blob, filename = await bundle_svc.export_project_bundle(session, project_id)

    token = settings.fleet_agent_token or ""
    source_node = settings.fleet_node_name or platform.node()
    qs = urllib.parse.urlencode(
        {
            "run_assemble": "true" if run_assemble else "false",
            "source_node": source_node,
            "source_project_id": str(project_id),
        }
    )
    try:
        result = await agent_upload_file(
            hub,
            token,
            f"/api/fleet/import-bundle?{qs}",
            file_bytes=blob,
            filename=filename,
            timeout_sec=600,
        )
    except FleetAgentError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is not None:
            handoff_meta = dict(project.meta or {})
            handoff_meta["fleet_handoff_complete"] = True
            handoff_meta["fleet_handoff_at"] = datetime.now(timezone.utc).isoformat()
            project.meta = handoff_meta
            await session.commit()

    size_mb = round(len(blob) / (1024 * 1024), 2)
    return {"ok": True, "size_mb": size_mb, **result}


@router.get("/config")
async def fleet_config() -> dict:
    return {
        "enabled": settings.fleet_enabled,
        "role": settings.fleet_role,
        "is_main": settings.fleet_is_main,
        "hub_url": settings.fleet_hub_url,
        "node_name": settings.fleet_node_name or platform.node(),
        "montage_hub": settings.fleet_montage_hub,
        "hub_is_worker": settings.fleet_hub_is_worker,
        "self_node": self_node_name(),
        "public_url": settings.fleet_public_url or settings.fleet_agent_base_url,
        "auth_required": settings.web_auth_enabled,
        "montage_max_parallel": settings.fleet_montage_max_parallel,
    }
