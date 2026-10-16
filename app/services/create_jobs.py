"""Фоновые jobs Create (Outsee / Kie): очередь ожидания + параллельный пул.

Каждый Generate → отдельный job_id.
status=queued пока ждёт слот семафора провайдера;
status=processing когда реально ушёл в API
(Outsee ≤ CREATE_MAX_PARALLEL_OUTSEE).
"""

from __future__ import annotations

import asyncio
import itertools
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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
    started_at: str | None = None
    finished_at: str | None = None
    elapsed_sec: int | None = None
    history_id: str = ""
    # позиция в очереди ожидания (1-based), None если уже processing/done
    queue_position: int | None = None

    def to_dict(self) -> dict[str, Any]:
        from app.services.generation_storage import elapsed_from_iso, format_elapsed_min_sec

        live_sec = self.elapsed_sec
        if live_sec is None and self.status in {"queued", "processing"}:
            start_iso = self.created_at or self.started_at
            live_sec = elapsed_from_iso(start_iso, live=True)

        label = (
            format_elapsed_min_sec(live_sec)
            if live_sec is not None
            else None
        )
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
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_sec": live_sec,
            "elapsed_label": label,
            "queue_position": self.queue_position,
            "prompt_preview": (self.prompt or "")[:120],
        }


_JOBS: dict[str, CreateJob] = {}
_LOCK = asyncio.Lock()
# Сильные ссылки на tasks.
_TASKS: set[asyncio.Task[Any]] = set()
_TASKS_BY_JOB: dict[str, asyncio.Task[Any]] = {}
# Отдельный семафор на провайдера.
_SEMS: dict[str, asyncio.Semaphore] = {}
_SEM_SIZES: dict[str, int] = {}
# Анти-даблклик.
_RECENT_FP: dict[str, tuple[str, float]] = {}
_DEDUP_WINDOW_S = 2.5

_MAX_PARALLEL_CAP = 16



CREATE_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS create_jobs (
    job_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    seq INTEGER NOT NULL DEFAULT 0,
    media TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    path TEXT NOT NULL,
    prompt TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    preview_url TEXT,
    raw_url TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    elapsed_sec INTEGER,
    history_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_create_jobs_status ON create_jobs (status);
CREATE INDEX IF NOT EXISTS ix_create_jobs_created_at ON create_jobs (created_at);
"""

_JOB_COLUMNS = ("job_id", "owner", "seq", "media", "model", "provider", "path", "prompt", "status",
                "preview_url", "raw_url", "error", "created_at", "started_at", "finished_at",
                "elapsed_sec", "history_id", "updated_at")
_STORE_STATUS = {"processing": "running"}
_LOAD_STATUS = {"running": "processing", "cancelled": "failed", "interrupted": "failed", "error": "failed"}
INTERRUPTED_ERROR_RU = (
    "Сервер был перезапущен, пока задача не завершилась. Автоматически она не повторяется "
    "(повтор платный): проверьте кабинет провайдера и историю, при необходимости запустите заново."
)
_RESTORE_LIMIT = 200
_RETENTION_DAYS = 30

_OWNER = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
_SEQ = itertools.count(1)
_DB_LOCK = threading.Lock()
_ENSURED: set[str] = set()
_RESTORED: set[str] = set()
_PERSIST_STATS: dict[str, Any] = {"written": 0, "skipped": 0, "last_error": ""}


def persistence_enabled() -> bool:
    return (os.environ.get("VP_CREATE_JOBS_DB") or "1").strip().lower() not in {"0", "false", "no", "off"}


def persistence_stats() -> dict[str, Any]:
    with _DB_LOCK:
        return dict(_PERSIST_STATS)


def _db_path() -> Path:
    p = Path(settings.sqlite_path)
    return p if p.is_absolute() else Path.cwd() / p


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5)
    con.execute("PRAGMA busy_timeout=5000")
    if str(path) not in _ENSURED:
        con.executescript(CREATE_JOBS_DDL)
        _ENSURED.add(str(path))
    return con


def _now_utc() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


def _clip(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    from app.services.redact import redact_text

    s = redact_text(str(value))
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _snapshot(job: "CreateJob", store_status: str | None = None) -> dict[str, Any]:
    status = store_status or _STORE_STATUS.get(job.status, job.status)
    return {
        "job_id": job.id, "owner": _OWNER, "seq": next(_SEQ), "media": job.media, "model": job.model,
        "provider": job.provider, "path": str(job.path), "prompt": _clip(job.prompt, 2000) or "",
        "status": status, "preview_url": job.preview_url, "raw_url": _clip(job.raw_url, 1000),
        "error": _clip(job.error, 500), "created_at": job.created_at, "started_at": job.started_at,
        "finished_at": job.finished_at, "elapsed_sec": job.elapsed_sec, "history_id": job.history_id or "",
        "updated_at": _now_utc(),
    }


def _persist_sync(snap: dict[str, Any]) -> bool:
    if not persistence_enabled():
        return False
    try:
        cols = ", ".join(_JOB_COLUMNS)
        marks = ", ".join(f":{c}" for c in _JOB_COLUMNS)
        sets = ", ".join(f"{c}=excluded.{c}" for c in _JOB_COLUMNS if c not in ("job_id", "created_at"))
        with _DB_LOCK:
            con = _connect()
            try:
                con.execute(
                    f"INSERT INTO create_jobs ({cols}) VALUES ({marks}) "
                    f"ON CONFLICT(job_id) DO UPDATE SET {sets} "
                    f"WHERE excluded.owner != create_jobs.owner OR excluded.seq >= create_jobs.seq",
                    snap,
                )
                con.commit()
            finally:
                con.close()
            _PERSIST_STATS["written"] += 1
        return True
    except Exception as exc:
        with _DB_LOCK:
            _PERSIST_STATS["skipped"] += 1
            _PERSIST_STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        logger.error("create_jobs: запись состояния {} пропущена: {}: {}", snap.get("job_id"),
                     type(exc).__name__, exc)
        return False


async def _persist(job: "CreateJob", store_status: str | None = None) -> bool:
    try:
        return _persist_sync(_snapshot(job, store_status))
    except Exception:
        logger.exception("create_jobs: сбой записи состояния {}", job.id)
        return False


def restore_jobs() -> int:
    if not persistence_enabled():
        return 0
    key = str(_db_path())
    if key in _RESTORED:
        return 0
    _RESTORED.add(key)
    loaded = 0
    try:
        with _DB_LOCK:
            con = _connect()
            try:
                con.row_factory = sqlite3.Row
                cutoff = (datetime.now(timezone.utc) - timedelta(days=_RETENTION_DAYS)).replace(tzinfo=None).isoformat(sep=" ")
                con.execute("DELETE FROM create_jobs WHERE status IN ('done','failed','cancelled','interrupted') "
                            "AND updated_at < ?", (cutoff,))
                rows = con.execute(
                    "SELECT * FROM create_jobs WHERE status IN ('queued','running') "
                    "UNION SELECT * FROM (SELECT * FROM create_jobs ORDER BY created_at DESC LIMIT ?)",
                    (_RESTORE_LIMIT,),
                ).fetchall()
                interrupted: list[dict[str, Any]] = []
                for r in rows:
                    d = dict(r)
                    if d["job_id"] in _JOBS:
                        continue
                    status = d["status"]
                    error = d["error"]
                    finished = d["finished_at"]
                    if status in ("queued", "running") and d["owner"] != _OWNER:
                        status, error = "interrupted", INTERRUPTED_ERROR_RU
                        finished = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
                        con.execute(
                            "UPDATE create_jobs SET status='interrupted', error=?, finished_at=?, updated_at=?, "
                            "seq=seq+1 WHERE job_id=? AND status IN ('queued','running')",
                            (error, finished, _now_utc(), d["job_id"]),
                        )
                        interrupted.append(d)
                    job = CreateJob(
                        id=d["job_id"], media=d["media"], model=d["model"], provider=d["provider"],
                        path=Path(d["path"]), prompt=d["prompt"] or "",
                        status=_LOAD_STATUS.get(status, status), preview_url=d["preview_url"],
                        raw_url=d["raw_url"], error=error, created_at=d["created_at"],
                        started_at=d["started_at"], finished_at=finished, elapsed_sec=d["elapsed_sec"],
                        history_id=d["history_id"] or "",
                    )
                    _JOBS[job.id] = job
                    loaded += 1
                con.commit()
            finally:
                con.close()
        for d in interrupted:
            try:
                update_sidecar(Path(d["path"]), status="failed", error=INTERRUPTED_ERROR_RU)
            except Exception:
                logger.debug("create_jobs: sidecar прерванной задачи {} не обновлён", d["job_id"], exc_info=True)
        if interrupted:
            logger.warning("create_jobs: после перезапуска помечено прерванными задач: {}", len(interrupted))
    except Exception as exc:
        _RESTORED.discard(key)
        with _DB_LOCK:
            _PERSIST_STATS["last_error"] = f"restore: {type(exc).__name__}: {exc}"[:300]
        logger.error("create_jobs: восстановление из БД не удалось: {}: {}", type(exc).__name__, exc)
    return loaded


def _job_fingerprint(
    *,
    media: str,
    model: str,
    provider: str,
    prompt: str,
    params: dict[str, Any] | None,
) -> str:
    p = params or {}
    values = p.get("values") if isinstance(p.get("values"), dict) else {}
    nonce = str(p.get("nonce") or p.get("batch_index") or values.get("_nonce") or "")
    return "|".join(
        [
            (provider or "").strip().lower(),
            (media or "").strip().lower(),
            (model or "").strip().lower(),
            (prompt or "").strip(),
            str(p.get("aspect") or ""),
            str(p.get("resolution") or ""),
            str(p.get("duration") or ""),
            str(bool(p.get("generate_audio"))),
            str(bool(p.get("has_first_frame"))),
            str(bool(p.get("has_last_frame"))),
            nonce,
        ]
    )


def max_parallel(provider: str | None = None) -> int:
    """Лимит одновременных Create-jobs для провайдера (outsee=5, grsai=10)."""
    p = (provider or "").strip().lower()
    fallback = int(getattr(settings, "create_max_parallel", 5) or 5)
    if p == "outsee":
        n = int(getattr(settings, "create_max_parallel_outsee", fallback) or fallback)
    else:
        n = fallback
    return max(1, min(n, _MAX_PARALLEL_CAP))


def _semaphore(provider: str) -> asyncio.Semaphore:
    """Ленивый семафор на provider; пересоздаём при смене лимита."""
    key = (provider or "outsee").strip().lower() or "outsee"
    size = max_parallel(key)
    if key not in _SEMS or _SEM_SIZES.get(key) != size:
        _SEMS[key] = asyncio.Semaphore(size)
        _SEM_SIZES[key] = size
    return _SEMS[key]


def get_job(job_id: str) -> CreateJob | None:
    restore_jobs()
    return _JOBS.get(job_id)


def _refresh_queue_positions() -> None:
    """Позиции в ожидании — отдельно по каждому провайдеру."""
    by_provider: dict[str, list[CreateJob]] = {}
    for j in _JOBS.values():
        if j.status == "queued":
            by_provider.setdefault(j.provider, []).append(j)
        else:
            j.queue_position = None
    for waiting in by_provider.values():
        waiting.sort(key=lambda j: j.created_at)
        for i, j in enumerate(waiting, start=1):
            j.queue_position = i


def list_active_jobs(*, provider: str | None = None) -> list[CreateJob]:
    restore_jobs()
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
        "max_parallel": max_parallel(provider),
        "max_parallel_outsee": max_parallel("outsee"),
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
    restore_jobs()
    fp = _job_fingerprint(
        media=media, model=model, provider=provider, prompt=prompt, params=params
    )
    now = datetime.now(timezone.utc).timestamp()
    async with _LOCK:
        stale = [k for k, (_, ts) in _RECENT_FP.items() if now - ts > _DEDUP_WINDOW_S]
        for k in stale:
            _RECENT_FP.pop(k, None)
        prev = _RECENT_FP.get(fp)
        if prev is not None:
            prev_id, prev_ts = prev
            if now - prev_ts <= _DEDUP_WINDOW_S:
                existing = _JOBS.get(prev_id)
                if existing is not None and existing.status in {"queued", "processing"}:
                    logger.info(
                        "create_job.dedup id={} fp={} (даблклик / повтор за {:.2f}s)",
                        existing.id,
                        fp[:80],
                        now - prev_ts,
                    )
                    return existing

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
        _RECENT_FP[fp] = (job_id, now)
        _refresh_queue_positions()
    await _persist(job)

    logger.info(
        "create_job.enqueued id={} provider={} media={} model={} wait_pos={} parallel_cap={}",
        job.id,
        provider,
        media,
        model,
        job.queue_position,
        max_parallel(provider),
    )

    task = asyncio.create_task(
        _run_job(job, run=run, params=params, quote=quote),
        name=f"create-job-{job_id}",
    )
    _TASKS.add(task)
    _TASKS_BY_JOB[job_id] = task

    def _on_done(t: asyncio.Task[Any]) -> None:
        _TASKS.discard(t)
        _TASKS_BY_JOB.pop(job_id, None)

    task.add_done_callback(_on_done)
    return job


async def cancel_job(job_id: str) -> bool:
    """Отменить выполнение или ожидание задачи Create по ID, history_id или имени файла."""
    from app.services.generation_storage import (
        generations_root,
        invalidate_generation_list_cache,
        update_sidecar,
    )

    restore_jobs()
    clean = job_id.removeprefix("gen-").strip()
    target_job: CreateJob | None = None
    target_jid: str | None = None

    async with _LOCK:
        for jid, j in list(_JOBS.items()):
            if (
                jid == job_id
                or jid == clean
                or j.history_id == job_id
                or j.history_id == f"gen-{clean}"
                or j.path.name == clean
                or j.path.stem == Path(clean).stem
                or str(j.path) == job_id
            ):
                target_job = j
                target_jid = jid
                break

    if target_job and target_jid:
        task = _TASKS_BY_JOB.get(target_jid)
        if task and not task.done():
            task.cancel()

        target_job.status = "failed"
        target_job.error = "Генерация остановлена пользователем"
        target_job.finished_at = (
            datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        )
        update_sidecar(
            target_job.path,
            status="failed",
            error=target_job.error,
            finished_at=target_job.finished_at,
        )
        invalidate_generation_list_cache()
        _refresh_queue_positions()
        await _persist(target_job, store_status="cancelled")
        logger.info("create_job.cancelled id={} (matched job)", target_jid)
        return True

    # Если задачи нет в памяти (например, после перезапуска сервера), ищем sidecar на диске
    root = generations_root()
    found = False
    stem = Path(clean).stem
    for pattern in (f"{stem}*.json", f"{clean}*.json"):
        for sp in root.rglob(pattern):
            if sp.is_file():
                try:
                    update_sidecar(
                        sp,
                        status="failed",
                        error="Генерация остановлена пользователем",
                        finished_at=datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                    )
                    found = True
                except Exception:  # noqa: BLE001
                    pass

    if found:
        invalidate_generation_list_cache()
        _refresh_queue_positions()
        logger.info("create_job.cancelled on disk: {}", job_id)
        return True

    return False


async def _run_job(
    job: CreateJob,
    *,
    run: GenerateFn,
    params: dict[str, Any] | None,
    quote: dict[str, Any] | None,
) -> None:
    import shutil

    from app.services.generation_storage import generations_root

    # Ждём свободный слот провайдера — пока status=queued (UI: «ожидание»)
    async with _semaphore(job.provider):
        job.status = "processing"
        job.queue_position = None
        job.started_at = (
            datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        )
        t0 = asyncio.get_running_loop().time()
        _refresh_queue_positions()
        update_sidecar(job.path, status="processing", started_at=job.started_at)
        await _persist(job)
        logger.info(
            "create_job.start id={} provider={} media={} model={} (slot acquired, cap={})",
            job.id,
            job.provider,
            job.media,
            job.model,
            max_parallel(job.provider),
        )
        try:
            from app.services.api_tracker_hook import check_api_allowed
            check_api_allowed(provider=job.provider, model=job.model)

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
            job.finished_at = (
                datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            )
            job.elapsed_sec = max(0, int(round(asyncio.get_running_loop().time() - t0)))
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
                started_at=job.started_at,
                finished_at=job.finished_at,
                elapsed_sec=job.elapsed_sec,
            )
            if not job.path.is_file() or job.path.stat().st_size < 32:
                raise RuntimeError("Create job: sidecar ok, но media-файл пропал")
            await _persist(job)
            from app.services.generation_storage import format_elapsed_min_sec

            logger.info(
                "create_job.done id={} path={} bytes={} elapsed={}",
                job.id,
                job.path,
                job.path.stat().st_size,
                format_elapsed_min_sec(job.elapsed_sec),
            )
            # Запись в API Tracker
            try:
                from app.services.api_tracker_hook import record_api_call
                cost_usd = None
                if quote and isinstance(quote, dict) and "usd" in quote:
                    try:
                        cost_usd = float(quote["usd"])
                    except (ValueError, TypeError):
                        pass
                record_api_call(
                    provider=job.provider,
                    model=job.model,
                    call_type=job.media,
                    media_count=1,
                    duration_sec=float(job.elapsed_sec or 0),
                    cost_usd=cost_usd,
                    status_code=200,
                    project_source="create_workspace",
                    metadata={"prompt": (job.prompt or "")[:500], "job_id": job.id},
                )
            except Exception as e:
                logger.debug("api_tracker record failed: {}", e)
        except asyncio.CancelledError:
            job.status = "failed"
            job.error = "Генерация отменена пользователем"
            job.finished_at = (
                datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            )
            job.elapsed_sec = max(0, int(round(asyncio.get_running_loop().time() - t0)))
            update_sidecar(
                job.path,
                status="failed",
                error=job.error,
                started_at=job.started_at,
                finished_at=job.finished_at,
                elapsed_sec=job.elapsed_sec,
            )
            _persist_sync(_snapshot(job, "cancelled"))
            logger.info("create_job.cancelled_handled id={}", job.id)
            raise
        except Exception as e:  # noqa: BLE001
            job.status = "failed"
            job.error = str(getattr(e, "reason", None) or e)[:500]
            job.finished_at = (
                datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
            )
            job.elapsed_sec = max(0, int(round(asyncio.get_running_loop().time() - t0)))
            update_sidecar(
                job.path,
                status="failed",
                error=job.error,
                started_at=job.started_at,
                finished_at=job.finished_at,
                elapsed_sec=job.elapsed_sec,
            )
            await _persist(job)
            logger.exception("create_job.failed id={}", job.id)
            # Запись ошибки в API Tracker
            try:
                from app.services.api_tracker_hook import record_api_call
                record_api_call(
                    provider=job.provider,
                    model=job.model,
                    call_type=job.media,
                    media_count=1,
                    duration_sec=float(job.elapsed_sec or 0),
                    status_code=500,
                    error_message=str(job.error or "")[:300],
                    project_source="create_workspace",
                    metadata={"prompt": (job.prompt or "")[:500], "job_id": job.id},
                )
            except Exception:
                pass
        finally:
            _refresh_queue_positions()
