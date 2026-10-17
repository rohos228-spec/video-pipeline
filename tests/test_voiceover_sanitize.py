"""sanitize_voiceover_text: маркеры VOICEOVER + убрать ИТОГО/мета."""

from __future__ import annotations

from app.services.voiceover_sanitize import (
    ensure_voiceover_format_instruction,
    extract_scenario_block,
    extract_voiceover_block,
    sanitize_voiceover_text,
)


def test_extract_voiceover_markers() -> None:
    raw = (
        "Сначала план работы.\n"
        "<<<VOICEOVER>>>\n"
        "Третьего января 1889 года Ницше пережил коллапс.\n"
        "<<<END>>>\n"
        "ИТОГО: 50 символов."
    )
    assert extract_voiceover_block(raw) == (
        "Третьего января 1889 года Ницше пережил коллапс."
    )
    out = sanitize_voiceover_text(raw)
    assert out == "Третьего января 1889 года Ницше пережил коллапс."
    assert "ИТОГО" not in out
    assert "план работы" not in out


def test_unclosed_voiceover_block_stripped() -> None:
    """GPT открыл <<<VOICEOVER>>> и забыл <<<END>>> — маркер и мусорный
    префикс перед ним не должны попасть в закадр."""
    raw = (
        "`.\n<<<VOICEOVER>>>\n"
        "День на старой пасеке начинается раньше, чем поле наполнится звуком. "
        "Утренний обход начинается с летков."
    )
    out = sanitize_voiceover_text(raw)
    assert "VOICEOVER" not in out
    assert not out.startswith("`")
    assert out.startswith("День на старой пасеке")


def test_ensure_voiceover_format_instruction_idempotent() -> None:
    base = "Сделай закадровый текст."
    once = ensure_voiceover_format_instruction(base)
    twice = ensure_voiceover_format_instruction(once)
    assert once.count("<<<VOICEOVER>>>") == 1
    assert twice == once


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


def test_looks_like_xlsx_tsv_writeback() -> None:
    from app.services.voiceover_sanitize import looks_like_xlsx_tsv_writeback

    tsv = (
        "# Лист: Общий план\n"
        "@row=1\tТема\tТекст\n"
        "# Лист: План\n"
        "@row=49\tзакадровый текст\tраз\tдва\n"
    )
    assert looks_like_xlsx_tsv_writeback(tsv)
    assert not looks_like_xlsx_tsv_writeback(
        "Третьего января 1889 года Ницше пережил психический коллапс."
    )


def test_extract_scenario_leaves_voiceover() -> None:
    raw = (
        "<<<SCENARIO>>>\n"
        "Испытание: соляная тишь.\n"
        "<<<END_SCENARIO>>>\n"
        "<<<VOICEOVER>>>\n"
        "Испытание. Соляная тишь. За первой дверью можно дышать.\n"
        "<<<END>>>\n"
    )
    assert extract_scenario_block(raw) == "Испытание: соляная тишь."
    assert extract_voiceover_block(raw) == (
        "Испытание. Соляная тишь. За первой дверью можно дышать."
    )


def test_extract_scenario_unclosed_stops_before_voiceover() -> None:
    raw = (
        "<<<SCENARIO>>>\n"
        "Дверь первая. Соль.\n"
        "<<<VOICEOVER>>>\n"
        "Испытание. Соль держит тело, пока не сделаешь шаг в сторону.\n"
        "<<<END>>>\n"
    )
    assert extract_scenario_block(raw) == "Дверь первая. Соль."
    assert "VOICEOVER" not in (extract_scenario_block(raw) or "")


def test_extract_scenario_or_preamble() -> None:
    from app.services.voiceover_sanitize import extract_scenario_or_preamble

    raw = (
        "<<<SCENARIO>>>\n"
        "Кадр 0–7. Две двери. Камера в коридоре.\n"
        "<<<END_SCENARIO>>>\n"
        "<<<VOICEOVER>>>\n"
        + ("А" * 220)
        + "\n<<<END>>>\n"
    )
    assert "Две двери" in extract_scenario_or_preamble(raw)


def test_extract_scenario_closed_with_generic_end() -> None:
    raw = (
        "<<<SCENARIO>>>\n"
        "Две ветки. Первая дверь жуткая. Вторая тихая.\n"
        "<<<END>>>\n"
        "<<<VOICEOVER>>>\n"
        + ("Б" * 80)
        + "\n<<<END>>>\n"
    )
    assert "Две ветки" in (extract_scenario_block(raw) or "")
