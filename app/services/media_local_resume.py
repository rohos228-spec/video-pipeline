"""D06: supervised reconcile / resume-local admission for paid nodes WITHOUT a provider task id.

``media_admission`` admits a resume only where a provider holds a task id (Kie: videos, items, music) and for the
local node ``assemble``. The paid nodes ``images``, ``hero``, ``items`` (Outsee), ``audio`` (TTS) and ``sfx_gen`` have no
task id, so until now every incident of them ended in a blanket stop (``no_resumable_task``, ``paid_no_idempotency``,
``paid_uncertain_submit``). This module re-judges them from the facts that exist since D03 and the earlier checkpoints:

``images`` / ``hero`` / ``items``  the Outsee request-key record (``outsee_idempotency``): a paid result on record whose file is
                                   intact is ``resume_local`` (the node takes the file, no request); an attempt without a
                                   verdict is ``denied`` (``unknown_outcome_outsee``, a human decides); plus everything
                                   ``media_recovery`` already knows (Kie tasks, ledger intents);
``audio``                          a finished file on disk (``media_recovery.assess_audio``);
``sfx_gen``                        the ElevenLabs event files in the checkpoint ``sfx_files`` that exist and decode; an event
                                   whose request left without an answer (``inflight``) stays ``denied``.

What is NEVER done here: a paid request. A paid RE-send stays native or human (``retry_same`` is typed
``retry_same_not_admitted``). The operation runs in the supervisor process inside ``media_spend.resume_only()``;
the result is verified from independent facts: every file is re-read and decoded inside the project folder, and neither
the provider-task ledger nor the request-key log may hold one new row compared with before.

The API is the shape of ``media_admission`` (``admitted``, ``admit``, ``run_and_verify``) so the caller can route by node
type: ``actions_recovery`` does this for the media block.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import func, select

from app.services import media_admission, media_recovery, media_resume, media_spend, outsee_idempotency as idem
from app.services.media_admission import MediaAdmissionDenied

OUTSEE_NODES = frozenset({"images", "hero", "items"})
NODES = OUTSEE_NODES | frozenset({"audio", "sfx_gen"})
PROBE_KIND = {"images": "image", "hero": "image", "items": "image", "audio": "audio", "sfx_gen": "audio"}
OPERATIONS = ("resume_poll", "resume_local")


def admitted(node_type: str | None) -> bool:
    return node_type in NODES


def _request_keys_usable() -> bool:
    return (media_resume.window_s() > 0 and not media_spend._managed()
            and media_spend.disabled_reason("browser") is None)


def _item(key: str, mode: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, **media_recovery._verdict(mode, code, **extra)}


async def _idem_keys(project_id: int, node_type: str) -> list[str]:
    from app.db import SessionLocal
    family = media_recovery.KEY_FAMILY[node_type]
    async with SessionLocal() as session:
        await idem.ensure_schema(session)
        rows = (await session.scalars(select(idem.OutseeIdempotency.key).where(
            idem.OutseeIdempotency.project_id == project_id).distinct())).all()
    return sorted(k for k in rows if k and family.match(k))


async def _outsee_items(project, node_type: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in await _idem_keys(project.id, node_type):
        verdict = await idem.assess(project.id, key)
        if verdict["mode"] == "uncertain":
            items.append(_item(key, "denied", "unknown_outcome_outsee", provider="outsee", source="request_key",
                               attempts=verdict["attempts"]))
        elif verdict["mode"] == "adopt":
            items.append(_item(key, "resume_local", "outsee_result_on_record", provider="outsee", source="request_key",
                               file_path=verdict["file_path"], request_sha=verdict["request_sha"]))
    return items


def _sfx_items(project) -> list[dict[str, Any]]:
    from app.services.ai_result_io import ai_job_checkpoint
    cp = ai_job_checkpoint(project, "sfx_files")
    files = cp.get("files") if isinstance(cp, dict) and isinstance(cp.get("files"), list) else []
    prog = ai_job_checkpoint(project, "sfx_progress")
    inflight = prog.get("inflight") if isinstance(prog, dict) and isinstance(prog.get("inflight"), dict) else {}
    items = []
    for rec in files:
        if not isinstance(rec, dict) or rec.get("provider") != "elevenlabs" or str(rec.get("idx")) in inflight:
            continue
        path = Path(str(rec.get("path") or ""))
        if path.is_file():
            items.append(_item(f"sfx_{rec['idx']}", "resume_local", "sfx_event_file_on_record",
                               provider="elevenlabs", file_path=str(path)))
    return items


async def assess(project, node_type: str) -> dict[str, Any]:
    """Read-only. Same shape as ``media_recovery.assess``; never submits, never writes."""
    if node_type not in NODES:
        raise ValueError(f"no local-resume profile for node type {node_type!r}")
    report = await media_recovery.assess(project, node_type)
    items = list(report["keys"])
    verdict = report["verdict"]
    if node_type in OUTSEE_NODES and report.get("ledger") in ("ok", "disabled") and _request_keys_usable():
        if report.get("ledger") == "disabled":
            items = []  # the Kie task ledger is off (no Kie key): no Kie task exists, the request-key log decides
        try:
            items += await _outsee_items(project, node_type)
        except Exception as exc:  # noqa: BLE001
            logger.warning("media_local_resume: request-key log unreadable: {}", type(exc).__name__)
            items.append(_item("", "denied", "request_key_log_unavailable"))
        verdict = media_recovery.aggregate(items)
    elif node_type == "sfx_gen":
        items += _sfx_items(project)
        verdict = media_recovery.aggregate(items)
    return {**report, "keys": items, "verdict": verdict, "submits_new_paid_request": False}


async def admit(project, node_type: str) -> dict[str, Any]:
    """The admitted operation and the full assessment, or a typed refusal. Read-only."""
    if not admitted(node_type):
        raise MediaAdmissionDenied("node_not_admitted", str(node_type))
    report = await assess(project, node_type)
    verdict = report["verdict"]
    mode = verdict["mode"]
    if mode in OPERATIONS:
        return {"operation": mode, "verdict": verdict, "report": report}
    if mode == "retry_same":
        raise MediaAdmissionDenied("retry_same_not_admitted", verdict.get("code", ""))
    if mode == "denied":
        raise MediaAdmissionDenied("verdict_denied", verdict.get("code", ""))
    raise MediaAdmissionDenied("nothing_to_do", verdict.get("code", ""))


async def request_key_fingerprint(project_id: int) -> int:
    """Number of rows of the request-key log of the project: a verified operation must not change it."""
    from app.db import SessionLocal
    await media_spend.drain()
    async with SessionLocal() as session:
        await idem.ensure_schema(session)
        return int(await session.scalar(select(func.count()).select_from(idem.OutseeIdempotency).where(
            idem.OutseeIdempotency.project_id == project_id)) or 0)


def _audio_paths(project) -> list[Path]:
    from app.services.elevenlabs_api import _SIDECAR_SUFFIX
    from app.services.frame_audio import find_voice_full_on_disk
    root = Path(project.data_dir)
    out: list[Path] = []
    try:
        out += [p.with_name(p.name[: -len(_SIDECAR_SUFFIX)]) for p in sorted((root / "audio").glob("*" + _SIDECAR_SUFFIX))]
        voice = find_voice_full_on_disk(root, meta=project.meta if isinstance(project.meta, dict) else None)
    except Exception:  # noqa: BLE001
        voice = None
    if voice is not None:
        out.append(Path(voice))
    return [p for p in dict.fromkeys(out) if p.is_file()]


def _files_to_verify(project, node_type: str, items: list[dict[str, Any]]) -> list[tuple[str, Path]]:
    if node_type == "audio":
        return [("audio", p) for p in _audio_paths(project)]
    return [(i["key"], Path(i["file_path"])) for i in items if i.get("file_path")]


async def run_and_verify(project, node_type: str, admission: dict[str, Any]) -> dict[str, Any]:
    """Execute the admitted operation and verify it from facts outside the executor. Never submits."""
    if node_type not in NODES:
        raise ValueError(f"{node_type!r} is not a local-resume node")
    operation = admission["operation"]
    if operation == "resume_poll":
        return await media_admission.run_and_verify(project, node_type, admission)
    from app.services.media_verification import _probe
    ledger_before = await media_admission.task_fingerprint(project.id)
    keys_before = await request_key_fingerprint(project.id)
    root = Path(project.data_dir)
    items = [i for i in admission["report"]["keys"] if i["mode"] == "resume_local"]
    files: list[dict[str, Any]] = []
    with media_spend.resume_only():  # even a coding mistake cannot reach a paid seam from here
        for key, path in _files_to_verify(project, node_type, items):
            probe = await _probe(path, root=root, kind=PROBE_KIND[node_type])
            files.append({"key": key, "path": str(path.relative_to(root)) if path.is_relative_to(root) else path.name,
                          **probe})
    ledger_after = await media_admission.task_fingerprint(project.id)
    keys_after = await request_key_fingerprint(project.id)
    new_ids = sorted(ledger_after - ledger_before)
    reassessed = await assess(project, node_type)
    problems = []
    if not files:
        problems.append("no_file_to_verify")
    problems += [f"{f['key']}:file_{f.get('reason') or f['status']}" for f in files if f["status"] != "passed"]
    if new_ids:
        problems.append("new_provider_task_in_ledger")
    if keys_after != keys_before:
        problems.append("request_key_log_changed")
    return {"operation": operation, "verified": not problems, "problems": problems, "error": None,
            "resumed": [], "stats": {}, "files": files, "new_provider_task_ids": new_ids,
            "new_paid_requests": len(new_ids) + abs(keys_after - keys_before),
            "assessment_after": {"mode": reassessed["verdict"]["mode"], "code": reassessed["verdict"]["code"]},
            "semantic_quality_verified": False, "node_started": False,
            "verified_at": datetime.utcnow().isoformat()}
