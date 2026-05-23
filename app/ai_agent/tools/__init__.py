"""Реестр всех tools для AI-агента.

Каждый tool — модуль с:
- `TOOL_SPEC: dict` — JSON-schema в OpenAI tools формате.
- `async def run(args: dict, ctx: ToolContext) -> dict | str` — выполнение.
- `IS_HITL: bool = False` — нужен ли HITL-апрув owner'а.

Реестр `ALL_TOOLS` собирается на импорте.
"""

from __future__ import annotations

from typing import Any

# Импортируем модули — они зарегистрируют свои TOOL_* константы.
from app.ai_agent.tools import answer, db, fs, gh, git, quality  # noqa: F401,E402
from app.ai_agent.tools._spec import ToolContext, ToolSpec


def _build_registry() -> dict[str, ToolSpec]:
    """Собрать реестр всех известных tools."""
    registry: dict[str, ToolSpec] = {}
    modules = [answer, db, fs, gh, git, quality]
    for mod in modules:
        for attr_name in dir(mod):
            if not attr_name.startswith("TOOL_"):
                continue
            spec_or_list = getattr(mod, attr_name)
            specs = (
                spec_or_list
                if isinstance(spec_or_list, list)
                else [spec_or_list]
            )
            for ts in specs:
                if not isinstance(ts, ToolSpec):
                    continue
                if ts.name in registry:
                    raise RuntimeError(f"duplicate tool name: {ts.name}")
                registry[ts.name] = ts
    return registry


ALL_TOOLS: dict[str, ToolSpec] = _build_registry()


def get_openai_tools_schema(
    *, include_edit: bool = True
) -> list[dict[str, Any]]:
    """Вернуть массив `tools` для передачи в `chat.completions`.

    include_edit=False — в QA-режиме отдаём только read-only tools.
    """
    result = []
    for ts in ALL_TOOLS.values():
        if not include_edit and ts.is_hitl:
            continue
        result.append(ts.spec)
    return result


def get_tool(name: str) -> ToolSpec | None:
    return ALL_TOOLS.get(name)


__all__ = [
    "ALL_TOOLS",
    "ToolContext",
    "ToolSpec",
    "get_openai_tools_schema",
    "get_tool",
]
