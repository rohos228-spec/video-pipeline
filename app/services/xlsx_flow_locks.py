"""Per-(project, step) locks для xlsx-flow (plan/script/split/img_pr).

При ⏹ также отменяем asyncio-task xlsx-flow (как test-prompt 🛑 Стоп).
"""
from __future__ import annotations

import asyncio

XLSX_FLOW_STEP_CODES: tuple[str, ...] = ("plan", "script", "split", "img_pr")

# (project_id, step_code)
_xlsx_flow_active: set[tuple[int, str]] = set()
_xlsx_flow_tasks: dict[tuple[int, str], asyncio.Task] = {}


def xlsx_flow_active_set() -> set[tuple[int, str]]:
    """Множество активных локов (для bot._run_xlsx_with_lock)."""
    return _xlsx_flow_active


def is_xlsx_flow_active(project_id: int, step: str) -> bool:
    return (project_id, step) in _xlsx_flow_active


def register_xlsx_flow_task(project_id: int, step: str, task: asyncio.Task) -> None:
    _xlsx_flow_tasks[(project_id, step)] = task


def unregister_xlsx_flow_task(project_id: int, step: str) -> None:
    _xlsx_flow_tasks.pop((project_id, step), None)


def cancel_xlsx_flow_tasks(project_id: int) -> list[str]:
    """Отменяет все xlsx-flow tasks проекта. Возвращает коды шагов."""
    stopped: list[str] = []
    for code in XLSX_FLOW_STEP_CODES:
        key = (project_id, code)
        task = _xlsx_flow_tasks.get(key)
        if task is not None and not task.done():
            task.cancel()
            stopped.append(code)
        elif key in _xlsx_flow_active:
            _xlsx_flow_active.discard(key)
            stopped.append(code)
    return stopped


def clear_xlsx_flow_locks(project_id: int) -> list[str]:
    """Снимает xlsx-flow локи проекта. Возвращает коды снятых шагов."""
    return cancel_xlsx_flow_tasks(project_id)
