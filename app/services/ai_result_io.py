"""Слой отдачи/приёма ИИ-результатов (агентская дисциплина).

Единый конвейер для всех агентов программы:

  send → receive → parse → validate → (retry с фидбеком) → checkpoint

- ``text_job`` — текстовые ИИ-задачи (GPT): промт → JSON → валидация →
  повтор с ошибками в контексте → чекпоинт в ``project.meta["ai_jobs"]``.
- ``verify_audio_file`` — приём аудио-результата: ffprobe (длительность,
  кодек) + не-тишина (astats RMS). Брак → понятная ошибка, не молчаливый
  файл в монтаже.
- Чекпоинты per-job: повтор шага не пересоздаёт успешные результаты.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

# ── Чекпоинты ИИ-задач в project.meta["ai_jobs"] ──────────────────────────


def ai_job_checkpoint(project: Any, name: str) -> Any | None:
    """Сохранённый результат ИИ-задачи или None."""
    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return None
    jobs = meta.get("ai_jobs")
    if not isinstance(jobs, dict):
        return None
    return jobs.get(name)


def save_ai_job_checkpoint(project: Any, name: str, payload: Any) -> None:
    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return
    # Копия верхнего уровня обязательна: иначе SQLAlchemy не видит мутацию
    # JSON-колонки (тот же объект) и чекпоинт не сохраняется в БД.
    meta = dict(meta)
    jobs = meta.get("ai_jobs")
    jobs = dict(jobs) if isinstance(jobs, dict) else {}
    jobs[name] = payload
    meta["ai_jobs"] = jobs
    project.meta = meta


def drop_ai_job_checkpoint(project: Any, name: str) -> None:
    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return
    jobs = meta.get("ai_jobs")
    if isinstance(jobs, dict) and name in jobs:
        meta = dict(meta)
        jobs = dict(jobs)
        jobs.pop(name)
        meta["ai_jobs"] = jobs
        project.meta = meta


# ── Текстовая ИИ-задача (GPT → JSON → валидация → retry) ──────────────────


@dataclass
class TextJobResult:
    payload: Any
    attempts: int
    problems_log: list[list[str]] = field(default_factory=list)


async def text_job(
    project: Any,
    *,
    name: str,
    prompt: str,
    parse: Callable[[str], Any],
    validate: Callable[[Any], list[str]],
    max_attempts: int = 2,
    timeout: float = 900,
    use_checkpoint: bool = True,
) -> TextJobResult:
    """Отправить промт в GPT, распарсить, проверить; при браке — повтор с
    списком проблем в контексте. Успех кладётся в чекпоинт ``ai_jobs[name]``.

    ``parse`` бросает ValueError при не-JSON; ``validate`` возвращает список
    проблем (пустой = ок). При полном провале — RuntimeError с проблемами.
    """
    cached = ai_job_checkpoint(project, name) if use_checkpoint else None
    if cached is not None:
        logger.info("[#{}] ai_job {}: checkpoint — без GPT", getattr(project, "id", "?"), name)
        return TextJobResult(payload=cached, attempts=0)

    from app.services import gpt_client

    problems_log: list[list[str]] = []
    feedback: str | None = None
    for attempt in range(1, max_attempts + 1):
        text = prompt
        if feedback:
            text = f"{prompt}\n\n# ОШИБКИ ПРОШЛОЙ ПОПЫТКИ (исправь)\n{feedback}"
        reply = await gpt_client.gpt_ask_fresh(
            text, timeout=timeout, project_id=getattr(project, "id", None)
        )
        try:
            payload = parse(reply)
        except ValueError as e:
            problems = [f"невалидный ответ: {e}"]
            problems_log.append(problems)
            feedback = "\n".join(problems)
            logger.warning(
                "[#{}] ai_job {} attempt {}/{} parse: {}",
                getattr(project, "id", "?"),
                name,
                attempt,
                max_attempts,
                e,
            )
            continue
        problems = validate(payload)
        if not problems:
            if use_checkpoint:
                save_ai_job_checkpoint(project, name, payload)
            return TextJobResult(
                payload=payload, attempts=attempt, problems_log=problems_log
            )
        problems_log.append(problems)
        feedback = "\n".join(problems)
        logger.warning(
            "[#{}] ai_job {} attempt {}/{} problems: {}",
            getattr(project, "id", "?"),
            name,
            attempt,
            max_attempts,
            problems,
        )
    raise RuntimeError(f"ai_job {name}: не прошло валидацию: {problems_log[-1]}")


# ── Приём аудио-результата (верификация файла) ────────────────────────────


def ffprobe_duration(path: Path) -> float | None:
    """Длительность аудио/видео в секундах (ffprobe) или None."""
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(out.stdout.strip())
    except Exception:  # noqa: BLE001
        return None


def audio_rms_db(path: Path) -> float | None:
    """RMS-уровень в dB (ffmpeg astats) — детектор тишины/брака."""
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(path), "-af", "astats", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        for line in out.stderr.splitlines():
            if "RMS level dB" in line:
                return float(line.rsplit(" ", 2)[-2])
    except Exception:  # noqa: BLE001
        pass
    return None


def verify_audio_file(
    path: Path,
    *,
    expect_duration: float | None = None,
    duration_tol: float = 0.35,
    min_rms_db: float = -60.0,
) -> list[str]:
    """Проблемы с аудио-файлом (пустой список = ок).

    Проверки: файл существует и не пуст; ffprobe читает длительность;
    длительность в допуске от ожидаемой; RMS выше порога тишины.
    """
    problems: list[str] = []
    if not path.is_file():
        return [f"файл не создан: {path.name}"]
    if path.stat().st_size < 1000:
        problems.append(f"файл слишком мал ({path.stat().st_size} Б): {path.name}")
        return problems
    dur = ffprobe_duration(path)
    if dur is None or dur <= 0:
        problems.append(f"ffprobe не читает длительность: {path.name}")
        return problems
    if expect_duration is not None and abs(dur - expect_duration) > duration_tol:
        problems.append(
            f"длительность {dur:.2f}с вне допуска (ожидалась {expect_duration:.2f}с): {path.name}"
        )
    rms = audio_rms_db(path)
    if rms is not None and rms < min_rms_db:
        problems.append(f"тишина/брак (RMS {rms:.1f} dB): {path.name}")
    return problems


def parse_json_object(reply: str) -> dict[str, Any]:
    """Достать первый JSON-объект из ответа GPT (```json …``` или голый)."""
    text = reply.strip()
    if "```" in text:
        import re

        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            text = m.group(1)
    start = text.find("{")
    if start < 0:
        raise ValueError("в ответе нет JSON-объекта")
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError as e:
                    raise ValueError(f"JSON не парсится: {e}") from e
    raise ValueError("JSON-объект не закрыт")
