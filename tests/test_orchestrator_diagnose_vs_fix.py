"""diagnose-only vs кнопка «Фикс багов»."""

from __future__ import annotations

from app.web.routers.db_browser import (
    _is_diagnose_only_question,
    _is_fix_bugs_mode,
)


def test_plain_find_cause_is_diagnose_only() -> None:
    assert _is_diagnose_only_question("найди причину почему персонажи кривые")


def test_fix_bugs_prefix_disables_diagnose_only() -> None:
    msg = (
        "Режим «Фикс багов» / поручение оператора ниже. "
        "ПОРУЧЕНИЕ:\nнайди причину почему персонажи кривые"
    )
    assert _is_fix_bugs_mode(msg) is True
    assert _is_diagnose_only_question(msg) is False


def test_fix_bugs_flag_disables_diagnose_only() -> None:
    assert (
        _is_diagnose_only_question(
            "найди причину", fix_bugs=True
        )
        is False
    )
