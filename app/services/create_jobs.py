"""Фоновые jobs Create (Outsee / Grsai): очередь ожидания + параллельный пул.

Каждый Generate → отдельный job_id.
status=queued пока ждёт слот семафора;
status=processing когда реально ушёл в API (слотов ≤ CREATE_MAX_PARALLEL).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from app.services.generation_storage import (
    build_generation_path,
    update_sidecar,
    write_sidecar,
)
from app.settings import settings

GenerateFn = Callable[[Path], Awaitable[Any]]


@dataclass
class CreateJob:
    id: str
    media: str
    model: str
    provider: str
    path: Path
    prompt: str
    status: str = "queued"  # queued | processing | done | failed
    preview_url: str | None = None
    raw_url: str | None = None
    error: str | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc)
        .astimezone()
        .isoformat(timespec="seconds")
    )
    history_id: str = ""
    # позиция в очереди ожидания (1-based), None если уже processing/done
    queue_position: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "ok": self.status != "failed",
            "status": self.status,
            "media": self.media,
            "model": self.model,
            "provider": self.provider,
            "path": str(self.path.resolve()),
            "history_id": self.history_id or f"gen-{self.path.name}",
            "preview_url": self.preview_url,
            "raw_url": self.raw_url,
            "error": self.error,
            "bytes": self.path.stat().st_size if self.path.is_file() else 0,
            "created_at": self.created_at,
            "queue_position": self.queue_position,
            "prompt_preview": (self.prompt or "")[:120],
        }


_JOBS: dict[str, CreateJob] = {}
_LOCK = asyncio.Lock()
_SEM: asyncio.Semaphore | None = None
_SEM_SIZE: int = 0


def max_parallel() -> int:
    n = int(getattr(settings, "create_max_parallel", 2) or 2)
    return max(1, min(n, 8))


def _semaphore() -> asyncio.Semaphore:
    """Ленивый семафор; пересоздаём если CREATE_MAX_PARALLEL изменился."""
    global _SEM, _SEM_SIZE
    size = max_parallel()
    if _SEM is None or _SEM_SIZE != size:
        _SEM = asyncio.Semaphore(size)
        _SEM_SIZE = size
    return _SEM


def get_job(job_id: str) -> CreateJob | None:
    return _JOBS.get(job_id)


def _refresh_queue_positions() -> None:
    waiting = sorted(
        (j for j in _JOBS.values() if j.status == "queued"),
        key=lambda j: j.created_at,
    )
    for i, j in enumerate(waiting, start=1):
        j.queue_position = i
    for j in _JOBS.values():
        if j.status != "queued":
            j.queue_position = None


def list_active_jobs(*, provider: str | None = None) -> list[CreateJob]:
    _refresh_queue_positions()
    out = [
        j
        for j in _JOBS.values()
        if j.status in {"queued", "processing"}
        and (provider is None or j.provider == provider)
    ]
    out.sort(key=lambda j: (0 if j.status == "processing" else 1, j.created_at))
    return out


def queue_snapshot(*, provider: str | None = None) -> dict[str, Any]:
    """Сводка для UI: running / waiting / лимит параллели."""
    active = list_active_jobs(provider=provider)
    running = [j for j in active if j.status == "processing"]
    waiting = [j for j in active if j.status == "queued"]
    return {
        "max_parallel": max_parallel(),
        "running_count": len(running),
        "waiting_count": len(waiting),
        "total_active": len(active),
        "running": [j.to_dict() for j in running],
        "waiting": [j.to_dict() for j in waiting],
        "jobs": [j.to_dict() for j in active],
    }


async def enqueue_generation(
    *,
    media: str,
    model: str,
    provider: str,
    prompt: str,
    ext: str,
    params: dict[str, Any] | None,
    quote: dict[str, Any] | None,
    run: GenerateFn,
) -> CreateJob:
    """Регистрирует job (queued) и стартует task — слот API берёт семафор."""
    job_id = uuid.uuid4().hex[:12]
    out_path = build_generation_path(media=media, model=model, ext=ext)
    history_id = f"gen-{out_path.name}"
    write_sidecar(
        out_path,
        media=media,
        model=model,
        prompt=prompt,
        params=params,
        quote=quote,
        provider=provider,
        status="queued",
        job_id=job_id,
        require_file=False,
    )
    job = CreateJob(
        id=job_id,
        media=media,
        model=model,
        provider=provider,
        path=out_path,
        prompt=prompt,
        status="queued",
        history_id=history_id,
    )
    async with _LOCK:
        _JOBS[job_id] = job
        _refresh_queue_positions()

    logger.info(
        "create_job.enqueued id={} provider={} media={} model={} wait_pos={} parallel_cap={}",
        job.id,
        provider,
        media,
        model,
        job.queue_position,
        max_parallel(),
    )

    asyncio.create_task(
        _run_job(job, run=run, params=params, quote=quote),
        name=f"create-job-{job_id}",
    )
    return job


async def _run_job(
    job: CreateJob,
    *,
    run: GenerateFn,
    params: dict[str, Any] | None,
    quote: dict[str, Any] | None,
) -> None:
    import shutil

    from app.services.generation_storage import generations_root

    # Ждём свободный слот — пока status=queued (видимо в UI как «ожидание»)
    async with _semaphore():
        job.status = "processing"
        job.queue_position = None
        _refresh_queue_positions()
        update_sidecar(job.path, status="processing")
        logger.info(
            "create_job.start id={} provider={} media={} model={} (slot acquired)",
            job.id,
            job.provider,
            job.media,
            job.model,
        )
        try:
            result = await run(job.path)
            final_path = Path(getattr(result, "file_path", job.path))
            if not final_path.is_file() or final_path.stat().st_size < 32:
                raise RuntimeError(
                    f"Create job: файл не сохранён на диск ({final_path})"
                )

            root = generations_root().resolve()
            try:
                final_path.resolve().relative_to(root)
                under_gens = True
            except ValueError:
                under_gens = False

            if not under_gens:
                dest = (
                    job.path
                    if job.path.suffix == final_path.suffix
                    else job.path.with_suffix(final_path.suffix)
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(final_path, dest)
                final_path = dest
            elif final_path != job.path:
                old_side = job.path.with_suffix(".json")
                if old_side.is_file() and not final_path.with_suffix(".json").is_file():
                    try:
                        old_side.replace(final_path.with_suffix(".json"))
                    except OSError:
                        pass

            job.path = final_path
            job.history_id = f"gen-{final_path.name}"
            job.raw_url = getattr(result, "raw_url", None)
            job.preview_url = f"/api/files?path={job.path.resolve()}"
            job.status = "done"
            write_sidecar(
                job.path,
                media=job.media,
                model=job.model,
                prompt=job.prompt,
                params=params,
                raw_url=job.raw_url,
                quote=quote,
                provider=job.provider,
                status="done",
                job_id=job.id,
            )
            if not job.path.is_file() or job.path.stat().st_size < 32:
                raise RuntimeError("Create job: sidecar ok, но media-файл пропал")
            logger.info(
                "create_job.done id={} path={} bytes={}",
                job.id,
                job.path,
                job.path.stat().st_size,
            )
        except Exception as e:  # noqa: BLE001
            job.status = "failed"
            job.error = str(getattr(e, "reason", None) or e)[:500]
            update_sidecar(job.path, status="failed", error=job.error)
            logger.exception("create_job.failed id={}", job.id)
        finally:
            _refresh_queue_positions()
