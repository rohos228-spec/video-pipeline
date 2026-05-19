"""Тесты UI-хелперов глобальной библиотеки промтов (`gprm:*`).

Проверяем что:
1. `overview_text()` корректно собирает счётчики по шагам.
2. `overview_kb()` содержит по кнопке на каждый шаг + «В главное меню».
3. `picker_kb()` содержит по кнопке на каждый существующий вариант
   + кнопки «+ Новый», «🗑 Удалить», «⬅ К списку шагов».
4. `delete_kb()` НЕ содержит `default` (его удалять нельзя).
5. Все callback_data укладываются в 64 байта Telegram-лимита.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services import prompt_library as plib
from app.telegram.global_prompts_menu import (
    delete_kb,
    overview_kb,
    overview_text,
    picker_kb,
    picker_text,
)


@pytest.fixture
def prompts_root(tmp_path: Path, monkeypatch) -> Path:
    """Подменяем `PROMPTS_ROOT` на временную папку и создаём минимальную
    структуру: 01_plan/default.md + 01_plan/horror.md."""
    root = tmp_path / "prompts"
    root.mkdir()
    monkeypatch.setattr(plib, "PROMPTS_ROOT", root)
    plan = root / "01_plan"
    plan.mkdir()
    (plan / "default.md").write_text("default plan", encoding="utf-8")
    (plan / "horror.md").write_text("horror plan", encoding="utf-8")
    # ещё одна папка с одним только default — script
    script = root / "02_script"
    script.mkdir()
    (script / "default.md").write_text("default script", encoding="utf-8")
    return root


def test_overview_text_contains_all_step_names(prompts_root: Path):
    """В обзоре должны быть упомянуты все шаги из `STEP_HUMAN_NAMES`."""
    text = overview_text()
    assert "Глобальная библиотека" in text
    for human in plib.STEP_HUMAN_NAMES.values():
        assert human in text, f"step {human!r} missing in overview"


def test_overview_text_shows_counts(prompts_root: Path):
    """В обзоре отражены реальные кол-ва файлов в папках."""
    text = overview_text()
    # plan: 2 варианта (default + horror)
    assert "1. План" in text
    assert "2 вар" in text
    # script: 1 вариант (default)
    assert "2. Закадровый текст" in text
    assert "1 вар" in text


def test_overview_kb_has_button_per_step(prompts_root: Path):
    """`overview_kb()` — по 1 кнопке на каждый шаг + 1 «закрыть»."""
    kb = overview_kb()
    rows = kb.inline_keyboard
    # Кнопок-шагов столько же, сколько в STEP_HUMAN_NAMES, плюс одна
    # последняя строка «В главное меню».
    assert len(rows) == len(plib.STEP_HUMAN_NAMES) + 1
    # Все step-кнопки имеют callback_data вида gprm:<code>:menu
    step_codes = set(plib.STEP_HUMAN_NAMES.keys())
    seen: set[str] = set()
    for row in rows[:-1]:
        assert len(row) == 1
        btn = row[0]
        assert btn.callback_data is not None
        assert btn.callback_data.startswith("gprm:")
        assert btn.callback_data.endswith(":menu")
        parts = btn.callback_data.split(":")
        assert len(parts) == 3
        seen.add(parts[1])
    assert seen == step_codes
    # последняя строка — выход
    last = rows[-1][0]
    assert last.callback_data == "menu:root"


def test_picker_kb_lists_variants_plus_actions(prompts_root: Path):
    """Пикер шага plan содержит 2 варианта + строку add/delask + назад."""
    kb = picker_kb("plan")
    rows = kb.inline_keyboard
    # 2 варианта (default + horror) — каждый отдельной строкой,
    # затем строка [add, delask], затем строка [back].
    assert len(rows) == 2 + 1 + 1
    # default — первый, как и в `list_prompts()`
    assert rows[0][0].text == "default"
    assert rows[0][0].callback_data == "gprm:plan:edit:default"
    assert rows[1][0].text == "horror"
    assert rows[1][0].callback_data == "gprm:plan:edit:horror"
    # add/delask
    action_row = rows[2]
    assert len(action_row) == 2
    assert action_row[0].callback_data == "gprm:plan:add"
    assert action_row[1].callback_data == "gprm:plan:delask"
    # назад — к обзору
    assert rows[-1][0].callback_data == "gprm:overview"


def test_picker_text_mentions_step_and_count(prompts_root: Path):
    text = picker_text("plan")
    assert "1. План" in text
    assert "2" in text  # кол-во вариантов
    assert "default" in text  # упоминание защиты default'а


def test_delete_kb_hides_default(prompts_root: Path):
    """В клавиатуре удаления `default` отсутствует."""
    kb = delete_kb("plan")
    rows = kb.inline_keyboard
    # 1 удаляемый вариант (horror) + 1 строка «назад»
    assert len(rows) == 2
    assert rows[0][0].callback_data == "gprm:plan:del:horror"
    assert "horror" in rows[0][0].text
    # последняя — назад
    assert rows[-1][0].callback_data == "gprm:plan:menu"


def test_delete_kb_when_only_default(prompts_root: Path):
    """Шаг с одним только default: удалять нечего, только «назад»."""
    kb = delete_kb("script")
    rows = kb.inline_keyboard
    assert len(rows) == 1
    assert rows[0][0].callback_data == "gprm:script:menu"


def test_all_callback_data_within_telegram_limit(prompts_root: Path):
    """Telegram ограничивает callback_data 64 байтами. Проверяем что
    ни одна из наших кнопок не превышает лимит."""
    keyboards = [
        overview_kb(),
        picker_kb("plan"),
        picker_kb("script"),
        delete_kb("plan"),
        delete_kb("script"),
    ]
    for kb in keyboards:
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data is None:
                    continue
                size = len(btn.callback_data.encode("utf-8"))
                assert size <= 64, (
                    f"callback_data too long ({size} bytes): "
                    f"{btn.callback_data!r}"
                )


def test_picker_kb_handles_missing_step_gracefully(prompts_root: Path):
    """Если папки шага нет — list_prompts создаст её (mkdir parents=True),
    но вариантов будет 0. Клавиатура должна остаться без падений: только
    add/delask + back."""
    kb = picker_kb("anim_pr")
    rows = kb.inline_keyboard
    # 0 вариантов + 1 строка [add, delask] + 1 строка [back] = 2 строки
    assert len(rows) == 2
    assert rows[0][0].callback_data == "gprm:anim_pr:add"
    assert rows[-1][0].callback_data == "gprm:overview"
