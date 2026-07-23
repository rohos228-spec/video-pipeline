"""NeMo word timestamp → seconds conversion."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.nvidia_asr import _hypothesis_words, _nemo_time_stride, _word_stamp_seconds


class _Cfg:
    def __init__(self, window_stride: float = 0.01, subsampling_factor: int = 8) -> None:
        self.preprocessor = {"window_stride": window_stride}
        self.encoder = {"subsampling_factor": subsampling_factor}


def test_nemo_time_stride_fastconformer_default() -> None:
    model = SimpleNamespace(cfg=_Cfg())
    assert _nemo_time_stride(model) == 0.08


def test_word_stamp_prefers_start_end_seconds() -> None:
    model = SimpleNamespace(cfg=_Cfg())
    start, end, text = _word_stamp_seconds(
        {"word": "привет", "start": 1.5, "end": 2.0, "start_offset": 10, "end_offset": 20},
        model,
    )
    assert text == "привет"
    assert start == 1.5
    assert end == 2.0


def test_word_stamp_offset_uses_subsampling_stride() -> None:
    model = SimpleNamespace(cfg=_Cfg(window_stride=0.01, subsampling_factor=8))
    start, end, text = _word_stamp_seconds(
        {"word": "тест", "start_offset": 100, "end_offset": 150},
        model,
    )
    assert text == "тест"
    assert start == 8.0
    assert end == 12.0


def test_hypothesis_words_reads_timestep_fallback() -> None:
    hyp = SimpleNamespace(
        timestamp=None,
        timestep={
            "word": [
                {"word": "раз", "start": 0.0, "end": 0.4},
                {"word": "два", "start": 0.5, "end": 0.9},
            ],
        },
    )
    model = SimpleNamespace(cfg=_Cfg())
    words = _hypothesis_words(hyp, model)
    assert len(words) == 2
    assert words[0].word == "раз"
    assert words[1].start == 0.5
