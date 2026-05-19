"""Сопоставление whisper-слов с ячейками (frames) сценария для получения
реальных таймкодов каждого кадра.

Алгоритм (простой, достаточно надёжный для коротких видео):
  1. Нормализуем слова сценария и whisper (приводим к нижнему регистру, убираем
     знаки препинания). Получаем две последовательности токенов.
  2. Жадный проход: для каждой ячейки по порядку списываем столько токенов
     whisper, сколько слов в ней. start_ts = start первого, end_ts = end последнего.
     Если ячейка короче/длиннее — добираем по границам (если whisper не нашёл
     слово, пропускаем и берём следующее).
  3. На выходе: обновлённые start_ts / end_ts / duration_seconds на каждом Frame.

Не идеальный, но даёт реальную длительность для FFmpeg-сборки. При больших
расхождениях падаем обратно на оценочные длительности.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.whisper import WordTS

_WORD_RE = re.compile(r"[^\wа-яА-ЯёЁ]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [t for t in _WORD_RE.split((text or "").lower()) if t]


@dataclass
class FrameTiming:
    frame_number: int
    start_ts: float
    end_ts: float
    duration: float


def map_frames(cells: list[tuple[int, str]], words: list[WordTS]) -> list[FrameTiming]:
    """cells = [(frame_number, voiceover_text), ...] в порядке произнесения.
    words = список WordTS из faster-whisper."""
    out: list[FrameTiming] = []
    w_idx = 0
    total_words = len(words)
    for frame_number, text in cells:
        tokens = _tokenize(text)
        if not tokens:
            # пустая ячейка — мало вероятно, но на всякий случай
            out.append(FrameTiming(frame_number, 0.0, 0.0, 0.0))
            continue
        n = len(tokens)
        # стараемся взять n слов из whisper, начиная с w_idx
        start_w = None
        end_w = None
        taken = 0
        probe = w_idx
        while probe < total_words and taken < n:
            start_w = start_w if start_w is not None else words[probe]
            end_w = words[probe]
            taken += 1
            probe += 1
        if start_w is None:
            # whisper-слов больше не осталось — равномерно растягиваем из прошлой точки
            last_end = out[-1].end_ts if out else 0.0
            out.append(FrameTiming(frame_number, last_end, last_end, 0.0))
            continue
        s, e = start_w.start, end_w.end if end_w else start_w.end
        if out and s < out[-1].end_ts:
            # не допускаем пересечений назад
            s = out[-1].end_ts
            e = max(e, s)
        out.append(FrameTiming(frame_number, round(s, 3), round(e, 3), round(max(e - s, 0.0), 3)))
        w_idx = probe
    return out
