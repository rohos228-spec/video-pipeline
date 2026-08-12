"""Агент-планировщик звуков сопровождения (SFX) по временной шкале.

Вход: кадры с таймингом (R15/duration), сцены, действия, речевые окна.
GPT → ``sfx_events`` JSON → валидация (границы таймлайна, длительности,
каталог видов, плотность) → повтор с фидбеком → чекпоинт + sfx_plan.json.

Выход — вход шага генерации звуков (``sfx_gen``) и микса в монтаже.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.ai_result_io import parse_json_object, text_job

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

    result = await text_job(
        project,
        name="sfx_plan",
        prompt=prompt,
        parse=parse_json_object,
        validate=lambda p: validate_events(
            _parse_events(p), total_duration=total
        ),
        max_attempts=max_attempts,
    )
    events = _parse_events(result.payload)

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
            return _parse_events(cached)
        except ValueError:
            pass
    data_dir = getattr(project, "data_dir", None)
    if data_dir is None:
        return None
    path: Path = data_dir / "sfx_plan.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return _parse_events(data)
        except (ValueError, json.JSONDecodeError):
            return None
    return None
