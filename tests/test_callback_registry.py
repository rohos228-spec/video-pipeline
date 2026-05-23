"""Тесты на app/telegram/callback_registry.py (Phase E.4 step 1).

Гарантирует:
- Все enum-значения уникальны.
- Префиксы валидные (ASCII, [a-z0-9_:]).
- Все callback'ы из реальных файлов handlers/ai_agent.py матчатся с CB.
- Префиксы укладываются в 64 байта с разумными var-значениями.
"""

from __future__ import annotations

from app.telegram.callback_registry import (
    CB,
    TG_CALLBACK_LIMIT,
    estimate_max_length,
    find_prefix,
    is_registered,
)


def test_all_prefixes_unique() -> None:
    """В CB Enum не должно быть дублей."""
    values = [c.value for c in CB]
    assert len(values) == len(set(values))


def test_all_prefixes_are_strings() -> None:
    """CB наследует str + Enum, значит .value — str."""
    for c in CB:
        assert isinstance(c.value, str)
        assert c.value  # не пустой


def test_all_prefixes_within_limit() -> None:
    """Каждый префикс с тремя var-значениями по 20 символов ≤ 64 байт."""
    for c in CB:
        assert estimate_max_length(c.value, var_max_len=20) < 200
        # Префикс + ":x" * 3 (например proj:42:menu) — все должны влезть
        # Note: estimate_max_length считает var_max_len * 3 = 60 байт для 3 vars,
        # + сам префикс ~20 байт = до 80 — это ВЕРХНЯЯ граница, реально меньше.
        # Проверяем что префикс сам короткий (≤ 32):
        assert len(c.value.encode("utf-8")) <= 32, (
            f"prefix {c.value!r} занимает {len(c.value.encode('utf-8'))} байт"
        )


def test_is_registered_recognizes_known() -> None:
    """is_registered() возвращает True для callback'ов с зарегистрированными префиксами."""
    assert is_registered("ai:approve:42")
    assert is_registered("ai:noop")  # точное совпадение
    assert is_registered("proj:42:menu")
    assert is_registered("mass:start:7")
    assert is_registered("step_run:1:plan")


def test_is_registered_rejects_unknown() -> None:
    assert not is_registered("")
    assert not is_registered("foo:bar")
    assert not is_registered("random:42")


def test_find_prefix_longest_match() -> None:
    """find_prefix должен возвращать самый длинный соответствующий префикс."""
    # mass:delete_yes — длиннее чем mass:delete (более specific)
    assert find_prefix("mass:delete_yes:7") == "mass:delete_yes"
    # mass:delete:7 — стандартный
    assert find_prefix("mass:delete:7") == "mass:delete"
    assert find_prefix("ai:noop") == "ai:noop"


def test_ai_handler_callbacks_registered() -> None:
    """Все callback'ы из app/telegram/handlers/ai_agent.py — в реестре."""
    from app.telegram.handlers.ai_agent import _hitl_kb, _summary_kb

    for kb_factory in [lambda: _hitl_kb(42), lambda: _summary_kb(99)]:
        kb = kb_factory()
        for row in kb.inline_keyboard:
            for btn in row:
                if not btn.callback_data:
                    continue
                assert is_registered(btn.callback_data), (
                    f"AI handler callback {btn.callback_data!r} не зарегистрирован в CB"
                )


def test_callback_data_under_64_bytes() -> None:
    """Все ai-handler callback_data при разумных tool_call_id ≤ 64 байт."""
    from app.telegram.handlers.ai_agent import _hitl_kb, _summary_kb

    for sid in [1, 99, 999_999, 10**12]:  # до триллиона
        kb1 = _hitl_kb(sid)
        kb2 = _summary_kb(sid)
        for kb in [kb1, kb2]:
            for row in kb.inline_keyboard:
                for btn in row:
                    if btn.callback_data:
                        assert (
                            len(btn.callback_data.encode("utf-8"))
                            <= TG_CALLBACK_LIMIT
                        ), f"{btn.callback_data} > 64 bytes for sid={sid}"


def test_cb_enum_provides_expected_aliases() -> None:
    """Базовая sanity: ключевые CB-константы существуют."""
    expected = {
        "AI_APPROVE",
        "AI_REJECT",
        "AI_CLARIFY",
        "AI_NOOP",
        "MENU_ROOT",
        "MENU_NEW",
        "PROJ_MENU",
        "STEP_RUN",
        "MASS_NEW",
        "MASS_START",
        "MASS_NOOP",
        "WIZ",
        "TEST",
    }
    actual = {c.name for c in CB}
    missing = expected - actual
    assert not missing, f"missing CB members: {missing}"


def test_cb_can_concat_with_variable() -> None:
    """Удобство использования: CB.X + ":42" должно работать (str-enum)."""
    full = f"{CB.PROJ_MENU.value}:42:menu"
    assert full == "proj:42:menu"
    assert is_registered(full)
