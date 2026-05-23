"""Тесты на scripts/audit_buttons.py — проверяем что скан работает
и инварианты соблюдаются.
"""

from __future__ import annotations

import pytest

from scripts.audit_buttons import (
    TG_CALLBACK_LIMIT,
    AuditReport,
    audit,
)


@pytest.fixture(scope="module")
def report() -> AuditReport:
    """Один прогон скана на весь модуль тестов."""
    return audit()


def test_audit_finds_buttons(report: AuditReport) -> None:
    """В app/telegram/ должны быть кнопки — иначе скан сломан."""
    assert len(report.buttons) > 50, (
        f"подозрительно мало кнопок: {len(report.buttons)}"
    )


def test_audit_finds_handlers(report: AuditReport) -> None:
    """В app/telegram/ должны быть callback handlers."""
    assert len(report.handlers) > 10, (
        f"подозрительно мало handlers: {len(report.handlers)}"
    )


def test_no_long_callbacks(report: AuditReport) -> None:
    """⚠️ Критический инвариант: ни одной callback_data > 64 байт.

    Это лимит Telegram, превышение → бот падает с BadRequest.
    """
    if report.long_callbacks:
        details = "\n".join(
            f"  {b.file}:{b.line} text={b.text!r} cb={b.callback_template!r}"
            for b in report.long_callbacks[:10]
        )
        pytest.fail(
            f"Найдено {len(report.long_callbacks)} callback'ов > "
            f"{TG_CALLBACK_LIMIT} байт:\n{details}"
        )


def test_no_critical_issues(report: AuditReport) -> None:
    """has_critical() возвращает False (т.е. только long_callbacks)."""
    assert not report.has_critical()


def test_ai_agent_callbacks_within_limit() -> None:
    """Все ai:* callback_data из Phase I.3 укладываются в 64 байта.

    Проверка специально, потому что они генерятся динамически с tool_call_id.
    """
    from app.telegram.handlers.ai_agent import _hitl_kb, _summary_kb

    # tool_call_id может быть большим (int) — проверим до 1 млн
    for tc_id in [1, 999, 999999]:
        kb = _hitl_kb(tc_id)
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    assert (
                        len(btn.callback_data.encode("utf-8"))
                        <= TG_CALLBACK_LIMIT
                    ), f"{btn.callback_data} > 64 байт"

    for sid in [1, 9999]:
        kb = _summary_kb(sid)
        for row in kb.inline_keyboard:
            for btn in row:
                if btn.callback_data:
                    assert (
                        len(btn.callback_data.encode("utf-8"))
                        <= TG_CALLBACK_LIMIT
                    )
