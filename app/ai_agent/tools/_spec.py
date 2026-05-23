"""Базовые типы (ToolSpec, ToolContext) — отдельный модуль, чтобы избежать
циклического импорта между tools/__init__.py и tools/*.py.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ToolContext:
    """Контекст, который получает каждый tool при вызове."""

    repo_root: Path
    tool_timeout_sec: int = 120
    # Опционально: callable для проверки cancel (например, /ai cancel)
    cancel_check: Callable[[], bool] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolSpec:
    """Метаданные одного tool'а."""

    name: str
    spec: dict[str, Any]  # OpenAI tools JSON
    run: Callable[[dict, ToolContext], Awaitable[Any]]
    is_hitl: bool = False
    is_terminal: bool = False  # final_answer — терминатор loop'а
    description_short: str = ""
