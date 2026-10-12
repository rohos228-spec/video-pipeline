"""Outsee: no blind resubmit after a submit with an unknown outcome (native pipeline).

Outsee gives a generation id only inside the process, and nothing in the pipeline resumes by it, so
every new generate call is a new paid task. Around each Outsee API call (image/video) an ``intent``
row (status ``submit_unknown``, api ``outsee``) is written to the append-only ``provider_task_ledger``
before the POST, and resolved when the outcome is known:

* ``bound``     the generation finished (file result known);
* ``done``      the generation finished, a later step (download, postprocess) failed: no new Generate;
* ``rejected``  the provider refused the submit, declared the generation failed, or nothing was sent.

Anything else (the POST was on the wire but the answer was lost, the id is known but polling ended
without a result, the process was cancelled mid-flight) leaves the intent unresolved. A new call for the
same (project, frame key) inside ``MEDIA_RESUME_WINDOW_S`` then raises ``OutseeSubmitUnknownError``
(``no_resubmit``: the retry ladder, rewrite and step retries all stop) until a human releases it with
``release_unknown`` after checking the Outsee history and balance.

The browser UI path (``OutseeBot`` clicking Generate / «Повторить») is guarded the same way (``run_browser``):
the bot marks ``maybe_sent`` right before the click that creates a paid generation, ``done`` once the result
URL is known and ``rejected`` when the page itself reports a failed generation (moderation, length, error
banner). Anything between the click and one of those two verdicts (timeout, crash, cancel, "Generate did not
start") stays an unresolved intent and blocks a second click for ``MEDIA_RESUME_WINDOW_S``; a failure before
the click is not an unknown submit. The browser guard needs no API key (cookie/CDP-only setups are exactly
where it matters), only a project id and a resumable frame key.

Only intent rows are written, never task rows: the Kie reconciler reads task rows and must not see
Outsee ids. Recording is awaited (crash safety) but a database error never blocks the provider call.
"""
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
    """The browser path is guarded whenever recording is on: no API key needed. Off only by the explicit
    ``MEDIA_SPEND_RECORDING=false`` or when the ledger is not the configured database (counted and logged by
    ``media_spend``); never by ``PYTEST_CURRENT_TEST``."""
    reason = media_spend.disabled_reason("browser")
    if reason is not None:
        media_spend._count_skip("outsee", reason)
        return False
    return True


def make_ctx(args: tuple, kwargs: dict, media: str, *, browser: bool = False) -> Ctx:
    """``args`` are the positional arguments of ``generate_*(prompt, out_path, ...)``; for the browser path of a
    method without a prompt (``regenerate_image(out_path, ...)``) pass ``out_path`` first, see ``make_browser_ctx``."""
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
    """Ctx for an ``OutseeBot`` method (``self`` already removed from ``args``)."""
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
    except Exception as e:  # noqa: BLE001
        # Without the intent row a lost answer cannot be blocked from a second paid submit: leave a trace.
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
    """Raise OutseeSubmitUnknownError while an earlier submit of this frame has no known outcome."""
    if not ctx.usable:
        return
    await media_spend.drain()
    try:
        unresolved = await _unresolved(ctx)
    except Exception as e:  # noqa: BLE001
        # The guard cannot tell whether an earlier submit is unresolved; the call goes on, the gap is traced.
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
    """done | not_sent | rejected | unknown, from what actually reached the provider."""
    from app.bots.outsee import OutseeDownloadError
    ctx = getattr(exc, "context", None)
    ctx = ctx if isinstance(ctx, dict) else {}
    if probe.get("done") or isinstance(exc, OutseeDownloadError):
        return "done"
    if probe.get("rejected"):
        return "rejected"  # the page/provider itself declared this generation failed
    if ctx.get("submit_unknown"):
        return "unknown"
    if probe.get("task_id") is None:
        return "unknown" if probe.get("maybe_sent") else "not_sent"
    if isinstance(ctx.get("status"), dict):
        return "rejected"  # the provider reported this generation as failed
    return "unknown"  # id known, polling ended without a verdict: it may still finish and be billed


async def run(ctx: Ctx, call: Callable[[], Awaitable[Any]]) -> Any:
    """Run one Outsee generate call under an intent row."""
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
            probe["maybe_sent"] = True  # отмена во время POST: запрос мог уйти и быть оплачен, intent остаётся открытым
        if probe["task_id"] is None and not probe["maybe_sent"]:
            await resolve("rejected", "cancelled before submit")
        elif probe["done"]:
            await resolve("done", "cancelled after result")
        raise  # accepted but not finished: stays unresolved, the generation may be billed
    except Exception as exc:
        kind = classify_failure(exc, probe)
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
    """Human action: Outsee showed no charge for this key, so a new submit is allowed.
    Resolves every unresolved Outsee intent of the key as rejected; returns how many."""
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
    """Guard one ``OutseeBot`` browser/UI generate call (see module docstring). ``args`` exclude ``self``."""
    media_spend.assert_submit_allowed("outsee", f"browser {media}")  # resume-only scope: nothing new may be clicked
    try:
        ctx = make_browser_ctx(args, kwargs, media, prompt_first=prompt_first)
    except Exception:  # noqa: BLE001 - the guard is an addition, never a reason to fail the call
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
