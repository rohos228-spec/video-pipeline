"""Outsee image idempotency by request key (D03): intent BEFORE the paid call, result AFTER it.

Outsee gives no task id to the client, so a repeated call is a new paid task. This module makes a repeat
safe without a task id. The key of a request is ``sha256(project, frame key, model, prompt)``; the rows live
in the append-only table ``outsee_idempotency`` (own table, created on first use, no change to the shared
ledger schema). Rows of one attempt share ``attempt_id`` and the newest row is its state:

* ``intent``    written and awaited right before the paid call;
* ``result``    the call returned and the file is on disk (path, size, sha256 of the file are stored);
* ``failed``    the provider refused or nothing was sent (``outsee_guard.classify_failure``): a repeat is free;
* ``uncertain`` the request may have been accepted and billed (answer lost, id known but no result, process
                cancelled): a SEPARATE status, a repeat is refused until a human decides;
* ``released``  the human checked the Outsee history: no charge, a repeat is allowed;
* ``consumed``  the step has taken the result into the project (artifact saved): the result is no longer
                offered for reuse, so an ordinary regeneration with the same prompt is a new request.

A crash after ``intent`` leaves the attempt without a verdict. Reading treats it as ``uncertain``
(``interrupted``): the call may have left the process.

Decisions of ``check`` for a new call of (project, key):

* an unresolved attempt of the key (any prompt)            -> ``OutseeIdempotencyUncertainError``;
* a ``result`` of the SAME request, not consumed, inside ``MEDIA_RESUME_WINDOW_S``, file intact
                                                           -> adopt the file, NO paid call;
* otherwise                                                -> the call may go on.

``resume_only`` (``media_spend.resume_only()``): only an adoptable result may be returned; with none, the
call is refused before any byte leaves and without an intent row.

This layer is added on top of ``outsee_guard`` (intent rows in the provider ledger). Both block; the human
release of ``media_unknown.release`` (it resolves the ledger intents) also releases this layer: a ledger
``released`` row newer than the attempt counts as the human decision.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger
from sqlalchemy import DateTime, Integer, String, select
from sqlalchemy.orm import Mapped, mapped_column

from app.bots.outsee import GenerationResult, OutseeImageError
from app.bots.outsee_http import OutseeSubmitUnknownError
from app.models import Base
from app.services import media_resume, media_spend

CODE_UNCERTAIN = "outsee_uncertain_submit"
CODE_RESUME_ONLY = "outsee_resume_only_no_result"
OPEN_EVENTS = ("intent", "uncertain")
_MIN_IMAGE_BYTES = 1024


class OutseeIdempotency(Base):
    """Append-only attempt log of Outsee paid requests (see module docstring)."""
    __tablename__ = "outsee_idempotency"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(120), default="", index=True)
    request_sha: Mapped[str] = mapped_column(String(64), default="")
    attempt_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    event: Mapped[str] = mapped_column(String(16), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    media: Mapped[str] = mapped_column(String(8), default="image")
    file_path: Mapped[str] = mapped_column(String(500), default="")
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_sha256: Mapped[str] = mapped_column(String(64), default="")
    raw_url: Mapped[str] = mapped_column(String(500), default="")
    note: Mapped[str] = mapped_column(String(200), default="")


_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS outsee_idempotency_no_update BEFORE UPDATE ON outsee_idempotency "
    "BEGIN SELECT RAISE(ABORT, 'outsee idempotency log is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS outsee_idempotency_no_delete BEFORE DELETE ON outsee_idempotency "
    "BEGIN SELECT RAISE(ABORT, 'outsee idempotency log is append-only'); END",
)
_ensured: set[str] = set()


async def ensure_schema(session) -> None:
    """Create the table and its append-only triggers on first use (a registered migration may do the same:
    ``OutseeIdempotency.__table__`` and ``_TRIGGERS`` are the whole definition)."""
    bind = session.get_bind()
    marker = str(getattr(bind, "url", id(bind)))
    if marker in _ensured:
        return
    await session.run_sync(lambda s: OutseeIdempotency.__table__.create(s.connection(), checkfirst=True))
    if session.get_bind().dialect.name == "sqlite":
        from sqlalchemy import text
        for ddl in _TRIGGERS:
            await session.execute(text(ddl))
    _ensured.add(marker)


class OutseeIdempotencyUncertainError(OutseeSubmitUnknownError):
    """An earlier paid request of this frame has no known outcome: a human decides. ``no_resubmit`` stops every ladder."""

    def __init__(self, reason: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(reason, context={**(context or {}), "code": CODE_UNCERTAIN, "human_decision": True})


class OutseeResumeOnlyError(OutseeImageError):
    """Resume-only scope and no recorded result of this request: nothing was sent."""

    def __init__(self, reason: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(reason, context={**(context or {}), "code": CODE_RESUME_ONLY, "no_resubmit": True,
                                          "resume_only": True, "request_sent": False})


def uncertain_error(message: str, context: dict[str, Any]) -> Exception:
    return OutseeIdempotencyUncertainError(message, context=context)


def is_uncertain(exc: BaseException) -> bool:
    return isinstance(getattr(exc, "context", None), dict) and exc.context.get("code") == CODE_UNCERTAIN


# -- request key --------------------------------------------------------------------------------

def request_sha(project_id: int | None, key: str, model: str, prompt: str) -> str:
    raw = "\x1f".join([str(project_id), key, model or "", prompt or ""])
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class Ctx:
    project_id: int | None
    key: str
    model: str
    sha: str
    media: str = "image"

    @property
    def usable(self) -> bool:
        return (isinstance(self.project_id, int) and bool(self.key) and media_resume.window_s() > 0
                and not media_spend._managed() and media_spend.disabled_reason("browser") is None)


def make_ctx(project_id: Any, out_path: Any, model: Any, prompt: Any, media: str = "image") -> Ctx:
    key = media_spend._task_key(out_path) if out_path is not None else ""
    if not media_resume._RESUMABLE_KEY.match(key):
        key = ""
    pid = project_id if isinstance(project_id, int) else None
    return Ctx(pid, key, str(model or ""), request_sha(pid, key, str(model or ""), str(prompt or "")), media)


# -- storage ------------------------------------------------------------------------------------

async def _append(session, ctx: Ctx, attempt_id: str, event: str, *, sha: str | None = None, note: str = "",
                  file_path: str = "", file_size: int | None = None, fsha: str = "", raw_url: str = "") -> None:
    await ensure_schema(session)
    session.add(OutseeIdempotency(
        project_id=ctx.project_id, key=ctx.key[:120], request_sha=sha or ctx.sha, attempt_id=attempt_id,
        event=event[:16], model=ctx.model[:64], media=ctx.media[:8], file_path=file_path[:500],
        file_size=file_size, file_sha256=fsha, raw_url=(raw_url or "")[:500], note=(note or "")[:200]))


async def _write(ctx: Ctx, attempt_id: str, event: str, **kw) -> bool:
    """One awaited, committed row. A failed write is traced and reported, never silently dropped."""
    try:
        from app.db import SessionLocal
        async with SessionLocal() as s:
            await _append(s, ctx, attempt_id, event, **kw)
            await s.commit()
        return True
    except Exception as e:  # noqa: BLE001
        await media_spend.gap_async("outsee", "idempotency_row", project_id=ctx.project_id, error=e,
                                    detail=f"{event} key={ctx.key}")
        return False


async def _attempts(session, project_id: int | None, key: str) -> list[dict]:
    """Attempts of one key, oldest first: newest row decides the state."""
    await ensure_schema(session)
    rows = (await session.scalars(select(OutseeIdempotency).where(
        OutseeIdempotency.project_id == project_id, OutseeIdempotency.key == key[:120])
        .order_by(OutseeIdempotency.id))).all()
    out: dict[str, dict] = {}
    for r in rows:
        a = out.setdefault(r.attempt_id, {"attempt_id": r.attempt_id, "request_sha": r.request_sha,
                                          "first_at": r.created_at, "model": r.model, "events": [],
                                          "file_path": "", "file_size": None, "file_sha256": "", "raw_url": ""})
        a["events"].append(r.event)
        a["state"], a["last_at"], a["note"] = r.event, r.created_at, r.note
        if r.event == "result":
            a.update(file_path=r.file_path, file_size=r.file_size, file_sha256=r.file_sha256, raw_url=r.raw_url,
                     result_at=r.created_at)
    return list(out.values())


async def _human_released_since(session, project_id: int | None, key: str, since: datetime) -> bool:
    """``media_unknown.release`` resolves the ledger intents of the key with note ``released``."""
    try:
        from app.services import spend_guard
        await spend_guard.ensure_task_schema(session)
        row = await session.scalar(select(spend_guard.ProviderTaskLedger.id).where(
            spend_guard.ProviderTaskLedger.project_id == project_id,
            spend_guard.ProviderTaskLedger.key == key[:120],
            spend_guard.ProviderTaskLedger.api == "outsee",
            spend_guard.ProviderTaskLedger.event == "intent_done",
            spend_guard.ProviderTaskLedger.note == "released",
            spend_guard.ProviderTaskLedger.created_at >= since).limit(1))
        return row is not None
    except Exception:  # noqa: BLE001
        return False


def _file_intact(att: dict) -> Path | None:
    p = Path(att.get("file_path") or "")
    try:
        if not p.is_file() or p.stat().st_size < _MIN_IMAGE_BYTES:
            return None
        if att.get("file_sha256") and file_sha256(p) != att["file_sha256"]:
            return None
        return p
    except OSError:
        return None


async def assess(project_id: int | None, key: str, sha: str | None = None) -> dict[str, Any]:
    """Read-only verdict for one key. ``mode``: ``uncertain`` | ``adopt`` | ``free`` | ``unavailable``.

    ``adopt`` carries the file of the newest unconsumed result (of ``sha`` when given). Never writes."""
    if not isinstance(project_id, int) or not key:
        return {"mode": "unavailable", "code": "no_key"}
    window = media_resume.window_s()
    if window <= 0:
        return {"mode": "unavailable", "code": "window_disabled"}
    await media_spend.drain()
    try:
        from app.db import SessionLocal
        async with SessionLocal() as s:
            atts = await _attempts(s, project_id, key)
            unresolved = []
            for a in atts:
                if a["state"] in OPEN_EVENTS:
                    if await _human_released_since(s, project_id, key, a["last_at"]):
                        continue  # a human resolved the provider intents of this key after the attempt
                    unresolved.append(a)
    except Exception as e:  # noqa: BLE001
        logger.warning("outsee_idempotency: read failed: {}", type(e).__name__)
        return {"mode": "unavailable", "code": "ledger_unreadable"}
    if unresolved:
        return {"mode": "uncertain", "code": CODE_UNCERTAIN,
                "attempts": [{"attempt_id": a["attempt_id"], "request_sha": a["request_sha"], "state": a["state"],
                              "interrupted": a["state"] == "intent"} for a in unresolved]}
    cutoff = datetime.utcnow() - timedelta(seconds=window)
    fresh = [a for a in atts if a["state"] == "result" and a.get("result_at") and a["result_at"] >= cutoff
             and (sha is None or a["request_sha"] == sha)]
    for a in reversed(fresh):
        path = _file_intact(a)
        if path is not None:
            return {"mode": "adopt", "code": "result_on_record", "attempt_id": a["attempt_id"],
                    "request_sha": a["request_sha"], "file_path": str(path), "raw_url": a["raw_url"],
                    "model": a["model"]}
    return {"mode": "free", "code": "nothing_recorded"}


async def check(ctx: Ctx) -> dict[str, Any] | None:
    """For a new call: ``None`` (go on) or the ``adopt`` verdict. Raises when an earlier request is uncertain.

    Inside ``resume_only`` the call is refused when there is nothing to adopt (nothing is written)."""
    if not ctx.usable:
        return None
    verdict = await assess(ctx.project_id, ctx.key, ctx.sha)
    if verdict["mode"] == "uncertain":
        raise uncertain_error(
            f"{ctx.key}: прошлый платный запрос в Outsee без известного исхода (мог быть принят и оплачен). "
            f"Повторную отправку не делаю: проверьте историю генераций и баланс Outsee и решите "
            f"(снять блокировку или подставить найденный файл)",
            {"frame_key": ctx.key, "attempts": verdict["attempts"], "request_sent": False})
    if verdict["mode"] == "adopt":
        return verdict
    if media_spend.in_resume_only():
        try:
            media_spend.assert_submit_allowed("outsee", f"{ctx.media} {ctx.key}")  # records the refusal hit
        except media_spend.ResumeOnlyBlocked as blocked:
            raise OutseeResumeOnlyError(
                f"{ctx.key}: режим только возобновления, записанного результата этого запроса нет; "
                f"новый платный запрос в Outsee не отправлялся", context={"frame_key": ctx.key}) from blocked
    return None


def adopted_result(verdict: dict[str, Any]):
    return GenerationResult(file_path=Path(verdict["file_path"]), raw_url=verdict.get("raw_url") or None,
                            gen_id=f"idem:{verdict['attempt_id']}")


async def run(ctx: Ctx, call: Callable[[], Awaitable[Any]], *, sent_prompt: str = "") -> Any:
    """Run ONE paid Outsee call under an intent row and record its result.

    Raises ``OutseeIdempotencyUncertainError`` instead of a second paid request when an earlier attempt of the key
    is unresolved. On success the result file is hashed and recorded; on a lost answer the attempt becomes
    ``uncertain`` (a separate status)."""
    if not ctx.usable:
        return await call()
    media_spend.assert_submit_allowed("outsee", f"{ctx.media} {ctx.key}")
    verdict = await assess(ctx.project_id, ctx.key, ctx.sha)
    if verdict["mode"] == "uncertain":
        await check(ctx)  # raises with the common text
    attempt = uuid.uuid4().hex[:32]
    sent_note = ("sent=" + hashlib.sha256((sent_prompt or "").encode("utf-8", "replace")).hexdigest()[:16]) if sent_prompt else ""
    if not await _write(ctx, attempt, "intent", note=sent_note):
        # no durable intent: a lost answer could not be blocked later. The paid call is NOT started blind.
        raise uncertain_error(
            f"{ctx.key}: не удалось записать намерение отправки в Outsee; платный запрос не отправлен",
            {"frame_key": ctx.key, "request_sent": False, "intent_row_missing": True})
    import asyncio
    from app.bots.outsee import OutseeDownloadError
    from app.services.step_cancel import StepCancelledError
    try:
        result = await call()
    except (asyncio.CancelledError, StepCancelledError):
        # the provider-ledger guard knows exactly whether the click/POST happened: it leaves an intent open only then
        if await _provider_intent_open(ctx):
            await _write(ctx, attempt, "uncertain", note="cancelled after submit")
        else:
            await _write(ctx, attempt, "failed", note="cancelled before submit")
        raise
    except Exception as exc:
        if is_uncertain(exc):
            raise
        if isinstance(exc, OutseeSubmitUnknownError):
            await _write(ctx, attempt, "uncertain", note=f"no verdict {type(exc).__name__}")
        elif isinstance(exc, OutseeDownloadError):
            # generated and billed, the file is not here yet: uncertain until the download-only retry records it
            await _write(ctx, attempt, "uncertain", note="generated, download failed")
            if isinstance(getattr(exc, "context", None), dict):
                exc.context["idem_attempt"] = attempt
        else:
            await _write(ctx, attempt, "failed", note=f"{type(exc).__name__}")
        raise
    await record_result(ctx, attempt, result)
    return result


async def record_download(ctx: Ctx | None, exc: BaseException, result: Any) -> bool:
    """The download-only retry after a finished generation got the file: the attempt ends with a result."""
    attempt = (getattr(exc, "context", None) or {}).get("idem_attempt") if ctx is not None else None
    if ctx is None or not attempt or not ctx.usable:
        return False
    return await record_result(ctx, str(attempt), result)


async def _provider_intent_open(ctx: Ctx) -> bool:
    try:
        from app.services import outsee_guard
        guard = outsee_guard.Ctx(ctx.project_id, ctx.key, ctx.model, ctx.media, ctx.sha[:16], False)
        return bool(await outsee_guard._unresolved(guard))
    except Exception:  # noqa: BLE001
        return True  # cannot tell: stay on the safe side


async def record_result(ctx: Ctx, attempt: str, result: Any) -> bool:
    path = Path(getattr(result, "file_path", "") or "")
    try:
        size = path.stat().st_size
        digest = file_sha256(path)
    except OSError:
        # the call said ok but there is no file: the provider may have billed, the file is lost
        await _write(ctx, attempt, "uncertain", note="result without file")
        return False
    return await _write(ctx, attempt, "result", file_path=str(path), file_size=size, fsha=digest,
                        raw_url=getattr(result, "raw_url", "") or "")


async def mark_consumed(project_id: int | None, key: str) -> int:
    """The step took the result into the project: it is not offered for reuse any more. Returns attempts marked."""
    if not isinstance(project_id, int) or not key or media_resume.window_s() <= 0:
        return 0
    if media_spend.disabled_reason("browser") is not None or media_spend._managed():
        return 0
    try:
        from app.db import SessionLocal
        async with SessionLocal() as s:
            atts = await _attempts(s, project_id, key)
            todo = [a for a in atts if a["state"] == "result"]
            for a in todo:
                await _append(s, Ctx(project_id, key, a["model"], a["request_sha"]), a["attempt_id"], "consumed",
                              note="taken by the step")
            await s.commit()
        return len(todo)
    except Exception as e:  # noqa: BLE001
        await media_spend.gap_async("outsee", "idempotency_consume", project_id=project_id, error=e, detail=key)
        return 0


async def release(project_id: int, key: str, *, note: str = "released by human") -> int:
    """Human decision 1: the Outsee history shows no charge, a new request is allowed. Also resolves the
    provider-ledger intents of the key (``outsee_guard``). Returns the number of released attempts."""
    from app.db import SessionLocal
    from app.services import outsee_guard
    async with SessionLocal() as s:
        atts = [a for a in await _attempts(s, project_id, key) if a["state"] in OPEN_EVENTS]
        for a in atts:
            await _append(s, Ctx(project_id, key, a["model"], a["request_sha"]), a["attempt_id"], "released", note=note)
        await s.commit()
    await outsee_guard.release_unknown(project_id, key)
    return len(atts)


async def adopt_file(project_id: int, key: str, path: Path, *, note: str = "file named by human") -> bool:
    """Human decision 2: the request was billed and the picture exists. The file becomes the recorded result of
    the open attempt, so recovery and the next call take it instead of paying again."""
    from app.db import SessionLocal
    path = Path(path)
    if not path.is_file() or path.stat().st_size < _MIN_IMAGE_BYTES:
        return False
    digest = file_sha256(path)
    async with SessionLocal() as s:
        atts = [a for a in await _attempts(s, project_id, key) if a["state"] in OPEN_EVENTS]
        if not atts:
            return False
        for a in atts:
            await _append(s, Ctx(project_id, key, a["model"], a["request_sha"]), a["attempt_id"], "result",
                          note=note, file_path=str(path), file_size=path.stat().st_size, fsha=digest)
        await s.commit()
    from app.services import outsee_guard
    await outsee_guard.release_unknown(project_id, key)
    return True
