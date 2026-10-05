"""Resume, never resubmit: durable guard around paid Kie createTask (native pipeline).

State lives in the append-only ``provider_task_ledger`` (see spend_guard). Before a createTask an
``intent`` row (status ``submit_unknown``) is written and awaited; once the provider answers it is
resolved to ``bound`` (task id received) or ``rejected`` (not accepted / never sent). A later call
for the same (project, frame key, model, prompt hash) inside ``MEDIA_RESUME_WINDOW_S``:

* finds an open task (submitted, pending, succeeded but not downloaded) -> polls that task id;
* finds an unresolved intent -> ``KieSubmitUnknownError`` (no automatic resubmit, a human decides);
* finds nothing, or only failed / downloaded tasks -> a new submit is allowed.

Kie has no list endpoint, so an unresolved intent cannot be proven either way by the provider.
Recording is awaited (crash safety) but a database error never blocks the provider call.
Outsee exposes no task id to the client, so it cannot be resumed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from loguru import logger

from app.services import media_spend, spend_guard

_RESUMABLE_KEY = re.compile(r"^(frame|clip|hero|predmet)_")
_NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout, ConnectionRefusedError, socket.gaierror)


@dataclass(frozen=True)
class Ctx:
    project_id: int | None
    key: str
    model: str
    api: str
    ph: str

    @property
    def usable(self) -> bool:
        return self.project_id is not None and bool(self.key) and bool(self.model) and enabled()


def window_s() -> int:
    try:
        from app.settings import settings
        return int(settings.media_resume_window_s)
    except Exception:  # noqa: BLE001
        return 3600


def enabled() -> bool:
    return window_s() > 0 and not media_spend._managed() and media_spend._enabled("kie")


def _urlish(v: Any) -> bool:
    if isinstance(v, str):
        return v.startswith(("http://", "https://", "data:"))
    return isinstance(v, list) and bool(v) and all(_urlish(x) for x in v)


def prompt_hash(model: str, payload: dict | None) -> str:
    """Stable over restarts: reference urls are re-uploaded each time, so url-valued fields are left out."""
    inp = payload.get("input") if isinstance((payload or {}).get("input"), dict) else (payload or {})
    stable = {k: v for k, v in inp.items() if not _urlish(v)}
    raw = json.dumps({"m": model, "i": stable}, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def make_ctx(project_id: int | None, out_path: Any, model: str, api: str, payload: dict | None) -> Ctx:
    key = media_spend._task_key(out_path)
    if not _RESUMABLE_KEY.match(key):
        key = ""
    # only calls that name their project are resumable (music, the Create tab and other ad-hoc calls are not)
    return Ctx(project_id, key, model, api, prompt_hash(model, payload))


async def _append(fn, **kw) -> bool:
    try:
        from app.db import SessionLocal
        async with SessionLocal() as s:
            await fn(s, **kw)
            await s.commit()
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("media_resume: ledger write failed ({}): {}", fn.__name__, type(e).__name__)
        return False


def _scope(ctx: Ctx) -> dict:
    return {"project_id": ctx.project_id, "key": ctx.key, "model": ctx.model, "api": ctx.api}


async def _states(ctx: Ctx) -> list[dict]:
    from app.db import SessionLocal
    since = datetime.utcnow() - timedelta(seconds=window_s())
    async with SessionLocal() as s:
        found = await spend_guard.find_task_states(s, project_id=ctx.project_id, key=ctx.key,
                                                   model=ctx.model, since=since)
    return [t for t in found if t["ph"] == ctx.ph]


async def begin(ctx: Ctx) -> str | None:
    """Task id to resume, or None when a new submit is allowed. Raises KieSubmitUnknownError if an
    earlier submit of the same request has no known outcome."""
    if not ctx.usable:
        return None
    await media_spend.drain()  # poll rows of a previous attempt must be visible
    try:
        states = await _states(ctx)
    except Exception as e:  # noqa: BLE001
        logger.warning("media_resume: ledger read failed: {}", type(e).__name__)
        return None
    open_tasks = [t for t in states if not t["intent"] and t["status"] in spend_guard.OPEN_TASK_STATUSES]
    if open_tasks:
        return max(open_tasks, key=lambda t: t["first_at"])["task_id"]
    unresolved = [t for t in states if t["intent"] and t["status"] == "submit_unknown"]
    if unresolved:
        from app.bots.kie_http import KieSubmitUnknownError
        until = (max(t["first_at"] for t in unresolved) + timedelta(seconds=window_s())).strftime("%H:%M:%S UTC")
        raise KieSubmitUnknownError(
            f"{ctx.key}: прошлый submit в Kie без известного исхода (задача могла быть принята и оплачена). "
            f"Повторный запуск запрещён до {until}. Проверьте задачи и баланс Kie; если списания не было, "
            f"снимите блокировку (media_resume.release_unknown)",
            context={"intent_ids": [t["task_id"] for t in unresolved], "frame_key": ctx.key})
    return None


def classify_submit_error(exc: BaseException) -> str:
    """not_sent (safe to retry) | rejected (provider answered, nothing accepted) | unknown (may be billed)."""
    if isinstance(exc, _NOT_SENT):
        return "not_sent"
    if isinstance(exc, (httpx.HTTPError, OSError, asyncio.TimeoutError, TimeoutError)):
        return "unknown"
    ctx = getattr(exc, "context", None)
    ctx = ctx if isinstance(ctx, dict) else {}
    if ctx.get("no_task_id") or ctx.get("submit_unknown"):
        return "unknown"
    if ctx.get("provider_code") == 504:
        return "unknown"
    return "rejected"


async def submit(ctx: Ctx, create: Callable[[], Awaitable[str]]) -> str:
    """Run ``create`` (the createTask POST) under an intent row. Post-send failures become KieSubmitUnknownError."""
    from app.bots.kie_http import KieSubmitUnknownError
    usable = ctx.usable
    intent = uuid.uuid4().hex[:16]
    if usable:
        await _append(spend_guard.append_intent, **_scope(ctx), intent_id=intent, ph=ctx.ph)
    try:
        task_id = await create()
    except asyncio.CancelledError:
        raise  # intent stays unresolved: the POST may have gone out
    except Exception as exc:
        kind = classify_submit_error(exc)
        if kind != "unknown":
            if usable:
                await _append(spend_guard.resolve_intent, **_scope(ctx), intent_id=intent, status="rejected",
                              note=kind)
            raise
        if isinstance(exc, KieSubmitUnknownError):
            raise
        raise KieSubmitUnknownError(
            f"{ctx.key}: createTask без ответа ({type(exc).__name__}); задача могла быть принята и оплачена, "
            f"повторный submit не делаю", context={"frame_key": ctx.key, "exc_type": type(exc).__name__}) from exc
    if usable:
        await _append(spend_guard.append_task_event, project_id=ctx.project_id, key=ctx.key, task_id=task_id,
                      provider="kie", api=ctx.api, model=ctx.model, event="bound", status="submitted",
                      note=f"ph={ctx.ph} intent={intent}")
        await _append(spend_guard.resolve_intent, **_scope(ctx), intent_id=intent, status="bound")
    return task_id


async def mark(ctx: Ctx, task_id: str, status: str, note: str = "") -> None:
    """Terminal or progress row for a task whose poll rows are not written by media_spend (Kling client)."""
    if not ctx.usable:
        return
    await media_spend.drain()
    await _append(spend_guard.append_task_event, project_id=ctx.project_id, key=ctx.key, task_id=task_id,
                  provider="kie", api=ctx.api, model=ctx.model, event=status[:16], status=status, note=note)


async def mark_downloaded(ctx: Ctx, task_id: str) -> None:
    await mark(ctx, task_id, "downloaded")


async def release_unknown(project_id: int, key: str) -> int:
    """Human action: the provider showed no charge for this key, so a new submit is allowed.
    Resolves every unresolved intent of the key (any model) as rejected; returns how many."""
    from app.db import SessionLocal
    since = datetime.utcnow() - timedelta(seconds=window_s())
    async with SessionLocal() as s:
        found = await spend_guard.find_task_states(s, project_id=project_id, key=key, model=None, since=since)
        todo = [t for t in found if t["intent"] and t["status"] == "submit_unknown"]
        for t in todo:
            await spend_guard.resolve_intent(
                s, project_id=project_id, key=key, model=t["model"], api=t["api"],
                intent_id=t["task_id"][len(spend_guard.INTENT_PREFIX):], status="rejected", note="released")
        await s.commit()
    return len(todo)


def _have_result_file(directory: Path, key: str) -> bool:
    from app.services.scan_frames import is_valid_scene_image
    pat = re.compile(rf"^{re.escape(key)}_[0-9a-f]{{8}}\.\w+$")
    try:
        for p in directory.iterdir():
            if pat.match(p.name):
                if key.startswith("frame_"):
                    if is_valid_scene_image(p):
                        return True
                elif p.stat().st_size >= 1024:
                    return True
    except OSError:
        pass
    return False


async def _reattach_one(project, t: dict, key: str, stats: dict) -> None:
    from app.bots import kie_http
    api, task_id = t["api"] or "jobs", t["task_id"]
    ctx = Ctx(project.id, key, t["model"], api, t["ph"])
    stats["checked"] += 1
    state, inner = await asyncio.wait_for(kie_http.task_status(api, task_id), 30)
    if state == "pending":
        stats["pending"] += 1  # the step resumes polling this task id through begin()
        return
    if state == "fail":
        await mark(ctx, task_id, "failed", "reconcile: provider fail")
        stats["failed"] += 1
        return
    directory = project.data_dir / ("scenes" if key.startswith("frame_") else "videos")
    if _have_result_file(directory, key):
        await mark(ctx, task_id, "downloaded", "reconcile: file present")
        return
    urls = kie_http._result_media_urls(inner)
    if not urls:
        return
    ext = kie_http._url_ext(urls[0]) or (".png" if key.startswith("frame_") else ".mp4")
    out = directory / f"{key}_{uuid.uuid4().hex[:8]}{ext}"
    await kie_http.download(urls[0], out)
    if key.startswith("frame_"):
        from app.services.scan_frames import is_valid_scene_image
        if not is_valid_scene_image(out):
            out.unlink(missing_ok=True)
            return  # stays succeeded: the step retries the download
    await mark(ctx, task_id, "downloaded", "reconcile: downloaded")
    stats["downloaded"] += 1


async def reconcile_on_startup(budget_s: float = 90.0) -> dict:
    """One-shot, before the worker re-queues: for projects found in generating_images/videos reattach
    ledger tasks that are open. One status request per task, results downloaded; never submits."""
    stats = {"checked": 0, "downloaded": 0, "failed": 0, "pending": 0, "errors": 0}
    if not enabled():
        return stats

    async def _run() -> None:
        from sqlalchemy import select
        from app.db import SessionLocal
        from app.models import Project, ProjectStatus
        since = datetime.utcnow() - timedelta(seconds=window_s())
        async with SessionLocal() as s:
            projects = (await s.scalars(select(Project).where(Project.status.in_(
                [ProjectStatus.generating_images, ProjectStatus.generating_videos])))).all()
            work: list[tuple[Any, dict, str]] = []
            for pr in projects:
                await spend_guard.ensure_task_schema(s)
                keys = (await s.scalars(select(spend_guard.ProviderTaskLedger.key).where(
                    spend_guard.ProviderTaskLedger.project_id == pr.id,
                    spend_guard.ProviderTaskLedger.created_at >= since).distinct())).all()
                for key in keys:
                    if not _RESUMABLE_KEY.match(key or ""):
                        continue
                    for t in await spend_guard.find_task_states(s, project_id=pr.id, key=key, model=None, since=since):
                        if not t["intent"] and t["status"] in spend_guard.OPEN_TASK_STATUSES:
                            work.append((pr, t, key))
        for pr, t, key in work:
            try:
                await _reattach_one(pr, t, key, stats)
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                logger.warning("media_resume reconcile: task {} skipped ({})", t["task_id"], type(e).__name__)

    try:
        await asyncio.wait_for(_run(), budget_s)
    except Exception as e:  # noqa: BLE001
        stats["errors"] += 1
        logger.warning("media_resume reconcile aborted: {}", type(e).__name__)
    if stats["checked"]:
        logger.warning("media_resume reconcile: {}", stats)
    return stats
