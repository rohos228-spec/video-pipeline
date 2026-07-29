"""sanitize_voiceover_text: убрать ИТОГО и мета-отчёт GPT."""

from __future__ import annotations

from app.services.voiceover_sanitize import sanitize_voiceover_text


_POLLUTED = (
    "Извлекаю из «Общего плана» только подтверждённую последовательность событий "
    "и свожу её в единый минутный рассказ. Затем проверю точное число символов и "
    "подготовлю текстовую запись для строки закадрового текста без смещения номера "
    "Excel.Каркас сохраняет хронологию: туринский коллапс, спорная легенда. "
    "Формулировки про сифилис и CADASIL будут явно обозначены как неподтверждённые "
    "версии, чтобы не выдавать ретроспективный диагноз за факт."
    "Третьего января 1889 года Ницше пережил психический коллапс на площади "
    "Карло Альберто в Турине. Позднее появилась история, будто перед падением "
    "он обнял лошадь. Точная причина распада осталась неизвестной.\n\n"
    "ИТОГО: 898 символов."
)


def test_sanitize_strips_itogo_and_process_preamble() -> None:
    out = sanitize_voiceover_text(_POLLUTED)
    assert "ИТОГО" not in out
    assert "Извлекаю" not in out
    assert "Каркас сохраняет" not in out
    assert out.startswith("Третьего января 1889")
    assert "Ницше пережил" in out


def test_sanitize_keeps_clean_voiceover() -> None:
    clean = (
        "Третьего января 1889 года Ницше пережил психический коллапс. "
        "Точная причина распада осталась неизвестной."
    )
    assert sanitize_voiceover_text(clean) == clean


def test_sanitize_strips_only_itogo_footer() -> None:
    text = (
        "Третьего января 1889 года Ницше пережил психический коллапс.\n\n"
        "ИТОГО: 120 символов."
    )
    out = sanitize_voiceover_text(text)
    assert out.endswith("коллапс.")
    assert "ИТОГО" not in out
