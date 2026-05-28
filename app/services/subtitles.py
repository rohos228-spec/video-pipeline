"""Субтитры по word-level таймкодам Whisper."""

from __future__ import annotations

from app.services.whisper import WordTS

SubtitleCue = tuple[float, float, str]


def build_word_subtitle_cues(
    words: list[WordTS],
    *,
    max_words: int = 2,
) -> list[SubtitleCue]:
    """Собирает ASS-субтитры: не более `max_words` слов, тайминг = start/end Whisper."""
    if max_words < 1:
        raise ValueError("max_words must be >= 1")

    entries: list[SubtitleCue] = []
    buf: list[WordTS] = []
    for word in words:
        text = (word.word or "").strip()
        if not text:
            continue
        buf.append(word)
        if len(buf) >= max_words:
            entries.append(_cue_from_words(buf))
            buf = []
    if buf:
        entries.append(_cue_from_words(buf))
    return entries


def _cue_from_words(words: list[WordTS]) -> SubtitleCue:
    text = " ".join((w.word or "").strip() for w in words)
    return words[0].start, words[-1].end, text
