"""Tool registry LLM-агента: схемы (Responses API) + исполнители.

Каждый tool — тонкая обёртка над существующими сервисами/БД (не HTTP, чтобы
не гонять loopback). Риск-уровни: read < write < danger. Потолок риска
задаётся автономией сессии (advisor=read, operator=write, autopilot=danger).
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

ToolExecutor = Callable[[dict[str, Any]], Awaitable[str]]

RISK_ORDER = {"read": 0, "write": 1, "danger": 2}
AUTONOMY_MAX_RISK = {"advisor": "read", "operator": "write", "autopilot": "danger"}


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    risk: str
    executor: ToolExecutor
    tags: list[str] = field(default_factory=list)


_REGISTRY: dict[str, ToolSpec] = {}


def tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    risk: str = "read",
    tags: list[str] | None = None,
) -> Callable[[ToolExecutor], ToolExecutor]:
    assert risk in RISK_ORDER, f"bad risk {risk}"

    def deco(fn: ToolExecutor) -> ToolExecutor:
        _REGISTRY[name] = ToolSpec(
            name=name,
            description=description,
            parameters=parameters,
            risk=risk,
            executor=fn,
            tags=tags or [],
        )
        return fn

    return deco


def get_tool(name: str) -> ToolSpec | None:
    return _REGISTRY.get(name)


def list_tools(max_risk: str = "danger") -> list[ToolSpec]:
    cap = RISK_ORDER[max_risk]
    return [t for t in _REGISTRY.values() if RISK_ORDER[t.risk] <= cap]


def tool_schemas(max_risk: str = "danger") -> list[dict[str, Any]]:
    """Схемы в формате Responses/chat API (одинаковый function-формат)."""
    return [
        {
            "type": "function",
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
        for t in list_tools(max_risk)
    ]


def risk_allowed(risk: str, autonomy: str) -> bool:
    max_risk = AUTONOMY_MAX_RISK.get(autonomy, "write")
    return RISK_ORDER[risk] <= RISK_ORDER[max_risk]


async def execute_tool(name: str, arguments_json: str) -> str:
    """Выполнить tool по имени; результат — JSON-строка для function_call_output."""
    spec = _REGISTRY.get(name)
    if spec is None:
        return json.dumps({"error": f"unknown tool {name!r}"}, ensure_ascii=False)
    try:
        args = json.loads(arguments_json) if arguments_json.strip() else {}
    except json.JSONDecodeError as e:
        return json.dumps({"error": f"bad arguments JSON: {e}"}, ensure_ascii=False)
    if not isinstance(args, dict):
        return json.dumps({"error": "arguments must be an object"}, ensure_ascii=False)
    try:
        return await spec.executor(args)
    except Exception as e:  # noqa: BLE001
        return json.dumps(
            {"error": f"{type(e).__name__}: {e}"[:500]}, ensure_ascii=False
        )
