from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from loguru import logger

from app.services import media_resume, media_spend, spend_guard

API = "outsee"


@dataclass(frozen=True)
class Ctx:
    project_id: int | None
    key: str
    model: str
    media: str
    ph: str
    browser: bool = False

    @property
    def usable(self) -> bool:
        base = (isinstance(self.project_id, int) and bool(self.key) and media_resume.window_s() > 0
                and not media_spend._managed())
        if not base:
            return False
        if self.browser:
            return browser_enabled()
        return media_spend._enabled("outsee")


def browser_enabled() -> bool:
    reason = media_spend.disabled_reason("browser")
    if reason is not None:
        media_spend._count_skip("outsee", reason)
        return False
    return True


def make_ctx(args: tuple, kwargs: dict, media: str, *, browser: bool = False) -> Ctx:
    out_path = args[1] if len(args) > 1 else kwargs.get("out_path")
    key = media_spend._task_key(out_path) if out_path is not None else ""
    if not media_resume._RESUMABLE_KEY.match(key):
        key = ""
    prompt = args[0] if args else kwargs.get("prompt")
    model = str(kwargs.get("model_slug") or "")
    ph = hashlib.sha1(f"{model}|{prompt}".encode("utf-8", "replace")).hexdigest()[:16]
    pid = kwargs.get("project_id")
    return Ctx(pid if isinstance(pid, int) else None, key, model, media, ph, browser)


def make_browser_ctx(args: tuple, kwargs: dict, media: str, *, prompt_first: bool = True) -> Ctx:
    if prompt_first:
        return make_ctx(args, kwargs, media, browser=True)
    out_path = args[0] if args else kwargs.get("out_path")
    return make_ctx(("", out_path), {**kwargs, "prompt": ""}, media, browser=True)


async def _append(fn, **kw) -> bool:
    try:
        from app.db import SessionLocal
        async with SessionLocal() as s:
            await fn(s, **kw)
            await s.commit()
        return True
    except Exception as e:
        await media_spend.gap_async(API, "intent_row", project_id=kw.get("project_id"), error=e,
                                    detail=f"{fn.__name__} key={kw.get('key', '')} event={kw.get('event', '')}")
        return False


def _row(ctx: Ctx, intent: str, *, event: str, status: str, note: str) -> dict:
    return dict(project_id=ctx.project_id, key=ctx.key, task_id=spend_guard.intent_task_id(intent), provider=API,
                api=API, model=ctx.model, event=event, status=status, note=note)


async def _unresolved(ctx: Ctx) -> list[dict]:
    from app.db import SessionLocal
    since = datetime.utcnow() - timedelta(seconds=media_resume.window_s())
    async with SessionLocal() as s:
        found = await spend_guard.find_task_states(s, project_id=ctx.project_id, key=ctx.key, model=None, since=since)
    return [t for t in found if t["api"] == API and t["intent"] and t["status"] == "submit_unknown"]


async def begin(ctx: Ctx) -> None:
    if not ctx.usable:
        return
    await media_spend.drain()
    try:
        unresolved = await _unresolved(ctx)
    except Exception as e:
        await media_spend.gap_async(API, "intent_read", project_id=ctx.project_id, error=e, detail=f"key={ctx.key}")
        return
    if not unresolved:
        return
    from app.bots.outsee_http import OutseeSubmitUnknownError
    until = (max(t["first_at"] for t in unresolved) + timedelta(seconds=media_resume.window_s())).strftime("%H:%M:%S UTC")
    raise OutseeSubmitUnknownError(
        f"{ctx.key}: прошлый запрос в Outsee без известного исхода (задача могла быть принята и оплачена). "
        f"Повторная отправка запрещена до {until}. Проверьте историю генераций и баланс Outsee; "
        f"если списания не было, снимите блокировку (outsee_guard.release_unknown)",
        context={"intent_ids": [t["task_id"] for t in unresolved], "frame_key": ctx.key})


def classify_failure(exc: BaseException, probe: dict) -> str:
    from app.bots.outsee import OutseeDownloadError
    ctx = getattr(exc, "context", None)
    ctx = ctx if isinstance(ctx, dict) else {}
    if probe.get("done") or isinstance(exc, OutseeDownloadError):
        return "done"
    if probe.get("rejected"):
        return "rejected"
    if ctx.get("submit_unknown"):
        return "unknown"
    if probe.get("task_id") is None:
        return "unknown" if probe.get("maybe_sent") else "not_sent"
    if isinstance(ctx.get("status"), dict):
        return "rejected"
    return "unknown"


async def run(ctx: Ctx, call: Callable[[], Awaitable[Any]]) -> Any:
    if not ctx.usable:
        return await call()
    await begin(ctx)
    from app.bots import outsee_http
    from app.services.step_cancel import StepCancelledError
    intent = uuid.uuid4().hex[:16]
    await _append(spend_guard.append_task_event,
                  **_row(ctx, intent, event="intent", status="submit_unknown", note=f"ph={ctx.ph} {ctx.media}"))
    probe: dict = {"maybe_sent": False, "task_id": None, "done": False}
    token = outsee_http.submit_probe.set(probe)

    async def resolve(status: str, note: str) -> None:
        await _append(spend_guard.append_task_event,
                      **_row(ctx, intent, event="intent_done", status=status, note=note))

    try:
        result = await call()
    except (asyncio.CancelledError, StepCancelledError):
        if probe.get("in_flight"):
            probe["maybe_sent"] = True
        if probe["task_id"] is None and not probe["maybe_sent"]:
            await resolve("rejected", "cancelled before submit")
        elif probe["done"]:
            await resolve("done", "cancelled after result")
        raise
    except Exception as exc:
        kind = classify_failure(exc, probe)
        if isinstance(exc, outsee_http.OutseeSubmitUncertain):
            # main: POST мог дойти (таймаут чтения, ответ без id) — исход неизвестен, не отказ
            probe["maybe_sent"] = True
            kind = "unknown"
        if kind == "unknown":
            from app.bots.outsee_http import OutseeSubmitUnknownError
            if isinstance(exc, OutseeSubmitUnknownError):
                raise
            raise OutseeSubmitUnknownError(
                f"{ctx.key}: запрос в Outsee ушёл, но результата нет ({type(exc).__name__}); "
                f"задача могла быть принята и оплачена, повторную отправку не делаю",
                context={"frame_key": ctx.key, "exc_type": type(exc).__name__,
                         "task_id": probe.get("task_id")}) from exc
        await resolve("done" if kind == "done" else "rejected", f"{kind} {type(exc).__name__}")
        raise
    finally:
        outsee_http.submit_probe.reset(token)
    await resolve("bound", f"id={probe.get('task_id')}")
    return result


async def release_unknown(project_id: int, key: str) -> int:
    from app.db import SessionLocal
    since = datetime.utcnow() - timedelta(seconds=media_resume.window_s())
    async with SessionLocal() as s:
        found = await spend_guard.find_task_states(s, project_id=project_id, key=key, model=None, since=since)
        todo = [t for t in found if t["api"] == API and t["intent"] and t["status"] == "submit_unknown"]
        for t in todo:
            await spend_guard.append_task_event(
                s, project_id=project_id, key=key, task_id=t["task_id"], provider=API, api=API, model=t["model"],
                event="intent_done", status="rejected", note="released")
        await s.commit()
    return len(todo)


async def run_browser(media: str, call: Callable[[], Awaitable[Any]], args: tuple, kwargs: dict, *,
                      prompt_first: bool = True) -> Any:
    media_spend.assert_submit_allowed("outsee", f"browser {media}")
    try:
        ctx = make_browser_ctx(args, kwargs, media, prompt_first=prompt_first)
    except Exception:
        return await call()
    if not ctx.usable:
        return await call()
    model = ctx.model
    try:
        result = await run(ctx, call)
    except BaseException as exc:
        media_spend.record("outsee", usd=0.0, unknown=True, exc=exc,
                           note=f"browser {media} {model} failed {type(exc).__name__}")
        raise
    media_spend.record("outsee", usd=0.0, unknown=True, note=f"browser {media} {model} ok")
    return result
