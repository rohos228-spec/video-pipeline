"""Агент разбора аудио: точные метки по словам.

NeMo ASR (слова с таймкодами) → сопоставление с закадровым текстом кадров
(difflib + монотонная интерполяция, ``mapper.align_script_tokens``) →
точная метка КАЖДОГО слова сценария: {текст, start, end, кадр, interpolated}.

Артефакты:
- ``word_marks.json`` в папке проекта (слова + spans кадров + отчёт);
- таблица ``asr_words`` (replace-per-project, привязка к кадрам);
- отчёт валидации: покрытие текста, монотонность, дрейф к длительности.

Используется шагом/подготовкой монтажа и планировщиком звуков (речевые
окна — куда звуки сопровождения НЕ ставить без ducking).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.mapper import (
    align_script_tokens,
    tokenize_display,
    tokenize_lower,
    whisper_token,
)
from app.services.whisper import WordTS


@dataclass
class WordMark:
    idx: int  # индекс токена сценария
    text: str  # слово как в сценарии (display)
    asr_word: str  # что реально распознано
    start: float
    end: float
    frame_number: int | None
    interpolated: bool  # True — слово вставлено интерполяцией (ASR его не сказал/иначе)


@dataclass
class FrameSpan:
    frame_number: int
    start_ts: float
    end_ts: float
    word_count: int


@dataclass
class WordMarksReport:
    words_total: int
    script_tokens: int
    coverage: float  # доля токенов, совпавших с ASR дословно
    monotonic: bool
    drift_s: float  # |конец последнего слова − длительность аудио|
    frames: list[FrameSpan] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "words_total": self.words_total,
            "script_tokens": self.script_tokens,
            "coverage": round(self.coverage, 4),
            "monotonic": self.monotonic,
            "drift_s": round(self.drift_s, 3),
            "frames": [vars(f) for f in self.frames],
            "problems": self.problems,
        }


def _frame_cells(frames: list[Frame]) -> list[tuple[int, str]]:
    """(frame_number, закадр ячейки) по порядку — только непустые."""
    cells: list[tuple[int, str]] = []
    for fr in frames:
        vo = (fr.voiceover_text or "").strip()
        if vo:
            cells.append((int(fr.number), vo))
    return cells


def build_marks_from_words(
    frames: list[Frame],
    words: list[WordTS],
    *,
    audio_duration: float | None = None,
) -> tuple[list[WordMark], list[FrameSpan], WordMarksReport]:
    """Сопоставление текста кадров с ASR-словами → метки + spans + отчёт.

    Чистая функция (без IO) — тестируется без NeMo.
    """
    cells = _frame_cells(frames)
    full_text = " ".join(text for _, text in cells)
    script_display = tokenize_display(full_text)
    script_lower = tokenize_lower(full_text)

    report = WordMarksReport(
        words_total=len(words),
        script_tokens=len(script_lower),
        coverage=0.0,
        monotonic=True,
        drift_s=0.0,
    )
    if not words or not script_lower:
        report.problems.append("пустой вход: нет слов ASR или текста")
        return [], [], report

    idx_map = align_script_tokens(script_lower, words)

    # Токен → кадр: границы ячеек в общем тексте.
    cell_of_token: list[int | None] = []
    pos = 0
    for frame_number, text in cells:
        n = len(tokenize_lower(text))
        for _ in range(n):
            cell_of_token.append(frame_number)
        pos += n
    # Хвост (если токенизация пошла иначе) — на последнюю ячейку.
    while len(cell_of_token) < len(script_lower):
        cell_of_token.append(cells[-1][0] if cells else None)

    # Дословное покрытие: токен == ASR-слову под ним.
    exact = 0
    for i, wi in enumerate(idx_map):
        if 0 <= wi < len(words) and whisper_token(words[wi]) == script_lower[i]:
            exact += 1
    report.coverage = exact / len(script_lower)

    # Метки слов: interpolated = соседний дубль индекса (ASR слово не сказал
    # или разбивка иная) — такие метки ориентировочные.
    marks: list[WordMark] = []
    seen_idx: set[int] = set()
    last_start = -1.0
    for i, wi in enumerate(idx_map):
        wi = max(0, min(wi, len(words) - 1))
        w = words[wi]
        start, end = float(w.start), float(w.end)
        if start < last_start:
            report.monotonic = False
        last_start = max(last_start, start)
        interp = wi in seen_idx
        seen_idx.add(wi)
        marks.append(
            WordMark(
                idx=i,
                text=script_display[i] if i < len(script_display) else script_lower[i],
                asr_word=(w.word or "").strip(),
                start=start,
                end=end,
                frame_number=cell_of_token[i] if i < len(cell_of_token) else None,
                interpolated=interp,
            )
        )

    # Spans кадров: от первого до последнего слова ячейки (монотонно).
    spans: list[FrameSpan] = []
    cursor = 0.0
    for frame_number, _text in cells:
        toks = [m for m in marks if m.frame_number == frame_number]
        if not toks:
            continue
        start = max(toks[0].start, cursor)
        end = max(toks[-1].end, start + 0.05)
        spans.append(
            FrameSpan(
                frame_number=frame_number,
                start_ts=round(start, 3),
                end_ts=round(end, 3),
                word_count=len(toks),
            )
        )
        cursor = start  # следующий кадр не раньше старта текущего
    report.frames = spans

    if audio_duration is not None and marks:
        report.drift_s = abs(marks[-1].end - audio_duration)
        if report.drift_s > 3.0:
            report.problems.append(
                f"дрейф к длительности аудио: {report.drift_s:.1f}с"
            )
    if report.coverage < 0.5:
        report.problems.append(f"низкое покрытие текста: {report.coverage:.0%}")
    if not report.monotonic:
        report.problems.append("метки слов не монотонны по времени")
    return marks, spans, report


async def build_word_marks(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
    audio_path: Path,
    *,
    language: str = "ru",
    model_name: str = "",
) -> WordMarksReport:
    """Полный разбор: NeMo ASR → метки слов → word_marks.json + asr_words."""
    from app.services import asr_words_store
    from app.services.ai_result_io import ffprobe_duration
    from app.services.nvidia_asr import transcribe_words_nvidia
    from app.settings import settings

    model = model_name or getattr(settings, "nvidia_asr_model", "")
    words = transcribe_words_nvidia(audio_path, model_name=model, language=language)
    if not words:
        raise RuntimeError(f"audio_marks: ASR не вернул слова для {audio_path.name}")

    duration = ffprobe_duration(audio_path)
    marks, spans, report = build_marks_from_words(frames, words, audio_duration=duration)

    # Артефакт на диск.
    out = {
        "audio": audio_path.name,
        "audio_duration": duration,
        "report": report.to_dict(),
        "marks": [vars(m) for m in marks],
    }
    out_path = project.data_dir / "word_marks.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    # Слова в БД с привязкой к кадрам (spans как сегменты).
    segments = [
        {"frame_number": s.frame_number, "start_ts": s.start_ts, "end_ts": s.end_ts}
        for s in spans
    ]
    await asr_words_store.replace_project_asr_words(
        session,
        project.id,
        words,
        backend="nemo",
        frame_segments=segments,
    )
    logger.info(
        "[#{}] audio_marks: {} слов, покрытие {:.0%}, кадров {}, проблем {}",
        project.id,
        len(marks),
        report.coverage,
        len(spans),
        len(report.problems),
    )
    return report
