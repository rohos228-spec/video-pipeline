"""Подстановка лимитов запуска ноды из реальных данных проекта (после ответа планировщика).

Лимиты ``node.run_step`` (min/max_llm_requests, max_input/output_tokens) модель выдумать не может:
сервер берёт их из того же расчёта, что и ``node.get_run_cost_basis`` (верхняя оценка по данным
проекта). Если расчёт невозможен, параметры остаются пустыми и preview покажет причину.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

DECL_KEYS = ("min_llm_requests", "max_llm_requests", "max_input_tokens", "max_output_tokens")


async def fill_node_run_limits(app: Any, operator: str, steps: list[dict]) -> list[str]:
    """Дополняет шаги node.run_step без лимитов; возвращает заметки для explanation_ru."""
    wanted = [s for s in steps if s.get("action_id") == "node.run_step"
              and not any(k in (s.get("params") or {}) for k in DECL_KEYS)]
    if not wanted:
        return []
    from app.services.action_layer import builtin_wave3 as w3
    from app.services.action_layer import node_run_cost as nrc
    from app.services.action_layer.context import ActionContext

    ctx = ActionContext(app=app, operator=operator, read_only=True)
    notes: list[str] = []
    for st in wanted:
        params = st["params"]
        key = str(params.get("node_key") or "")
        if not key or nrc.is_report_node(key):
            continue
        try:
            basis = await w3._load_basis(ctx, params)
        except Exception as exc:  # noqa: BLE001 - причину покажет preview
            logger.debug("planner autofill: no run basis for {}: {}", key, type(exc).__name__)
            continue
        if isinstance(basis, str):
            continue
        params.update(basis.declare())
        notes.append(f"лимиты запуска {key} из расчёта по данным проекта "
                     f"(запросов {basis.min_requests}..{basis.max_requests})")
    return notes
