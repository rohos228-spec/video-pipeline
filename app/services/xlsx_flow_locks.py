"""Per-(project, step) locks для xlsx-flow (plan/script/split/img_pr).

Используется Telegram-ботом и веб-студией при ⏹ Остановить — чтобы снять
зависший лок, если юзер прервал длинный GPT+Excel цикл.
"""
from __future__ import annotations

XLSX_FLOW_STEP_CODES: tuple[str, ...] = ("plan", "script", "split", "img_pr")

# (project_id, step_code)
_xlsx_flow_active: set[tuple[int, str]] = set()


def xlsx_flow_active_set() -> set[tuple[int, str]]:
    """Множество активных локов (для bot._run_xlsx_with_lock)."""
    return _xlsx_flow_active


def is_xlsx_flow_active(project_id: int, step: str) -> bool:
    return (project_id, step) in _xlsx_flow_active


def clear_xlsx_flow_locks(project_id: int) -> list[str]:
    """Снимает все xlsx-flow локи проекта. Возвращает коды снятых шагов."""
    stopped: list[str] = []
    for code in XLSX_FLOW_STEP_CODES:
        key = (project_id, code)
        if key in _xlsx_flow_active:
            _xlsx_flow_active.discard(key)
            stopped.append(code)
    return stopped
