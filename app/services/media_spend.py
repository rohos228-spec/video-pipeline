"""Spend recording for native paid media/audio provider calls.

Hooks wrap the narrowest submit seam of each provider (Kie createTask/poll, Outsee
API generate, ElevenLabs POST). Every helper here is fire-and-forget: it never raises
into the caller, never awaits the database on the request path (a local insert runs in a
background task) and never changes the provider call, its retries or its result.

A failed record is NEVER silent (A08): the error is logged with its traceback, a counter grows
(``gap_count``) and a gap row (``spend_ledger`` kind ``event``, source ``write_failed``, the lost
amount in the note) is written; when even that cannot reach the database the gap goes to the
append-only file ``<data_dir>/spend_gaps.jsonl``, so the money can be restored by hand.

Estimate and fact are separate kinds in ``spend_ledger`` (A08): ``estimated`` is a price from the Kie
catalog (1 credit = CREDIT_USD), an uncalibrated constant, or a call whose cost is unknown
(``cost_unknown=true`` in the note); ``actual`` is a fact (a refused request or a poll that cost
nothing). A provider-reported amount arrives as a ``settle`` row (actual minus the estimate). Totals
still sum every kind, so the cap stays conservative.

Recording is switched by the explicit ``MEDIA_SPEND_RECORDING`` setting, never by ``PYTEST_CURRENT_TEST``
or by a ``synthetic*`` key (such a key only tags the row ``synthetic_key=true``). Kie task ids go to the append-only
``provider_task_ledger``. HTTP 402 / credit exhaustion engages the supervisory kill switch
(``spend_guard.engage_on_credit_error``) and writes an audit row; native work is not stopped.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from loguru import logger

# UNCALIBRATED: upper-end published overage rate per character; replace after a measured top-up.
ELEVENLABS_USD_PER_CHAR = 0.0003
# UNCALIBRATED: ~40 credits per second of generated effect at the same per-credit rate.
ELEVENLABS_SFX_USD_PER_SEC = 0.012

# Tests set True to skip only the "ledger database is the configured one" check (they pass their own
# SessionLocal). It is not a recording switch: the switch is settings.media_spend_recording.
TEST_FORCE = False

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


# -- gaps: a lost record must leave a trace ------------------------------------

_gap_lock = threading.Lock()
_gap_count = 0
_gap_recent: list[dict] = []
_skipped: dict[str, int] = {}
_GAP_RECENT_MAX = 200


def gap_count() -> int:
    """How many spend records were lost (and traced) since the process started."""
    return _gap_count


def recent_gaps() -> list[dict]:
    with _gap_lock:
        return list(_gap_recent)


def skipped_counts() -> dict[str, int]:
    """Calls not recorded on purpose (setting off, no key, ledger is not the configured DB), by reason."""
    with _gap_lock:
        return dict(_skipped)


def reset_gap_state() -> None:
    """Tests only."""
    global _gap_count
    with _gap_lock:
        _gap_count = 0
        _gap_recent.clear()
        _skipped.clear()
        _uncorrelated.clear()


def _gap_file() -> Path:
    from app.settings import settings
    return Path(settings.data_dir) / "spend_gaps.jsonl"


def _spill_gap(entry: dict) -> bool:
    """Last resort when the database cannot take even a gap row: append the lost row to a file."""
    try:
        path = _gap_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        return True
    except Exception:  # noqa: BLE001
        logger.exception("media_spend: gap file is not writable, the lost record is only in the log: {}", entry)
        return False


async def _write_gap_row(entry: dict) -> bool:
    if not (TEST_FORCE or ledger_target_ok()):
        return False  # not the configured database: the gap goes to the file instead of a foreign ledger
    try:
        from app.db import SessionLocal
        from app.services import spend_guard
        note = (f"write_failed {entry.get('what', '')} usd={float(entry.get('usd') or 0.0):.6f} "
                f"{entry.get('error_type', '')} {entry.get('detail', '')}")
        async with SessionLocal() as s:
            await spend_guard.record_event(s, project_id=entry.get("project_id"),
                                           provider=str(entry.get("provider") or ""),
                                           source="write_failed", note=note)
            await s.commit()
        return True
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).error("media_spend: gap row could not be written")
        return False


def _register_gap(provider: str, what: str, *, project_id=None, usd: float = 0.0,
                  error: BaseException | None = None, detail: str = "") -> dict:
    global _gap_count
    entry = {"provider": provider, "what": what, "project_id": project_id, "usd": float(usd or 0.0),
             "error_type": type(error).__name__ if error is not None else "", "detail": str(detail)[:80]}
    logger.opt(exception=error).error(
        "media_spend: record LOST ({} {} usd={} project={}): {}",
        provider, what, entry["usd"], project_id, entry["error_type"] or detail)
    with _gap_lock:
        _gap_count += 1
        _gap_recent.append(entry)
        del _gap_recent[:-_GAP_RECENT_MAX]
    return entry


async def gap_async(provider: str, what: str, *, project_id=None, usd: float = 0.0,
                    error: BaseException | None = None, detail: str = "") -> None:
    """Trace a lost record from async code. Never raises."""
    try:
        entry = _register_gap(provider, what, project_id=project_id, usd=usd, error=error, detail=detail)
        if not await _write_gap_row(entry):
            _spill_gap(entry)
    except Exception:  # noqa: BLE001
        logger.exception("media_spend: gap tracing failed")


async def _gap_row_or_spill(entry: dict) -> None:
    if not await _write_gap_row(entry):
        _spill_gap(entry)


def note_gap(provider: str, what: str, *, project_id=None, usd: float = 0.0,
             error: BaseException | None = None, detail: str = "") -> None:
    """Trace a lost record from sync code (any thread). Never raises."""
    try:
        entry = _register_gap(provider, what, project_id=project_id, usd=usd, error=error, detail=detail)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            task = loop.create_task(_gap_row_or_spill(entry))
            _tasks.add(task)
            task.add_done_callback(_tasks.discard)
        elif not asyncio.run(_write_gap_row(entry)):
            _spill_gap(entry)
    except Exception:  # noqa: BLE001
        logger.exception("media_spend: gap tracing failed")
        try:
            _spill_gap({"provider": provider, "what": what, "usd": usd})
        except Exception:  # noqa: BLE001
            pass


def _count_skip(provider: str, reason: str) -> None:
    key = f"{provider}:{reason}"
    with _gap_lock:
        first = key not in _skipped
        _skipped[key] = _skipped.get(key, 0) + 1
    if first:
        logger.warning("media_spend: {} calls are NOT recorded ({})", provider, reason)


def _spawn(coro) -> None:
    try:
        task = asyncio.get_running_loop().create_task(_serialized(coro))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    except Exception as exc:  # noqa: BLE001
        coro.close()
        note_gap("", "spawn", error=exc, detail="no running loop: the write was not scheduled")


async def drain() -> None:
    """Wait for pending background writes (tests, shutdown)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _project_id() -> int | None:
    try:
        from app.services.request_context import get_project_id  # _cost_project first, then the scope variable
        return get_project_id()
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning("media_spend: project id unavailable, the row is written without it")
        return None


def _provider_key(provider: str) -> str:
    if provider == "kie":
        from app.bots.kie_kling import kie_api_key
        key = kie_api_key()
    elif provider == "outsee":
        from app.bots.outsee_http import outsee_api_key
        key = outsee_api_key()
    else:
        from app.settings import settings
        key = settings.elevenlabs_api_key
    return str(key or "").strip()


def _synthetic_key(provider: str) -> bool:
    try:
        return _provider_key(provider).startswith("synthetic")
    except Exception:  # noqa: BLE001
        return False


def ledger_target_ok() -> bool:
    """The ledger session writes to the configured database (settings.sqlite_path).

    A mismatch means a process whose settings point elsewhere than its engine (a test sandbox, a broken
    start): rows would land in somebody else's database, so recording is refused, counted and logged."""
    try:
        from app.db import engine
        from app.settings import settings
        db = engine.url.database
        if not db:
            return True
        return Path(db).resolve() == Path(settings.sqlite_path).resolve()
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning("media_spend: ledger target check failed, assuming it is right")
        return True


def disabled_reason(provider: str) -> str | None:
    """Why recording is off for this provider, or None when it is on."""
    try:
        from app.settings import settings
        if not settings.media_spend_recording:
            return "setting_off"
        if provider != "browser" and not _provider_key(provider):  # the browser path has no API key by design
            return "no_key"
        if not TEST_FORCE and not ledger_target_ok():
            return "ledger_target_mismatch"
        return None
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).error("media_spend: cannot decide if recording is on, recording anyway")
        return None


def _enabled(provider: str) -> bool:
    reason = disabled_reason(provider)
    if reason is not None:
        _count_skip(provider, reason)
        return False
    return True


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


def _kind_for(usd: float, unknown: bool) -> str:
    """``actual``: a fact (a definite refusal or a poll: nothing was charged).
    ``estimated``: a price we computed, or a call whose cost we do not know."""
    return "estimated" if unknown or usd > 0 else "actual"


def _corr(ref: str = "") -> dict:
    """Correlation of the CALLER (B02), captured when the record is scheduled, not in the background task."""
    from app.services import request_context
    return {"run_id": request_context.get_run_id(), "request_id": request_context.get_request_id(), "ref": ref}


async def _write(project_id, provider, usd, note, credits=0.0, settle_from=None, kind="actual", corr=None,
                 basis="") -> None:
    corr = corr or _corr()
    try:
        from app.db import SessionLocal
        from app.services import spend_guard
        async with SessionLocal() as s:
            if settle_from is not None:
                await spend_guard.settle(s, project_id=project_id, run_id=corr["run_id"], reserved_usd=settle_from,
                                         actual_usd=usd, source="media_native", provider=provider,
                                         basis=basis, request_id=corr["request_id"], ref=corr["ref"])
            else:
                # estimated or actual, same columns (B03: the kind is decided by the caller, never by the amount)
                await spend_guard.record_actual(s, project_id=project_id, usd=usd, provider=provider,
                                                source="media_native", note=note, credits=credits, kind=kind,
                                                run_id=corr["run_id"], request_id=corr["request_id"],
                                                ref=corr["ref"])
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        await gap_async(provider, "settle" if settle_from is not None else "spend_row", project_id=project_id,
                        usd=usd, error=exc, detail=note)


async def _credit_stop(project_id, provider, note, corr=None) -> None:
    corr = corr or _corr()
    try:
        from app.db import SessionLocal
        from app.services import spend_guard
        await spend_guard.engage_on_credit_error(_CreditShim(provider), provider=provider)
        async with SessionLocal() as s:
            await spend_guard.record_event(s, project_id=project_id, provider=provider,
                                           source="media_402", note=note, run_id=corr["run_id"],
                                           request_id=corr["request_id"], ref=corr["ref"])
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        await gap_async(provider, "credit_stop_402", project_id=project_id, error=exc, detail=note)


async def _write_task(**kw) -> None:
    try:
        from app.db import SessionLocal
        from app.services import spend_guard
        async with SessionLocal() as s:
            await spend_guard.append_task_event(s, **kw)
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        await gap_async(str(kw.get("provider") or ""), "provider_task", project_id=kw.get("project_id"),
                        usd=float(kw.get("est_usd") or 0.0), error=exc,
                        detail=f"task={kw.get('task_id', '')} event={kw.get('event', '')}")


_uncorrelated: dict[str, int] = {}


def uncorrelated_counts() -> dict[str, int]:
    """Spend rows scheduled with no project or with neither a run nor a request id, by provider (B02).

    A non-empty result means a paid entry point did not set the correlation (``request_context.project_scope``)."""
    with _gap_lock:
        return dict(_uncorrelated)


def _check_correlation(provider: str, pid, corr: dict) -> None:
    if pid is not None and (corr.get("run_id") or corr.get("request_id")):
        return
    with _gap_lock:
        first = provider not in _uncorrelated
        _uncorrelated[provider] = _uncorrelated.get(provider, 0) + 1
    if first:
        logger.warning("media_spend: {} spend row has no project/run/request id (project={}, run={!r}, request={!r}): "
                       "a paid entry point is not wrapped in request_context.project_scope",
                       provider, pid, corr.get("run_id"), corr.get("request_id"))


def record(provider: str, *, usd: float, note: str, credits: float = 0.0, unknown: bool = False,
           exc: BaseException | None = None, ref: str = "") -> None:
    """Schedule one ledger row; on a credit error also engage the kill switch. Never raises.

    ``ref``: the provider task id (or another handle) of this call; the project, run and request ids come from
    ``request_context`` at the moment of the call."""
    try:
        if not _enabled(provider):
            return
        pid = _project_id()
        corr = _corr(ref)
        _check_correlation(provider, pid, corr)
        if unknown:
            note = f"{note} cost_unknown=true"
        if _synthetic_key(provider):
            note = f"{note} synthetic_key=true"
        usd = max(0.0, float(usd))
        _spawn(_write(pid, provider, usd, note, credits, kind=_kind_for(usd, unknown), corr=corr))
        if exc is not None and is_credit_error(exc):
            _spawn(_credit_stop(pid, provider, f"{provider} 402: {note}"[:200], corr))
    except Exception as err:  # noqa: BLE001
        note_gap(provider, "record", error=err, detail=str(note))


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
        logger.opt(exception=True).warning("media_spend: Kie price estimate failed, the row is written as cost_unknown")
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
               unknown=bool(ctx.get("unknown", True)), note=f"create {api} {model} task={task_id}",
               ref=str(task_id))
        if _enabled("kie"):
            _spawn(_write_task(project_id=_project_id(), key=ctx.get("key", ""), task_id=task_id,
                               provider="kie", api=api, model=model, event="submitted", status="submitted",
                               est_credits=None if ctx.get("unknown", True) else ctx.get("credits"),
                               est_usd=None if ctx.get("unknown", True) else ctx.get("usd")))
    except Exception as exc:  # noqa: BLE001
        note_gap("kie", "after_create_ok", error=exc, detail=f"api={api} task={task_id}")


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
    except Exception as err:  # noqa: BLE001
        note_gap("kie", "after_create_failed", error=err, detail=f"api={api}")


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
        record("kie", usd=0.0, note=f"poll {api} {ctx.get('model', '')} task={task_id} polls={polls}", exc=exc,
               ref=str(task_id))
        if _enabled("kie"):
            _spawn(_write_task(project_id=_project_id(), key=ctx.get("key", ""), task_id=task_id,
                               provider="kie", api=api, model=ctx.get("model", ""), event="poll",
                               status=status, note=f"{note} polls={polls}"))
    except Exception as err:  # noqa: BLE001
        note_gap("kie", "after_poll", error=err, detail=f"api={api} task={task_id}")


async def track_kling(coro, *, kie_id: str, duration, has_frame: bool):
    """Wrap the Kling fallback client call (own create/poll client) as one request row."""
    _refuse_coro(coro, "kie", "kling createTask")
    spec = None
    try:
        from app.services import kie_catalog
        spec = kie_catalog.get_model(f"{kie_id}-{'i2v' if has_frame else 't2v'}")
    except Exception:  # noqa: BLE001
        logger.opt(exception=True).warning("media_spend: Kling catalog lookup failed, the row is written as cost_unknown")
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
           note=f"kling {kie_id} task={getattr(result, 'gen_id', '')}", ref=str(getattr(result, "gen_id", "") or ""))
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
                # the provider's own answer (character-cost header): the settle row is a fact (B03)
                _spawn(_write(_project_id(), "elevenlabs", int(billed) * ELEVENLABS_USD_PER_CHAR, "",
                              settle_from=est, corr=_corr(), basis="reported"))
        else:
            record("elevenlabs", usd=0.0, exc=_CreditShim("elevenlabs") if resp.status_code == 402 else None,
                   note=f"tts {model} http={resp.status_code} not billed")
    except Exception as err:  # noqa: BLE001
        note_gap("elevenlabs", "tts_record", usd=est, error=err, detail=f"chars={chars}")
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
