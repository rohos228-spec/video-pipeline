"""Supervisory admission of media incidents: a resume-only recovery ticket, never a new paid request.

Text nodes are recovered by restarting the native node once (``actions_recovery.recover_node``). A paid
media node cannot be treated that way: a restart can submit again and pay twice, and a managed worker
attempt of a media node runs through a different transport (media_jobs, synthetic profile only). So the
media admission is narrower on purpose:

* the incident is ``media_failure`` or a media ``uncertain_request`` of an admitted node
  (``revisions.MEDIA_RESUME_NODES``: videos, items, music; plus the local node assemble, see below);
* ``media_recovery.assess`` decides. Only ``resume_poll`` (poll a known provider task, download a finished
  result) and ``resume_local`` (a finished file already lies on disk) are executed;
* the operation runs IN the supervisor process inside ``media_spend.resume_only()``: every seam that would
  send a new paid request (Kie createTask, Kling, Outsee, ElevenLabs) refuses before a byte leaves;
* ``retry_same`` (a provably failed task) is NOT dispatched: it is typed ``retry_same_not_admitted`` and left
  to the native layer or a human. A restart of the node would pay again and its managed transport is not
  the ledger path that proved the verdict;
* the result is verified independently: files re-read and probed, and the provider-task ledger must not
  hold a single new task id or submit intent compared with before the operation;
* the supervisor never starts or restarts the node. After a verified resume the human continues it.

A LOCAL node (``revisions.LOCAL_RECOVERY_NODES``: assemble) has no provider task. It is the one exception to
"never starts the node": the supervisor re-runs it ONCE through the original worker under a recovery ticket, inside
the resume-only scope, and the ticket is verified from independent facts (``local_node_recovery``).

Everything unknown (unknown outcome, retry cap, ledger disabled or unreadable, a node outside the admitted
set, nothing to do) is a typed refusal and ends with the human.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.services import media_recovery, media_resume, media_spend, revisions

RESUME_NODES = revisions.MEDIA_RESUME_NODES          # provider task resume, in the supervisor process
LOCAL_NODES = revisions.LOCAL_RECOVERY_NODES         # one re-run through the original worker (local_node_recovery)
ADMITTED_NODES = RESUME_NODES | LOCAL_NODES
MEDIA_INCIDENT_TYPES = frozenset({"media_failure", "uncertain_request"})
OPERATIONS = ("resume_poll", "resume_local")
RESUME_BUDGET_S = 100.0  # one decision step owns a 120 s lease; partial progress is written to the ledger
_FILE_KIND = {"clip_": "video", "music": "audio", "predmet": "image", "hero_": "image", "frame_": "image"}

DENIAL_TEXT = {
    "node_not_admitted": "Для этой медиа-ноды надзор возобновление не допускает: решает человек.",
    "incident_not_media": "Инцидент не относится к медиа-сбою с известной задачей провайдера.",
    "retry_same_not_admitted": ("Все прежние платные задачи достоверно упали, но повторную платную отправку надзор "
                                "не допускает: её делает нативный слой или человек."),
    "nothing_to_do": "Возобновлять нечего: открытых задач и готовых файлов для возобновления нет.",
    "verdict_denied": "Исход прошлой платной отправки неизвестен или повтор исчерпан: решает человек.",
    "source_proof_missing": ("Нет записи проверки упавшей попытки, которая называла бы её собственный сбой: "
                             "повторять локальную ноду без неё нельзя."),
    "failure_not_retryable": ("Нода сама не считает этот сбой повторяемым (ошибка входа: нет клипов, озвучки): "
                              "повтор ничего не изменит, решает человек."),
    "ledger_unavailable": ("Журнал платных задач выключен или не читается: неизменность журнала после повтора "
                           "не доказать, поэтому повтор не допускается."),
}


class MediaAdmissionDenied(ValueError):
    """Typed refusal of a media recovery ticket. ``code`` is machine readable, the message is for people."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        text = DENIAL_TEXT.get(code, code)
        super().__init__(f"media recovery denied [{code}]: {text}" + (f" ({detail})" if detail else ""))


def is_media_incident(incident) -> bool:
    details = getattr(incident, "details", None) or {}
    return incident.type == "media_failure" or (incident.type == "uncertain_request" and details.get("media") is True)


def resumable(node_type: str | None) -> bool:
    """A paid media node whose recovery is a resume of a known provider task (runs in the supervisor process)."""
    return node_type in RESUME_NODES


def local_worker(node_type: str | None) -> bool:
    """A local node re-run once through the original worker (a ticket that is verified from independent facts)."""
    from app.services import local_node_recovery
    return node_type in LOCAL_NODES and local_node_recovery.spec_ok(node_type)


def admitted(node_type: str | None) -> bool:
    return resumable(node_type) or local_worker(node_type)


async def admit(project, node_type: str, *, source_facts=None) -> dict[str, Any]:
    """Read-only. The admitted operation and the full assessment, or a typed refusal. Never submits.

    A local node needs ``source_facts`` (the events of the failed attempt): without them there is nothing that
    types its own failure, and no operation is offered."""
    if local_worker(node_type):
        from app.services import local_node_recovery
        return await local_node_recovery.admit(project, node_type, source_facts=source_facts)
    if not admitted(node_type):
        raise MediaAdmissionDenied("node_not_admitted", str(node_type))
    report = await media_recovery.assess(project, node_type)
    verdict = report["verdict"]
    mode = verdict["mode"]
    if mode in OPERATIONS:
        return {"operation": mode, "verdict": verdict, "report": report}
    if mode == "retry_same":
        raise MediaAdmissionDenied("retry_same_not_admitted", verdict.get("code", ""))
    if mode == "denied":
        raise MediaAdmissionDenied("verdict_denied", verdict.get("code", ""))
    raise MediaAdmissionDenied("nothing_to_do", verdict.get("code", ""))


async def task_fingerprint(project_id: int) -> frozenset[str]:
    """Every provider task id AND submit intent the ledger holds for the project. A new paid request, however
    it ends, always adds one of them before the request leaves, so growth means a submit happened."""
    from sqlalchemy import select
    from app.db import SessionLocal
    from app.services import spend_guard
    await media_spend.drain()
    async with SessionLocal() as session:
        await spend_guard.ensure_task_schema(session)
        rows = (await session.scalars(select(spend_guard.ProviderTaskLedger.task_id).where(
            spend_guard.ProviderTaskLedger.project_id == project_id).distinct())).all()
    return frozenset(rows)


def _file_kind(key: str) -> str:
    return next((kind for prefix, kind in _FILE_KIND.items() if key.startswith(prefix)), "image")


def _result_files(project, key: str) -> list[Path]:
    directory = media_resume.result_dir(Path(project.data_dir), key)
    pattern = re.compile(rf"^{re.escape(key)}_[0-9a-f]{{8}}\.\w+$")
    try:
        return sorted((p for p in directory.iterdir() if pattern.match(p.name) and p.is_file()),
                      key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []


async def verify_files(project, keys: list[str]) -> list[dict[str, Any]]:
    """Independent re-read of the result files of the resumed keys: exists, not empty, inside the project,
    decodes (image) or ffprobe sees the stream and duration (video, audio)."""
    from app.services.media_verification import _probe
    out = []
    root = Path(project.data_dir)
    for key in keys:
        files = _result_files(project, key)
        if not files:
            out.append({"key": key, "status": "failed", "reason": "missing_file", "path": None})
            continue
        probe = await _probe(files[0], root=root, kind=_file_kind(key))
        out.append({"key": key, "path": str(files[0].relative_to(root)) if files[0].is_relative_to(root)
                    else files[0].name, **probe})
    return out


async def _run_operation(project, node_type: str, operation: str) -> tuple[dict[str, Any], str | None]:
    """The only place a provider is contacted: one status request per open task, inside resume_only()."""
    if operation == "resume_local":
        return {"resumed": [], "stats": {}, "new_paid_requests": 0}, None  # nothing to ask: files are on disk
    try:
        return await asyncio.wait_for(media_recovery.resume_poll(project, node_type), RESUME_BUDGET_S), None
    except asyncio.TimeoutError:
        return {"resumed": [], "stats": {}, "new_paid_requests": 0}, "resume_budget_exceeded"
    except Exception as exc:  # noqa: BLE001 - a poll error never turns into a submit
        logger.warning("media_admission: resume failed: {}", type(exc).__name__)
        return {"resumed": [], "stats": {}, "new_paid_requests": 0}, type(exc).__name__


async def run_and_verify(project, node_type: str, admission: dict[str, Any]) -> dict[str, Any]:
    """Execute the admitted operation and verify it from facts outside the executor. Never submits."""
    if node_type in LOCAL_NODES:
        raise ValueError("a local node is re-run by the original worker under a recovery ticket, never here")
    operation = admission["operation"]
    before = await task_fingerprint(project.id)
    report, error = await _run_operation(project, node_type, operation)
    after = await task_fingerprint(project.id)
    new_ids = sorted(after - before)
    reassessed = await media_recovery.assess(project, node_type)
    ledger_keys = [i["key"] for i in admission["report"]["keys"] if i["mode"] == "resume_poll"] \
        if operation == "resume_poll" else [i["key"] for i in admission["report"]["keys"]]
    done_outcomes = {"downloaded", "file_present"}
    resumed = {r["key"]: r for r in report.get("resumed", [])}
    checked = [k for k in ledger_keys if (resumed.get(k) or {}).get("outcome") in done_outcomes]
    files = await verify_files(project, checked) if checked else []
    problems = []
    if error:
        problems.append(error)
    if new_ids:
        problems.append("new_provider_task_in_ledger")
    for key in ledger_keys:
        outcome = (resumed.get(key) or {}).get("outcome")
        if outcome not in done_outcomes:
            problems.append(f"{key}:{outcome or 'not_resumed'}")
    problems += [f"{f['key']}:file_{f.get('reason') or f['status']}" for f in files if f["status"] != "passed"]
    verified = not problems and (operation == "resume_local" or bool(checked))
    return {"operation": operation, "verified": verified, "problems": problems, "error": error,
            "resumed": report.get("resumed", []), "stats": report.get("stats", {}),
            "files": files, "new_provider_task_ids": new_ids,
            "new_paid_requests": len(new_ids),
            "assessment_after": {"mode": reassessed["verdict"]["mode"], "code": reassessed["verdict"]["code"]},
            "semantic_quality_verified": False, "node_started": False,
            "verified_at": datetime.utcnow().isoformat()}


async def settle_interrupted(master, row, control, observer, update_permission) -> bool:
    """A media ticket is executed inside the recover call and is never re-run. One found in a transient
    state long after (process died mid-resume) is held for a human: the provider outcome was not recorded."""
    from app.services.actions_v1 import ActionRecord
    if row.status not in {"reserved", "queued", "claimed", "await_verify"}:
        return False
    if (datetime.utcnow() - row.created_at).total_seconds() < 180:
        return False
    row.status = "human_review"
    row.verification_result = {**(row.verification_result or {}), "verified": False,
                               "reason": "media resume interrupted; not repeated automatically"}
    record = await master.get(ActionRecord, row.action_id)
    if record is not None:
        record.status = "human_review"
        record.verification = row.verification_result
    if control is not None:
        control.held = True
        update_permission(row.project_id, held=True)
    return True
