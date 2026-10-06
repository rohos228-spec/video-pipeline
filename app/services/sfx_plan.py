"""Агент-планировщик звуков сопровождения (SFX) по временной шкале.

Вход: кадры с таймингом (R15/duration), сцены, действия, речевые окна.
GPT → ``sfx_events`` JSON → валидация (границы таймлайна, длительности,
каталог видов, плотность) → повтор с фидбеком → чекпоинт + sfx_plan.json.

Выход — вход шага генерации звуков (``sfx_gen``) и микса в монтаже.
"""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
from typing import Any
import wave

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.ai_result_io import TextJobFailed, parse_json_object, text_job

# Каталог видов звуков сопровождения (понятен и GPT, и локальному синтезу).
SFX_KINDS: tuple[str, ...] = (
    "whoosh",      # свист-переход (свип шума)
    "hit",         # удар/акцент (импульс + затухание)
    "riser",       # нарастание (подъём тона + шум)
    "ambience",    # фон-атмосфера (шумовая подложка)
    "transition",  # короткий переход (спад вниз)
    "stinger",     # акцент-стингер (короткий аккорд)
    "foley",       # бытовой звук действия (шаги/стук — шумовые постукивания)
)

MAX_EVENTS_PER_FRAME = 3
MIN_GAP_S = 0.8
MIN_DUR_S = 0.3
MAX_DUR_S = 8.0


@dataclass(frozen=True)
class SfxEvent:
    frame_number: int
    t_start: float  # секунды от начала ролика
    duration: float
    kind: str
    prompt: str  # EN-описание для ИИ-генератора
    gain: float  # 0.05–1.0
    duck: bool  # True — приглушить под речью (низкий gain, фоновая роль)

    def to_dict(self) -> dict[str, Any]:
        return {
            "frame_number": self.frame_number,
            "t_start": self.t_start,
            "duration": self.duration,
            "kind": self.kind,
            "prompt": self.prompt,
            "gain": self.gain,
            "duck": self.duck,
        }


def frame_timeline(frames: list[Frame]) -> list[dict[str, float]]:
    """Старт/конец кадра на таймлайне: R15 из attrs нет — по duration_seconds."""
    out: list[dict[str, float]] = []
    cursor = 0.0
    for fr in frames:
        dur = float(fr.duration_seconds or 0.0) or 3.0
        out.append({"frame_number": int(fr.number), "start": cursor, "end": cursor + dur})
        cursor += dur
    return out


def validate_events(
    events: list[SfxEvent], *, total_duration: float
) -> list[str]:
    """Проблемы плана (пустой список = ок)."""
    problems: list[str] = []
    if not events:
        return ["пустой план: ни одного звукового события"]
    per_frame: dict[int, int] = {}
    last_t = -10.0
    for i, e in enumerate(events):
        where = f"событие {i + 1} (кадр {e.frame_number})"
        if e.kind not in SFX_KINDS:
            problems.append(f"{where}: вид {e.kind!r} не из каталога {sorted(SFX_KINDS)}")
        if not (0.0 <= e.t_start < total_duration):
            problems.append(
                f"{where}: t_start {e.t_start:.2f}с вне таймлайна 0..{total_duration:.2f}с"
            )
        if not (MIN_DUR_S <= e.duration <= MAX_DUR_S):
            problems.append(
                f"{where}: длительность {e.duration:.2f}с вне {MIN_DUR_S}..{MAX_DUR_S}с"
            )
        if not (0.05 <= e.gain <= 1.0):
            problems.append(f"{where}: gain {e.gain} вне 0.05..1.0")
        if not e.prompt.strip():
            problems.append(f"{where}: пустой prompt")
        if e.t_start < last_t + MIN_GAP_S:
            problems.append(
                f"{where}: ближе {MIN_GAP_S}с к предыдущему ({e.t_start:.2f} < {last_t:.2f})"
            )
        last_t = max(last_t, e.t_start)
        per_frame[e.frame_number] = per_frame.get(e.frame_number, 0) + 1
        if per_frame[e.frame_number] > MAX_EVENTS_PER_FRAME:
            problems.append(
                f"кадр {e.frame_number}: больше {MAX_EVENTS_PER_FRAME} событий"
            )
    return problems


def _parse_events(payload: dict[str, Any]) -> list[SfxEvent]:
    raw = payload.get("events")
    if not isinstance(raw, list):
        raise ValueError("нет списка events")
    out: list[SfxEvent] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"events[{i}] не объект")
        try:
            out.append(
                SfxEvent(
                    frame_number=int(item["frame_number"]),
                    t_start=round(float(item["t_start"]), 3),
                    duration=round(float(item["duration"]), 3),
                    kind=str(item["kind"]).strip().lower(),
                    prompt=str(item["prompt"]).strip(),
                    gain=float(item.get("gain") or 0.5),
                    duck=bool(item.get("duck", False)),
                )
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ValueError(f"events[{i}]: {e}") from e
    out.sort(key=lambda e: e.t_start)
    return out


_PLAN_PROMPT = """Ты — звукорежиссёр короткого вертикального ролика. По таймлайну кадров спланируй звуки сопровождения (SFX): акценты действия, переходы, атмосферу.

# ТАЙМЛАЙН КАДРОВ (JSON)
{timeline}

# СЦЕНЫ/ДЕЙСТВИЕ (JSON, может быть пусто)
{scenes}

# ПРАВИЛА
1. Звук усиливает момент: переход кадра (whoosh/transition), акцент действия (hit/stinger/foley), нарастание перед кульминацией (riser), фон локации (ambience, duck=true).
2. НЕ заглушай речь: во время плотного закадра — только тихие фоновые звуки (duck=true, gain ≤ 0.3).
3. Максимум {max_per_frame} события на кадр, пауза между событиями ≥ {min_gap}с, длительность 0.3–8с.
4. Всего 4–12 событий на ролик — меньше, но точнее.
5. prompt — на АНГЛИЙСКОМ, короткое описание звука для генератора ("deep cinematic whoosh", "soft footsteps on wood").

# ОТВЕТ — ТОЛЬКО JSON:
{{"events": [{{"frame_number": 1, "t_start": 0.0, "duration": 1.2, "kind": "whoosh", "prompt": "deep cinematic whoosh", "gain": 0.5, "duck": false}}]}}

kind — СТРОГО из: {kinds}. t_start — секунды от начала ролика (в пределах 0..{total:.1f}).
"""


async def plan_sfx_events(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
    *,
    max_attempts: int = 2,
) -> list[SfxEvent]:
    """Спланировать звуковые события по таймлайну (GPT + валидация)."""
    timeline = frame_timeline(frames)
    if not timeline:
        raise RuntimeError("sfx_plan: нет кадров")
    total = timeline[-1]["end"]

    scenes: list[dict[str, Any]] = []
    meta = project.meta if isinstance(project.meta, dict) else {}
    registry = meta.get("scene_registry")
    if isinstance(registry, list):
        scenes = [s for s in registry if isinstance(s, dict)][:40]
    if not scenes:
        for fr in frames:
            attrs = fr.attrs or {}
            scenes.append(
                {
                    "frame_number": int(fr.number),
                    "place": attrs.get("place") or "",
                    "main_action": attrs.get("main_action") or "",
                    "meaning": (fr.meaning or "")[:120],
                }
            )

    prompt = _PLAN_PROMPT.format(
        timeline=json.dumps(timeline, ensure_ascii=False),
        scenes=json.dumps(scenes, ensure_ascii=False),
        max_per_frame=MAX_EVENTS_PER_FRAME,
        min_gap=MIN_GAP_S,
        kinds=", ".join(SFX_KINDS),
        total=total,
    )

    await record_sfx_plan_input(
        project, frames, timeline=timeline, total_duration=total,
        scenes=scenes, prompt=prompt,
    )
    await _reset_invalid_checkpoint_for_recovery(project, total_duration=total)

    # The closures only watch what text_job already sees (last reply, last parsed payload);
    # they change neither parsing nor validation nor the retry loop.
    seen: dict[str, Any] = {"reply": None, "payload": None, "parse_failed": False, "shape_error": None}

    def _parse(reply: str) -> dict[str, Any]:
        seen["reply"] = reply
        try:
            payload = parse_json_object(reply)
        except ValueError:
            seen["payload"], seen["parse_failed"] = None, True
            raise
        seen["parse_failed"] = False
        return payload

    def _validate(payload: dict[str, Any]) -> list[str]:
        seen["payload"] = payload
        try:
            return validate_events(_parse_events(payload), total_duration=total)
        except ValueError as error:
            seen["shape_error"] = error
            raise

    try:
        result = await text_job(
            project,
            name="sfx_plan",
            prompt=prompt,
            parse=_parse,
            validate=_validate,
            max_attempts=max_attempts,
        )
    except TextJobFailed as error:
        # The model answered, but never with a usable plan. Typed evidence for diagnosis:
        # a reply that is not a JSON object is invalid_json, a parsed plan that broke the
        # timeline rules is a rejected contract (no automatic retry for that one).
        events: list[SfxEvent] = []
        if seen["payload"] is not None:
            try:
                events = _parse_events(seen["payload"])
            except ValueError:
                events = []
        await record_sfx_parse(
            seen["payload"], events, [str(error)], source="model", reply=seen["reply"],
            parse_error=error, parsed=not seen["parse_failed"] and bool(events),
        )
        raise
    except ValueError as error:
        if error is seen["shape_error"]:
            await record_sfx_parse(
                seen["payload"], [], [str(error)], source="model", reply=seen["reply"],
                parse_error=error,
            )
        raise
    source = "checkpoint" if result.attempts == 0 else "model"
    reply = seen["reply"] if source == "model" else None
    try:
        events = _parse_events(result.payload)
    except ValueError as error:
        await record_sfx_parse(
            result.payload, [], [], source=source, parse_error=error, reply=reply,
        )
        raise
    # text_job returns checkpoints unchecked; validate against this timeline.
    problems = validate_events(events, total_duration=total)
    if problems:
        error = RuntimeError(f"sfx_plan: не прошло валидацию: {problems}")
        await record_sfx_parse(
            result.payload, events, problems, source=source, parse_error=error,
            reply=reply,
        )
        raise error
    await record_sfx_parse(result.payload, events, problems, source=source, reply=reply)

    # Артефакт на диск.
    out_path = project.data_dir / "sfx_plan.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {"total_duration": total, "events": [e.to_dict() for e in events]},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    logger.info(
        "[#{}] sfx_plan: {} событий на {:.1f}с (attempts={})",
        project.id,
        len(events),
        total,
        result.attempts,
    )
    return events


def load_sfx_plan(project: Project) -> list[SfxEvent] | None:
    """План из чекпоинта meta.ai_jobs['sfx_plan'] или sfx_plan.json."""
    from app.services.ai_result_io import ai_job_checkpoint

    cached = ai_job_checkpoint(project, "sfx_plan")
    if isinstance(cached, dict) and isinstance(cached.get("events"), list):
        try:
            events = _parse_events(cached)
            _annotate_selected_sfx_plan(project, "checkpoint", cached)
            return events
        except ValueError:
            pass
    data_dir = getattr(project, "data_dir", None)
    if data_dir is None:
        return None
    path: Path = data_dir / "sfx_plan.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            events = _parse_events(data)
            _annotate_selected_sfx_plan(project, "file", data)
            return events
        except (ValueError, json.JSONDecodeError):
            return None
    return None


# The following hooks deliberately live beside the original SFX parser.  They
# only describe values which the native code has already loaded or committed;
# they never validate again, alter a checkpoint, or advance a project.
_SFX_FRAME_FIELDS = ("id", "project_id", "uuid", "number", "sort_key",
                     "voiceover_text", "meaning", "duration_seconds", "attrs")


def _sfx_project_id(project):
    if project is None:
        return None
    from sqlalchemy import inspect
    state = inspect(project, raiseerr=False)
    return state.identity[0] if state is not None and state.identity else vars(project).get("id")


def _sfx_context(project=None, *, node_type=None):
    from app.services.observation import _attempt
    ctx = _attempt.get()
    if (ctx is None or ctx.scope.observer_state not in {"active", "degraded"}
            or getattr(ctx, "native_attempt_closed", False) or not ctx.attempt_id
            or ctx.node_key not in ctx.scope.node_keys):
        return None
    if project is not None and _sfx_project_id(project) != ctx.scope.project_id:
        return None
    if node_type is not None and getattr(ctx, "native_node_type", None) != node_type:
        return None
    return ctx


def _sfx_entry(ctx):
    entries = getattr(ctx, "sfx_journal", None)
    if entries is None:
        entries = {}
        ctx.sfx_journal = entries
    return entries.setdefault((ctx.scope.execution_id, ctx.node_key, ctx.attempt_id), {})


async def _sfx_fact(ctx, kind, payload, *, body=None, request_id=None):
    """Save a redacted full body once and associate it with an immutable fact."""
    from app.services.observation import _request, record_fact
    details = dict(payload)
    if body is not None:
        try:
            details["body"] = ctx.observer.save_body(ctx.scope.execution_id, body)
        except Exception as error:
            ctx.observer.mark_gap(ctx.scope.execution_id, error)
            details["body"] = {"gap": "sfx_body_unavailable", "error_type": type(error).__name__}
    token = _request.set(request_id)
    try:
        await record_fact(kind, details, request_id=request_id)
    finally:
        _request.reset(token)
    return details.get("body")


def _frame_snapshot(frame):
    return {field: deepcopy(getattr(frame, field)) for field in _SFX_FRAME_FIELDS}


def _annotate_selected_sfx_plan(project, source, payload):
    """A transient attempt annotation; it never reaches project metadata."""
    ctx = _sfx_context(project, node_type="sfx_gen")
    if ctx is not None:
        ctx.sfx_selected_plan_source = source
        ctx.sfx_selected_plan_payload = deepcopy(payload)


async def record_sfx_plan_input(project, frames, *, timeline, total_duration, scenes, prompt):
    ctx = _sfx_context(project, node_type="sfx_plan")
    if ctx is None or frames is None:
        return
    frames = list(frames)
    if any(getattr(frame, "project_id", None) != ctx.scope.project_id for frame in frames):
        return
    cached = deepcopy(((getattr(project, "meta", None) or {}).get("ai_jobs", {})
                       .get("sfx_plan")))
    snapshot = {"project_id": ctx.scope.project_id, "frames": [_frame_snapshot(frame) for frame in frames],
                "timeline": deepcopy(timeline), "total_duration": total_duration,
                "scenes": deepcopy(scenes), "prompt": prompt, "cached_payload": cached}
    from app.services.observation import digest
    entry = _sfx_entry(ctx)
    if entry.get("input") is not None:
        return
    ref = await _sfx_fact(ctx, "node_input", {"contract": "native_sfx_plan",
                          "input_sha256": digest(snapshot)}, body=snapshot)
    entry.update(input=deepcopy(snapshot), input_sha256=digest(snapshot), input_body=ref)


async def record_sfx_generation_input(project, frames, *, source, payload, events, provider):
    ctx = _sfx_context(project, node_type="sfx_gen")
    if ctx is None or frames is None:
        return
    frames = list(frames)
    if any(getattr(frame, "project_id", None) != ctx.scope.project_id for frame in frames):
        return
    normalized = [event.to_dict() for event in list(events or ())]
    snapshot = {"project_id": ctx.scope.project_id, "frames": [_frame_snapshot(frame) for frame in frames],
                "source": source, "payload": deepcopy(payload), "events": normalized,
                "provider": provider}
    from app.services.observation import digest
    entry = _sfx_entry(ctx)
    if entry.get("input") is not None:
        return
    ref = await _sfx_fact(ctx, "node_input", {"contract": "native_sfx_gen",
                          "input_sha256": digest(snapshot)}, body=snapshot)
    entry.update(input=deepcopy(snapshot), input_sha256=digest(snapshot), input_body=ref)


async def record_sfx_parse(payload, events, problems, *, source, reply=None, parse_error=None,
                           request_id=None, parsed=None):
    """Journal the native parse/validation outcome of one sfx_plan result.

    payload_required / payload_present follow the same contract as the text nodes: a model
    reply without a usable plan is a typed invalid_json / missing_payload incident for
    diagnosis, while a parsed plan that broke the timeline rules stays a rejected contract.
    `parsed` overrides the legacy rule (parsed = events present or a validation RuntimeError)
    for the one case the legacy rule gets wrong: a reply that was not a JSON object at all."""
    ctx = _sfx_context()
    if ctx is None or getattr(ctx, "native_node_type", None) != "sfx_plan":
        return
    from app.services.observation import completed_model_request_id, digest
    normalized = [event.to_dict() for event in list(events or ())]
    raw_reply = reply is not None and source == "model"
    syntax_valid = None
    if raw_reply:
        try:
            json.loads(reply)
            syntax_valid = True
        except (TypeError, ValueError):
            syntax_valid = False
    performed = parse_error is None
    schema_valid = False if parse_error is not None else bool(normalized and not problems)
    bound_request = request_id or (completed_model_request_id() if raw_reply else None)
    if parsed is None:
        parsed = bool(normalized) or isinstance(parse_error, RuntimeError)
    payload_present = bool(normalized) or (
        isinstance(payload, dict) and isinstance(payload.get("events"), list) and bool(payload["events"]))
    detail = {"contract": "native_sfx_plan", "source": source,
              "syntax_valid": syntax_valid, "parsed": parsed,
              "schema_valid": schema_valid, "error_type": type(parse_error).__name__ if parse_error else None,
              # A payload is "required" only of a model reply; a replayed checkpoint is not a
              # model answer, so its failure stays a rejected contract, never invalid_json.
              "payload_required": source == "model", "payload_present": payload_present,
              "nonempty_payload_required": source == "model",
              "problems": deepcopy(list(problems or ())), "sha256": digest(normalized)}
    _sfx_entry(ctx)["parse_request_id"] = bound_request
    await _sfx_fact(ctx, "parse_result", detail,
                    body={"payload": deepcopy(payload), "events": normalized}, request_id=bound_request)
    await _sfx_fact(ctx, "runtime_check", {"check": "native_sfx_events_validation", "performed": performed,
                    "status": "passed" if schema_valid else "failed", "business_commit_claim": False},
                    request_id=bound_request)


async def _reset_invalid_checkpoint_for_recovery(project, *, total_duration):
    """A supervisor retry must reach the model: drop a persisted checkpoint that fails the
    current timeline rules (text_job would otherwise replay it with zero requests and fail
    the same way). Only inside a recovery attempt; an ordinary run keeps the native behaviour
    (an invalid cache is rejected without a model call). Returns True when it dropped one."""
    ctx = _sfx_context(project, node_type="sfx_plan")
    if ctx is None or ctx.session.info.get("recovery_attempt_id") != ctx.attempt_id:
        return False
    from app.services.ai_result_io import ai_job_checkpoint, drop_ai_job_checkpoint
    cached = ai_job_checkpoint(project, "sfx_plan")
    if cached is None:
        return False
    try:
        problems = validate_events(_parse_events(cached), total_duration=total_duration)
    except ValueError as error:
        problems = [str(error)]
    if not problems:
        return False
    drop_ai_job_checkpoint(project, "sfx_plan")
    await _sfx_fact(ctx, "runtime_check", {
        "check": "sfx_plan_checkpoint_reset", "performed": True, "status": "passed",
        "reason": "persisted checkpoint failed validation and was dropped before the supervisor retry",
        "problems": problems[:5], "business_commit_claim": False})
    return True


def record_sfx_plan_applied(project, events):
    """Stage the honest application fact of a finished plan.

    plan_sfx.run calls this right before its own commit: the checkpoint, sfx_plan.json and
    meta.sfx_plan are the application of this node. The fact is staged on the business
    session, so it becomes visible only together with that commit (a rollback drops it).
    Recovery verification needs it (data_applied + an accepted payload)."""
    ctx = _sfx_context(project, node_type="sfx_plan")
    if ctx is None:
        return
    from app.services.observation import emit
    entry = _sfx_entry(ctx)
    entry["step_finished"] = True
    emit("data_applied", {"result": {"ok": True, "node_kind": "sfx_plan", "events": len(events or ()),
                                     "file": "sfx_plan.json", "checkpoint": "meta.ai_jobs.sfx_plan"},
                          "transactional": True}, request_id=entry.get("parse_request_id"))


def record_sfx_plan_pass_through(project):
    """SFX_ENABLED=false: the step reached sfx_plan_ready without planning anything."""
    ctx = _sfx_context(project, node_type="sfx_plan")
    if ctx is not None:
        _sfx_entry(ctx)["step_finished"] = True


def _sfx_wav_record(record, by_number):
    path = Path(str(record.get("path") or "")).resolve()
    if not path.is_file():
        raise FileNotFoundError(str(path))
    with wave.open(str(path), "rb") as reader:
        if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (1, 2, 44100):
            raise ValueError("unsupported native sfx wav")
        count = reader.getnframes()
        pcm = reader.readframes(count)
    if not count:
        raise ValueError("empty native sfx wav")
    power = sum(sample[0] ** 2 for sample in __import__("struct").iter_unpack("<h", pcm)) / count
    if power <= 0:
        raise ValueError("silent native sfx wav")
    frame = by_number.get(record.get("frame_number"))
    if frame is None:
        raise ValueError("native sfx source frame unavailable")
    return {"idx": record.get("idx"), "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "duration_seconds": count / 44100,
            "rms_db": 20 * math.log10(math.sqrt(power) / 32768),
            "provider": record.get("provider"),
            "source_frame": {field: getattr(frame, field) for field in ("id", "project_id", "uuid", "number")},
            "artifact_row": None}


async def observe_sfx_result(session, project, node_type="sfx_plan"):
    ctx = _sfx_context(project, node_type=node_type)
    if ctx is None or session is None or session.bind is not getattr(ctx.session, "bind", None):
        return
    entry = _sfx_entry(ctx)
    if entry.get("result_started"):
        return
    entry["result_started"] = True
    proof = {"status": "unknown", "reason": "captured native sfx input/body unavailable", "errors": [],
             "input_sha256": entry.get("input_sha256"), "files": [], "managed_eligible": False,
             "business_commit_claim": False, "semantic_quality_verified": False, "mix_verified": False}
    output = {"project_id": ctx.scope.project_id, "node_type": node_type}
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from app.models import Frame, Project as ProjectModel
        from sqlalchemy import select
        async with async_sessionmaker(session.bind, expire_on_commit=False, autoflush=False)() as reader:
            native = await reader.get(ProjectModel, ctx.scope.project_id)
            if native is None:
                raise LookupError("native sfx project unavailable")
            frames = list((await reader.scalars(select(Frame).where(Frame.project_id == native.id)
                         .order_by(Frame.sort_key, Frame.number))).all())
            jobs = deepcopy(((native.meta or {}).get("ai_jobs", {})))
            if node_type == "sfx_plan":
                payload = jobs.get("sfx_plan")
                plan_file = native.data_dir / "sfx_plan.json"
                output.update(plan_payload=payload,
                              plan_file=json.loads(plan_file.read_text(encoding="utf-8")) if plan_file.is_file() else None)
                passed = entry.get("input") is not None and isinstance(payload, dict) and output["plan_file"] is not None
            else:
                records = jobs.get("sfx_files", {}).get("files", []) if isinstance(jobs.get("sfx_files"), dict) else []
                by_number = {frame.number: frame for frame in frames}
                expected_events = (entry.get("input") or {}).get("events")
                if not isinstance(expected_events, list) or len(records) != len(expected_events):
                    raise ValueError("native sfx file coverage differs from captured plan")
                root = (native.data_dir / "sfx").resolve()
                for record in records:
                    candidate = Path(str(record.get("path") or "")).resolve()
                    if not candidate.is_relative_to(root):
                        raise ValueError("native sfx checkpoint references a foreign file")
                files = [_sfx_wav_record(record, by_number) for record in records]
                output.update(files=files)
                proof["files"] = files
                passed = entry.get("input") is not None and bool(files)
            if passed:
                proof.update(status="passed", reason=None)
    except Exception as error:
        proof.update(status="failed" if entry.get("input") is not None else "unknown",
                     reason="committed native sfx result unavailable", errors=[type(error).__name__],
                     error_type=type(error).__name__)
    await _sfx_fact(ctx, "node_output", {"contract": "native_" + node_type,
                    "sha256": hashlib.sha256(json.dumps(output, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest(),
                    "source": "committed original executor rows; independent read session"}, body=output)
    await _sfx_fact(ctx, "node_result_verification", proof, body=proof)
    return proof


async def observe_sfx_plan_result(session, project, node_type="sfx_plan"):
    """observe_sfx_result plus the managed-mode gate for sfx_plan only.

    In an observation scope this is exactly observe_sfx_result (journal only, never raises).
    In a managed scope, when the native step FINISHED (it staged its application fact or
    passed through) but the independent re-read does not confirm the plan, the execution is
    held and the attempt fails, as for split: a pass-through with SFX_ENABLED=false or an
    unreadable plan never counts as a verified result. A native failure is left alone."""
    ctx = _sfx_context(project, node_type=node_type)
    try:
        proof = await observe_sfx_result(session, project, node_type)
    except Exception as error:
        if ctx is not None:
            ctx.observer.mark_gap(ctx.scope.execution_id, error)
        if ctx is not None and ctx.scope.mode == "managed":
            raise
        return
    if (proof is None or ctx is None or ctx.scope.mode != "managed"
            or not _sfx_entry(ctx).get("step_finished") or proof["status"] == "passed"):
        return
    from app.services.actions_v1 import ActionSupervisor
    await ActionSupervisor(ctx.observer.master).set_held(
        project_id=ctx.scope.project_id, execution_id=ctx.scope.execution_id, held=True,
        initiator="native_sfx_plan_verifier", rationale=str(proof.get("reason") or proof["status"]),
        idempotency_key="sfx-plan-verify:" + ctx.attempt_id, operator_request=False)
    raise RuntimeError("native sfx plan verification not confirmed: " + str(proof.get("reason")))


def sfx_plan_contract():
    """What the shipped sfx_plan executor requires and what a supervisor retry may and may not do."""
    return {
        "node_type": "sfx_plan",
        "executor": "plan_sfx.run -> sfx_plan.plan_sfx_events -> ai_result_io.text_job",
        "input": "Frame rows ordered by sort_key/number (duration_seconds, scene attrs) and the "
                 "_PLAN_PROMPT constant of app/services/sfx_plan.py; no attachments, no images",
        "output": {"checkpoint": "Project.meta.ai_jobs.sfx_plan", "file": "sfx_plan.json",
                   "flag": "Project.meta.sfx_plan", "frames_written": False},
        "format": '{"events": [{"frame_number", "t_start", "duration", "kind", "prompt", "gain", "duck"}]}',
        "kinds": list(SFX_KINDS),
        "rules": {"max_events_per_frame": MAX_EVENTS_PER_FRAME, "min_gap_s": MIN_GAP_S,
                  "duration_s": [MIN_DUR_S, MAX_DUR_S], "gain": [0.05, 1.0],
                  "t_start": "inside the frame timeline", "prompt": "non-empty"},
        "payload_required": True,
        "payload_required_note": "of a model reply only; a replayed checkpoint is not a model answer",
        "data_applied": "staged by plan_sfx.run in the same transaction as the plan commit "
                        "(record_sfx_plan_applied); absent for a pass-through",
        "checkpoint_reset": "inside a supervisor recovery attempt a persisted checkpoint that fails "
                            "the current timeline rules is dropped before the model call",
        "native_attempts_per_run": 2,
        "native_json_retry_fact": False,
        "verification": "node_result_verification: independent re-read of the plan checkpoint "
                        "and sfx_plan.json; managed scope holds the execution when it is not 'passed'",
        "retry_policy": "ONE supervisor retry_same per node and execution, only for invalid_json / "
                        "missing_payload of a model reply with no data applied; never starts sfx_gen "
                        "and never changes frames",
        "human_approval_preserved": True,
        "semantic_quality_verified": False,
        "arbitrary_shell_code_paths": False,
        "write_authority": "existing worker only",
    }


def sfx_plan_current_output(project, frames):
    """Read-only description of the plan the project holds now (no write, no model call)."""
    from app.services.ai_result_io import ai_job_checkpoint
    meta = project.meta if isinstance(getattr(project, "meta", None), dict) else {}
    cached = ai_job_checkpoint(project, "sfx_plan")
    out = {"checkpoint_present": cached is not None, "plan_flag": bool(meta.get("sfx_plan")),
           "frame_count": len(frames), "events": None, "problems": []}
    if cached is None:
        return out
    try:
        events = _parse_events(cached)
        out["events"] = len(events)
        out["problems"] = validate_events(events, total_duration=frame_timeline(frames)[-1]["end"])             if frames else ["нет кадров"]
    except ValueError as error:
        out["problems"] = [str(error)]
    return out
