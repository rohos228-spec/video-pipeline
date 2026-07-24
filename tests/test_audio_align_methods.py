"""Tests for 5 audio-align methodologies."""

from __future__ import annotations

import pytest

from app.services.audio_align_methods import (
    ALIGN_METHODS,
    apply_align_method,
    list_align_methods,
    resolve_align_method,
)
from app.services.whisper import WordTS


def _cells_words():
    cells = [(i, f"слово{i} фраза") for i in range(1, 6)]
    words = []
    t = 0.0
    for i in range(1, 6):
        words.append(WordTS(f"слово{i}", t, t + 0.4, 1.0))
        words.append(WordTS("фраза", t + 0.4, t + 0.9, 1.0))
        t += 2.0
    return cells, words, 12.0


def test_list_has_five_methods() -> None:
    methods = list_align_methods()
    assert len(methods) == 5
    assert {m["id"] for m in methods} == {m.id for m in ALIGN_METHODS}


def test_resolve_unknown_raises() -> None:
    with pytest.raises(ValueError):
        resolve_align_method("nope")


@pytest.mark.parametrize("method_id", [m.id for m in ALIGN_METHODS])
def test_each_method_covers_master(method_id: str) -> None:
    cells, words, master = _cells_words()
    timings = apply_align_method(method_id, cells, words, master)
    assert len(timings) == 5
    assert timings[0].start_ts == 0.0
    assert abs(timings[-1].end_ts - master) < 0.02
    for prev, cur in zip(timings, timings[1:]):
        assert cur.start_ts >= prev.end_ts - 0.001


def test_uniform_equal_shares() -> None:
    cells, words, master = _cells_words()
    timings = apply_align_method("uniform", cells, words, master)
    fair = master / 5
    for t in timings:
        assert abs(t.duration - fair) < 0.05


def test_proportional_longer_text_gets_more() -> None:
    cells = [
        (1, "коротко"),
        (2, "очень длинная фраза с кучей слов внутри ячейки"),
    ]
    words = [
        WordTS("коротко", 0.0, 0.5, 1.0),
        WordTS("очень", 1.0, 1.3, 1.0),
        WordTS("длинная", 1.3, 1.6, 1.0),
        WordTS("фраза", 1.6, 1.9, 1.0),
        WordTS("с", 1.9, 2.0, 1.0),
        WordTS("кучей", 2.0, 2.3, 1.0),
        WordTS("слов", 2.3, 2.6, 1.0),
        WordTS("внутри", 2.6, 2.9, 1.0),
        WordTS("ячейки", 2.9, 3.2, 1.0),
    ]
    timings = apply_align_method("proportional", cells, words, master=10.0)
    assert timings[1].duration > timings[0].duration
