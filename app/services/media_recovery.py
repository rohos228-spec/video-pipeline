from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from loguru import logger

from app.services import media_resume, media_spend, spend_guard

KEY_FAMILY = {
    "images": re.compile(r"^frame_"),
    "videos": re.compile(r"^clip_"),
    "hero": re.compile(r"^hero_"),
    "items": re.compile(r"^predmet\d"),
    "music": re.compile(r"^music$"),
}
LEDGER_NODES = frozenset(KEY_FAMILY)
LOCAL_NODES = frozenset({"audio", "sfx_gen", "assemble"})
RETRY_SAME_MAX = 1
_MODE_ORDER = ("denied", "resume_poll", "resume_local", "retry_same", "nothing_to_do")

RECOVERY_PROFILES: dict[str, dict[str, Any]] = {
    "videos": {
        "resume": "Kie: опрос и скачивание по task id из журнала задач; Outsee: id нет, возобновить нельзя",
        "retry_same_max": RETRY_SAME_MAX,
        "retry_only_if": "все прежние платные задачи этого клипа достоверно упали (failed)",
        "never_if": "submit без известного исхода (Kie или Outsee), открытая задача, повтор уже был",
    },
    "hero": {
        "resume": "Kie: опрос по task id; промт героя v1 строится GPT заново, поэтому после рестарта "
                  "тот же запрос не восстанавливается",
        "retry_same_max": RETRY_SAME_MAX,
        "retry_only_if": "все прежние платные задачи достоверно упали",
        "never_if": "submit без известного исхода, открытая задача, повтор уже был",
    },
    "items": {
        "resume": "Kie: опрос и скачивание по task id; промт предмета детерминирован (стиль + описание)",
        "retry_same_max": RETRY_SAME_MAX,
        "retry_only_if": "все прежние платные задачи этого предмета достоверно упали",
        "never_if": "submit без известного исхода, открытая задача, повтор уже был",
    },
    "images": {
        "resume": "Kie: опрос и скачивание по task id; Outsee: id нет",
        "retry_same_max": RETRY_SAME_MAX,
        "retry_only_if": "все прежние платные задачи кадра достоверно упали",
        "never_if": "submit без известного исхода, открытая задача, повтор уже был",
    },
    "music": {
        "resume": "Kie Suno: опрос и скачивание по task id; незавершённая задача берёт сохранённый промт",
        "retry_same_max": RETRY_SAME_MAX,
        "retry_only_if": "прежняя задача Suno достоверно упала (failed)",
        "never_if": "submit без известного исхода, открытая задача, повтор уже был",
    },
    "audio": {
        "resume": "готовый файл озвучки на диске (voice_full или sidecar с тем же хэшем текста): без POST",
        "retry_same_max": RETRY_SAME_MAX,
        "retry_only_if": "нет файла, нет маркера .tts_uncertain_* и нет платной задачи с неизвестным исходом",
        "never_if": "маркер .tts_uncertain_* (таймаут или обрыв после отправки), потолок POST исчерпан",
    },
    "sfx_gen": {
        "resume": "событие с готовым файлом ElevenLabs не пересоздаётся (чекпоинт sfx_files по индексу)",
        "retry_same_max": RETRY_SAME_MAX,
        "retry_only_if": "откат на локальный синтез был после ответа провайдера (may_have_charged=no), "
                         "повтора по этому событию ещё не было",
        "never_if": "откат после таймаута или обрыва (may_have_charged=uncertain): мог быть списан платёж",
    },
    "assemble": {
        "resume": "нет платных вызовов; прежний итог не трогается до проверки нового",
        "retry_same_max": RETRY_SAME_MAX,
        "retry_only_if": "сборка или проверка итога не прошла (одна повторная попытка внутри ноды)",
        "never_if": "подтверждение approve_final нельзя обойти ни повтором, ни автоодобрением без проверки",
    },
}


def _verdict(mode: str, code: str, **extra: Any) -> dict[str, Any]:
    return {"mode": mode, "code": code, **extra}


def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    if not items:
        return _verdict("nothing_to_do", "nothing_submitted", reasons=[])
    for mode in _MODE_ORDER:
        hit = [i for i in items if i["mode"] == mode]
        if hit:
            return _verdict(mode, hit[0]["code"], reasons=[{"key": i.get("key"), "code": i["code"]} for i in hit])
    return _verdict("denied", "unclassified", reasons=[])


def classify_states(states: list[dict[str, Any]]) -> dict[str, Any]:
    unresolved = [t for t in states if t["intent"] and t["status"] == "submit_unknown"]
    if unresolved:
        provider = "outsee" if any(t["api"] == "outsee" for t in unresolved) else "kie"
        return _verdict("denied", f"unknown_outcome_{provider}", provider=provider,
                        intent_ids=[t["task_id"] for t in unresolved])
    tasks = [t for t in states if not t["intent"]]
    open_tasks = [t for t in tasks if t["status"] in spend_guard.OPEN_TASK_STATUSES]
    if open_tasks:
        newest = max(open_tasks, key=lambda t: t["first_at"])
        return _verdict("resume_poll", "open_task", provider="kie", task_id=newest["task_id"], api=newest["api"],
                        model=newest["model"], status=newest["status"])
    failed = [t for t in tasks if t["status"] == "failed"]
    if failed:
        if len(failed) > RETRY_SAME_MAX:
            return _verdict("denied", "retry_cap_reached", provider="kie", failed_tasks=len(failed))
        return _verdict("retry_same", "provably_failed", provider="kie", failed_tasks=len(failed),
                        task_id=failed[-1]["task_id"])
    if any(t["status"] == "downloaded" for t in tasks):
        return _verdict("nothing_to_do", "downloaded", provider="kie")
    return _verdict("nothing_to_do", "nothing_submitted")


async def _ledger_keys(session, project_id: int, node_type: str, since: datetime) -> list[str]:
    from sqlalchemy import select
    await spend_guard.ensure_task_schema(session)
    rows = (await session.scalars(select(spend_guard.ProviderTaskLedger.key).where(
        spend_guard.ProviderTaskLedger.project_id == project_id,
        spend_guard.ProviderTaskLedger.created_at >= since).distinct())).all()
    family = KEY_FAMILY[node_type]
    return sorted(k for k in rows if k and family.match(k))


async def _assess_ledger_node(project_id: int, node_type: str) -> dict[str, Any]:
    if not media_resume.enabled():
        return {"keys": [], "verdict": _verdict("denied", "ledger_disabled", reasons=[
            {"key": None, "code": "ledger_disabled"}]), "ledger": "disabled"}
    await media_spend.drain()
    from app.db import SessionLocal
    since = datetime.utcnow() - timedelta(seconds=media_resume.window_s())
    items: list[dict[str, Any]] = []
    try:
        async with SessionLocal() as session:
            for key in await _ledger_keys(session, project_id, node_type, since):
                states = await spend_guard.find_task_states(
                    session, project_id=project_id, key=key, model=None, since=since)
                items.append({"key": key, **classify_states(states)})
    except Exception as exc:
        logger.warning("media_recovery: ledger read failed: {}", type(exc).__name__)
        return {"keys": [], "ledger": "unavailable", "verdict": _verdict(
            "denied", "ledger_unavailable", reasons=[{"key": None, "code": "ledger_unavailable"}])}
    return {"keys": items, "verdict": aggregate(items), "ledger": "ok"}


def _audio_dir(project) -> Path:
    return Path(project.data_dir) / "audio"


def assess_audio(project) -> dict[str, Any]:
    from app.services.elevenlabs_api import _SIDECAR_SUFFIX, _UNCERTAIN_PREFIX
    directory = _audio_dir(project)
    items: list[dict[str, Any]] = []
    try:
        markers = sorted(p.name for p in directory.glob(_UNCERTAIN_PREFIX + "*"))
        sidecars = sorted(p for p in directory.glob("*" + _SIDECAR_SUFFIX))
    except OSError:
        markers, sidecars = [], []
    finished = [p.with_name(p.name[: -len(_SIDECAR_SUFFIX)]) for p in sidecars]
    finished = [p for p in finished if p.is_file() and p.stat().st_size >= 1000]
    from app.services.frame_audio import find_voice_full_on_disk
    try:
        voice = find_voice_full_on_disk(Path(project.data_dir), meta=project.meta if isinstance(project.meta, dict) else None)
    except Exception:
        voice = None
    if voice is not None or finished:
        items.append({"key": "audio", **_verdict("resume_local", "finished_file_on_disk", provider="elevenlabs",
                                                 files=[p.name for p in finished][:20],
                                                 voice_full=getattr(voice, "name", None),
                                                 stale_markers=markers)})
    elif markers:
        items.append({"key": "audio", **_verdict("denied", "unknown_outcome_elevenlabs", provider="elevenlabs",
                                                 markers=markers)})
    if not items:
        items.append({"key": "audio", **_verdict("retry_same", "no_paid_job_and_no_marker", provider="elevenlabs")})
    return {"keys": items, "verdict": aggregate(items), "ledger": "n/a"}


def assess_sfx(project) -> dict[str, Any]:
    from app.services.ai_result_io import ai_job_checkpoint
    cp = ai_job_checkpoint(project, "sfx_files")
    files = cp.get("files") if isinstance(cp, dict) and isinstance(cp.get("files"), list) else []
    items: list[dict[str, Any]] = []
    prog = ai_job_checkpoint(project, "sfx_progress")
    inflight = prog.get("inflight") if isinstance(prog, dict) and isinstance(prog.get("inflight"), dict) else {}
    for idx in inflight:
        items.append({"key": f"sfx_{idx}", **_verdict("denied", "unknown_outcome_elevenlabs", provider="elevenlabs",
                                                      error_class="InterruptedAfterSend")})
    for rec in files:
        if not isinstance(rec, dict):
            continue
        key = f"sfx_{rec.get('idx')}"
        fb = rec.get("provider_fallback")
        if not isinstance(fb, dict):
            continue
        if rec.get("provider_retry"):
            items.append({"key": key, **_verdict("denied", "retry_cap_reached", provider="elevenlabs")})
        elif fb.get("may_have_charged") == "no":
            items.append({"key": key, **_verdict("retry_same", "fallback_after_provider_answer",
                                                 provider="elevenlabs", error_class=fb.get("error_class"))})
        else:
            items.append({"key": key, **_verdict("denied", "unknown_outcome_elevenlabs", provider="elevenlabs",
                                                 error_class=fb.get("error_class"))})
    return {"keys": items, "verdict": aggregate(items), "ledger": "n/a"}


async def assess(project, node_type: str) -> dict[str, Any]:
    if node_type in LEDGER_NODES:
        out = await _assess_ledger_node(project.id, node_type)
    elif node_type == "audio":
        out = assess_audio(project)
    elif node_type == "sfx_gen":
        out = assess_sfx(project)
    elif node_type == "assemble":
        item = {"key": "final", **_verdict("retry_same", "local_only_no_provider_spend")}
        out = {"keys": [item], "verdict": aggregate([item]), "ledger": "n/a"}
    else:
        raise ValueError(f"no media recovery profile for node type {node_type!r}")
    return {"node_type": node_type, "project_id": project.id, "profile": RECOVERY_PROFILES.get(node_type),
            "retry_same_max": RETRY_SAME_MAX, "submits_new_paid_request": False, **out}


async def resume_poll(project, node_type: str) -> dict[str, Any]:
    if node_type not in LEDGER_NODES:
        raise ValueError(f"resume_poll needs a ledger node type, got {node_type!r}")
    report = await assess(project, node_type)
    stats = {"checked": 0, "downloaded": 0, "failed": 0, "pending": 0, "errors": 0}
    resumed: list[dict[str, Any]] = []
    from app.db import SessionLocal
    since = datetime.utcnow() - timedelta(seconds=media_resume.window_s())
    with media_spend.resume_only():
        for item in report["keys"]:
            if item["mode"] != "resume_poll":
                continue
            key = item["key"]
            outcome, error = None, None
            try:
                async with SessionLocal() as session:
                    states = await spend_guard.find_task_states(
                        session, project_id=project.id, key=key, model=None, since=since)
                task = next((t for t in states if t["task_id"] == item["task_id"]), None)
                if task is None:
                    outcome = "task_not_found"
                else:
                    outcome = await media_resume._reattach_one(project, task, key, stats)
            except Exception as exc:
                stats["errors"] += 1
                error = type(exc).__name__
            resumed.append({"key": key, "task_id": item["task_id"], "outcome": outcome, "error": error})
    return {**report, "resumed": resumed, "stats": stats, "new_paid_requests": 0}
