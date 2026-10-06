"""Best-effort spend recording for native paid media/audio provider calls.

Hooks wrap the narrowest submit seam of each provider (Kie createTask/poll, Outsee
API generate, ElevenLabs POST). Every helper here is fire-and-forget: it never raises
into the caller, never awaits the database on the request path (a local insert runs in a
background task) and never changes the provider call, its retries or its result.

Recorded in ``spend_ledger`` (kind ``actual``): estimated cost from the Kie catalog
(1 credit = CREDIT_USD) where the model is priced, otherwise a zero-cost request row with
``cost_unknown=true`` in the note. Kie task ids go to the append-only
``provider_task_ledger``. HTTP 402 / credit exhaustion engages the supervisory kill switch
(``spend_guard.engage_on_credit_error``) and writes an audit row; native work is not stopped.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import os
import re
from contextlib import contextmanager
from typing import Any

# UNCALIBRATED: upper-end published overage rate per character; replace after a measured top-up.
ELEVENLABS_USD_PER_CHAR = 0.0003
# UNCALIBRATED: ~40 credits per second of generated effect at the same per-credit rate.
ELEVENLABS_SFX_USD_PER_SEC = 0.012

TEST_FORCE = False  # tests set True; otherwise recording is off under pytest (no writes to the dev DB)

_tasks: set = set()
_kie_ctx: contextvars.ContextVar[dict | None] = contextvars.ContextVar("media_spend_kie", default=None)
_polls: contextvars.ContextVar[list | None] = contextvars.ContextVar("media_spend_polls", default=None)

_DEFINITE_REJECT = {400, 401, 402, 403, 404, 405, 409, 413, 422, 429, 433, 451}


# -- resume-only scope --------------------------------------------------------
# A supervisory recovery of a paid media node may only RESUME work that a provider already has
# (poll a known task id, download a finished file, reuse a finished local file). Inside this
# scope every seam that would send a NEW paid request refuses before any byte leaves the process.

_resume_only: contextvars.ContextVar[bool] = contextvars.ContextVar("media_resume_only", default=False)


class ResumeOnlyBlocked(RuntimeError):
    """A new paid submit was requested inside a resume-only scope. Nothing was sent.

    ``no_resubmit`` makes the video/image ladders stop instead of burning an attempt."""

    def __init__(self, provider: str, what: str = "submit"):
        super().__init__(
            f"{provider}: resume-only scope: a new paid request ({what}) is refused; "
            f"only an already known task may be polled or downloaded")
        self.context = {"no_resubmit": True, "resume_only": True, "provider": provider,
                        "request_sent": False}


_resume_hits: contextvars.ContextVar[list | None] = contextvars.ContextVar("media_resume_only_hits", default=None)


@contextmanager
def resume_only(hits: list | None = None):
    """Refuse every new paid provider request (Kie createTask, Kling, Outsee, ElevenLabs) inside.

    ``hits`` (optional) collects one entry per refused request: the proof that a local re-run reached no paid seam."""
    token = _resume_only.set(True)
    hits_token = _resume_hits.set(hits)
    try:
        yield
    finally:
        _resume_hits.reset(hits_token)
        _resume_only.reset(token)


def in_resume_only() -> bool:
    return _resume_only.get()


def assert_submit_allowed(provider: str, what: str = "submit") -> None:
    if _resume_only.get():
        hits = _resume_hits.get()
        if hits is not None:
            hits.append({"provider": provider, "what": what})
        raise ResumeOnlyBlocked(provider, what)


def _refuse_coro(coro, provider: str, what: str) -> None:
    """Raise ResumeOnlyBlocked for an already built provider coroutine without leaving it un-awaited."""
    try:
        assert_submit_allowed(provider, what)
    except ResumeOnlyBlocked:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise


# -- plumbing -----------------------------------------------------------------

_lock_for: tuple = (None, None)


async def _serialized(coro) -> None:
    """Writes run one at a time in submission order, so task events keep their real sequence."""
    global _lock_for
    loop = asyncio.get_running_loop()
    if _lock_for[0] is not loop:
        _lock_for = (loop, asyncio.Lock())
    async with _lock_for[1]:
        await coro


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(_serialized(coro))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception:  # noqa: BLE001
        coro.close()


async def drain() -> None:
    """Wait for pending background writes (tests, shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _project_id() -> int | None:
    try:
        from app.services.observation import _cost_project
        return _cost_project.get()
    except Exception:  # noqa: BLE001
        return None


def _enabled(provider: str) -> bool:
    try:
        if not TEST_FORCE and "PYTEST_CURRENT_TEST" in os.environ:
            return False
        if provider == "kie":
            from app.bots.kie_kling import kie_api_key
            key = kie_api_key()
        elif provider == "outsee":
            from app.bots.outsee_http import outsee_api_key
            key = outsee_api_key()
        else:
            from app.settings import settings
            key = settings.elevenlabs_api_key
        key = str(key or "").strip()
        return bool(key) and not key.startswith("synthetic")
    except Exception:  # noqa: BLE001
        return False


def _status_of(exc: BaseException) -> int | None:
    ctx = getattr(exc, "context", None)
    if isinstance(ctx, dict):
        for k in ("provider_code", "status_code", "status"):
            v = ctx.get(k)
            try:
                if v is not None:
                    return int(v)
            except (TypeError, ValueError):
                pass
    m = re.search(r"\b(?:HTTP |sfx |code=)(\d{3})\b", str(exc))
    return int(m.group(1)) if m else None


def is_credit_error(exc: BaseException) -> bool:
    ctx = getattr(exc, "context", None)
    if isinstance(ctx, dict) and ctx.get("credits_exhausted"):
        return True
    return _status_of(exc) == 402 or "402 payment required" in str(exc).lower()


class _CreditShim(RuntimeError):
    def __init__(self, provider: str):
        super().__init__("HTTP 402 payment required")
        self.context = {"status_code": 402, "provider": provider, "credits_exhausted": True}


async def _write(project_id, provider, usd, note, credits=0.0, settle_from=None) -> None:
    try:
        from app.db import SessionLocal
        from app.services import spend_guard
        async with SessionLocal() as s:
            if settle_from is not None:
                await spend_guard.settle(s, project_id=project_id, run_id="", reserved_usd=settle_from,
                                         actual_usd=usd, source="media_native", provider=provider)
            else:
                await spend_guard.record_actual(s, project_id=project_id, usd=usd, provider=provider,
                                                source="media_native", note=note, credits=credits)
            await s.commit()
    except Exception:  # noqa: BLE001
        pass


async def _credit_stop(project_id, provider, note) -> None:
    try:
        from app.db import SessionLocal
        from app.services import spend_guard
        await spend_guard.engage_on_credit_error(_CreditShim(provider), provider=provider)
        async with SessionLocal() as s:
            await spend_guard.record_event(s, project_id=project_id, provider=provider,
                                           source="media_402", note=note)
            await s.commit()
    except Exception:  # noqa: BLE001
        pass


async def _write_task(**kw) -> None:
    try:
        from app.db import SessionLocal
        from app.services import spend_guard
        async with SessionLocal() as s:
            await spend_guard.append_task_event(s, **kw)
            await s.commit()
    except Exception:  # noqa: BLE001
        pass


def record(provider: str, *, usd: float, note: str, credits: float = 0.0, unknown: bool = False,
           exc: BaseException | None = None) -> None:
    """Schedule one ledger row; on a credit error also engage the kill switch. Never raises."""
    try:
        if not _enabled(provider):
            return
        pid = _project_id()
        if unknown:
            note = f"{note} cost_unknown=true"
        _spawn(_write(pid, provider, max(0.0, float(usd)), note, credits))
        if exc is not None and is_credit_error(exc):
            _spawn(_credit_stop(pid, provider, f"{provider} 402: {note}"[:200]))
    except Exception:  # noqa: BLE001
        pass


# -- Kie ----------------------------------------------------------------------

def price_kie(spec: dict | None, payload: dict | None) -> dict:
    """Catalog estimate for one submit; ``unknown`` when the model has no price."""
    out = {"model": str((spec or {}).get("id") or ""), "credits": 0.0, "usd": 0.0, "unknown": True}
    try:
        from app.services import kie_catalog
        if not spec or not spec.get("pricing"):
            return out
        values = payload.get("input") if isinstance((payload or {}).get("input"), dict) else (payload or {})
        est = kie_catalog.estimate_credits(spec, values)
        if float(est["credits"]) > 0:
            out.update(credits=float(est["credits"]), usd=float(est["usd"]), unknown=False)
    except Exception:  # noqa: BLE001
        pass
    return out


def _task_key(out_path) -> str:
    try:
        stem = os.path.splitext(os.path.basename(str(out_path)))[0]
        return re.sub(r"_[0-9a-f]{8}$", "", stem)
    except Exception:  # noqa: BLE001
        return ""


@contextmanager
def kie_scope(spec: dict | None, payload: dict | None, out_path=None):
    """Carries price and frame/clip key from run_generation to the create/poll hooks."""
    try:
        ctx = {**price_kie(spec, payload), "key": _task_key(out_path), "api": str((spec or {}).get("api") or "")}
    except Exception:  # noqa: BLE001
        ctx = None
    token = _kie_ctx.set(ctx)
    try:
        yield
    finally:
        _kie_ctx.reset(token)


def _managed() -> bool:
    try:
        from app.services.media_transport import current_job
        return current_job.get() is not None
    except Exception:  # noqa: BLE001
        return False


def tracked_kie_create(fn):
    """Decorator for kie_http.create_task: record cost estimate + task id, 402 -> kill switch."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        assert_submit_allowed("kie", "createTask")
        if _managed():
            return await fn(*args, **kwargs)
        api = str(args[0] if args else kwargs.get("api", ""))
        try:
            task_id = await fn(*args, **kwargs)
        except BaseException as exc:
            _after_create_failed(api, exc)
            raise
        _after_create_ok(api, task_id)
        return task_id
    return wrapper


def _after_create_ok(api: str, task_id: str) -> None:
    try:
        ctx = _kie_ctx.get() or {}
        model = ctx.get("model", "")
        record("kie", usd=ctx.get("usd", 0.0), credits=ctx.get("credits", 0.0),
               unknown=bool(ctx.get("unknown", True)), note=f"create {api} {model} task={task_id}")
        if _enabled("kie"):
            _spawn(_write_task(project_id=_project_id(), key=ctx.get("key", ""), task_id=task_id,
                               provider="kie", api=api, model=model, event="submitted", status="submitted",
                               est_credits=None if ctx.get("unknown", True) else ctx.get("credits"),
                               est_usd=None if ctx.get("unknown", True) else ctx.get("usd")))
    except Exception:  # noqa: BLE001
        pass


def _after_create_failed(api: str, exc: BaseException) -> None:
    try:
        ctx = _kie_ctx.get() or {}
        status = _status_of(exc)
        definite = status in _DEFINITE_REJECT
        model = ctx.get("model", "")
        if definite:
            record("kie", usd=0.0, note=f"create {api} {model} rejected http={status}", exc=exc)
        else:  # timeout / transport / no taskId: the submit may have been accepted and billed
            record("kie", usd=ctx.get("usd", 0.0), credits=ctx.get("credits", 0.0),
                   unknown=bool(ctx.get("unknown", True)),
                   note=f"create {api} {model} uncertain {type(exc).__name__}", exc=exc)
    except Exception:  # noqa: BLE001
        pass


def count_poll() -> None:
    try:
        c = _polls.get()
        if c is not None:
            c[0] += 1
    except Exception:  # noqa: BLE001
        pass


def tracked_kie_poll(fn):
    """Decorator for kie_http.poll_task: append task status event, count polls as one request row."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        if _managed():
            return await fn(*args, **kwargs)
        api = str(args[0] if args else kwargs.get("api", ""))
        task_id = str(args[1] if len(args) > 1 else kwargs.get("task_id", ""))
        counter = [0]
        token = _polls.set(counter)
        try:
            result = await fn(*args, **kwargs)
        except BaseException as exc:
            ectx = getattr(exc, "context", None)
            kind = ectx.get("kind") if isinstance(ectx, dict) else None
            _after_poll(api, task_id, counter[0],
                        "failed" if kind == "generation" else "pending", f"poll {type(exc).__name__} {kind or ''}", exc)
            raise
        finally:
            _polls.reset(token)
        _after_poll(api, task_id, counter[0], "succeeded", "poll success", None)
        return result
    return wrapper


def _after_poll(api, task_id, polls, status, note, exc) -> None:
    try:
        ctx = _kie_ctx.get() or {}
        record("kie", usd=0.0, note=f"poll {api} {ctx.get('model', '')} task={task_id} polls={polls}", exc=exc)
        if _enabled("kie"):
            _spawn(_write_task(project_id=_project_id(), key=ctx.get("key", ""), task_id=task_id,
                               provider="kie", api=api, model=ctx.get("model", ""), event="poll",
                               status=status, note=f"{note} polls={polls}"))
    except Exception:  # noqa: BLE001
        pass


async def track_kling(coro, *, kie_id: str, duration, has_frame: bool):
    """Wrap the Kling fallback client call (own create/poll client) as one request row."""
    _refuse_coro(coro, "kie", "kling createTask")
    spec = None
    try:
        from app.services import kie_catalog
        spec = kie_catalog.get_model(f"{kie_id}-{'i2v' if has_frame else 't2v'}")
    except Exception:  # noqa: BLE001
        pass
    price = price_kie(spec, {"duration": duration or 5})
    try:
        result = await coro
    except BaseException as exc:
        if _status_of(exc) in _DEFINITE_REJECT:
            record("kie", usd=0.0, note=f"kling {kie_id} rejected http={_status_of(exc)}", exc=exc)
        else:
            record("kie", usd=price["usd"], credits=price["credits"], unknown=price["unknown"],
                   note=f"kling {kie_id} uncertain {type(exc).__name__}", exc=exc)
        raise
    record("kie", usd=price["usd"], credits=price["credits"], unknown=price["unknown"],
           note=f"kling {kie_id} task={getattr(result, 'gen_id', '')}")
    return result


# -- Outsee -------------------------------------------------------------------

def outsee_tracked(fn, media: str):
    """Wrap outsee_http.generate_image/generate_video: one request row per call, price unknown."""
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        assert_submit_allowed("outsee", media)
        model = str(kwargs.get("model_slug") or "")
        try:
            from app.services import outsee_guard
            guard = outsee_guard.make_ctx(args, kwargs, media)
        except Exception:  # noqa: BLE001 - the guard is an addition, never a reason to fail the call
            guard = None
        try:
            if guard is not None and guard.usable:
                result = await outsee_guard.run(guard, lambda: fn(*args, **kwargs))
            else:
                result = await fn(*args, **kwargs)
        except BaseException as exc:
            record("outsee", usd=0.0, unknown=True, exc=exc,
                   note=f"{media} {model} failed {type(exc).__name__}")
            raise
        record("outsee", usd=0.0, unknown=True, note=f"{media} {model} ok")
        return result
    return wrapper


# -- ElevenLabs ---------------------------------------------------------------

async def track_eleven_post(coro, *, chars: int, model: str = ""):
    """Await one TTS POST; record each attempt (retries and the no-proxy repeat are separate POSTs)."""
    _refuse_coro(coro, "elevenlabs", "tts post")
    est = chars * ELEVENLABS_USD_PER_CHAR
    try:
        resp = await coro
    except BaseException as exc:
        import httpx
        sent = not isinstance(exc, httpx.ConnectError)  # a refused connection never reached the API
        record("elevenlabs", usd=est if sent else 0.0, exc=exc,
               note=f"tts {model} chars={chars} {'uncertain ' if sent else ''}{type(exc).__name__}")
        raise
    try:
        if resp.status_code == 200:
            record("elevenlabs", usd=est, note=f"tts {model} chars={chars} uncalibrated")
            billed = resp.headers.get("character-cost")  # reported credits; absent header -> estimate stays
            if billed and _enabled("elevenlabs") and str(billed).isdigit() and int(billed) != chars:
                _spawn(_write(_project_id(), "elevenlabs", int(billed) * ELEVENLABS_USD_PER_CHAR, "",
                              settle_from=est))
        else:
            record("elevenlabs", usd=0.0, exc=_CreditShim("elevenlabs") if resp.status_code == 402 else None,
                   note=f"tts {model} http={resp.status_code} not billed")
    except Exception:  # noqa: BLE001
        pass
    return resp


def track_eleven_sfx(fn):
    """Decorator for sfx_gen._elevenlabs_sfx: one row per call, cost = seconds x constant."""
    @functools.wraps(fn)
    async def wrapper(prompt, duration, out_path):
        assert_submit_allowed("elevenlabs", "sfx post")
        est = min(max(float(duration), 0.5), 22.0) * ELEVENLABS_SFX_USD_PER_SEC
        try:
            result = await fn(prompt, duration, out_path)
        except BaseException as exc:
            if _status_of(exc) is not None or isinstance(exc, RuntimeError):  # got an HTTP answer: not billed
                record("elevenlabs", usd=0.0, exc=exc, note=f"sfx not billed {str(exc)[:60]}")
            else:
                record("elevenlabs", usd=est, exc=exc, note=f"sfx uncertain {type(exc).__name__}")
            raise
        record("elevenlabs", usd=est, note=f"sfx dur={duration} uncalibrated")
        return result
    return wrapper
