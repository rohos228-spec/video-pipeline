"""Тесты детекции фейковых равномерных таймкодов NeMo fallback."""

from app.services.asr.nvidia_backend import looks_like_fake_uniform_timestamps
from app.services.whisper import WordTS


def test_fake_uniform_025_detected() -> None:
    words = [
        WordTS("a", i * 0.25, (i + 1) * 0.25) for i in range(10)
    ]
    assert looks_like_fake_uniform_timestamps(words) is True


def test_realistic_timestamps_not_fake() -> None:
    words = [
        WordTS("hello", 0.0, 0.42),
        WordTS("мир", 0.55, 1.1),
        WordTS("снова", 1.2, 2.05),
        WordTS("тест", 2.1, 2.8),
    ]
    assert looks_like_fake_uniform_timestamps(words) is False


def test_english_model_blocked_for_ru() -> None:
    from app.services.asr.nvidia_backend import _assert_model_for_language
    import pytest

    with pytest.raises(RuntimeError, match="английская"):
        _assert_model_for_language("nvidia/parakeet-tdt-0.6b-v2", "ru")
