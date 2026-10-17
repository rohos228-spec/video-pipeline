"""Critic R3: approve | revise | reject.

Сначала детерминированные проверки (удаления, правило 9, прогноз spend),
затем strong-вердикт роутера. Правила из rag.retrieve попадают в system.
≤2 revise считает супервизор и эскалирует reason=critic_escalation.
Без роутера вердикт не approve: отказ, если детерминированные проверки
молчат. Автоодобрение при ``router=None`` — ошибка конфигурации.
"""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from app.orchestrator.agents.llm_call import router_call
from app.orchestrator.agents.planner import NullRetriever
from app.orchestrator.agents.retrieve import retrieve, rules_block
from app.orchestrator.contracts import (
    AgentRole,
    BudgetScope,
    Chunk,
    PlanStep,
    ceiling_blocks,
    forecast_cost,
    forecast_unknown,
    plan_deletions,
    plan_missing_verify,
    server_forecast_limit,
)


class PlanCritic:
    def __init__(self, router: Any, rag: Any | None = None) -> None:
        self.router = router
        self.rag = rag if rag is not None else NullRetriever()
        self.last_response: Any = None

    async def review(
        self,
        plan: list[PlanStep],
        context: str = "",
        *,
        run_id: str | None = None,
        task_id: str | int | None = None,
        agent_role: str | None = None,
        budget_scope: str | None = None,
        reasoning_effort: str | None = None,
    ) -> str:
        self.last_response = None
        block = rules_block(self.rag)
        rules = await retrieve(self.rag, context or "critic", ["rules"], 8)
        if plan_deletions(plan):
            return "reject"
        if plan_missing_verify(plan):
            return "revise"
        limit = server_forecast_limit()
        if ceiling_blocks(limit) or forecast_unknown(plan):
            return "reject"
        if forecast_cost(plan) > limit:
            return "revise"
        if self.router is None:
            return "reject"
        response = await router_call(
            self.router,
            "strong",
            [
                {"role": "system", "content": _critic_system(rules, block)},
                {
                    "role": "user",
                    "content": _json_plan(plan) + ("\n" + context if context else ""),
                },
            ],
            stream=False,
            run_id=run_id,
            task_id=task_id,
            agent_role=agent_role or AgentRole.critic.value,
            budget_scope=budget_scope or BudgetScope.internal.value,
            reasoning_effort=reasoning_effort,
        )
        self.last_response = response
        return _parse_verdict(getattr(response, "text", "") or "")


def _critic_system(rules: list[Chunk], block: str = "") -> str:
    lines = [
        "Вердикт одним словом: approve, revise или reject.",
        "reject — удаление кадров или сцен, либо нарушение правила пользователя.",
        "revise — нет verify_code_state перед run_node или прогноз spend выше лимита.",
        "Правила:",
    ]
    text = (block or "").strip()
    if text:
        lines.append(text)
    if not rules and not text:
        lines.append("(нет)")
    for chunk in rules:
        lines.append(chunk.text)
    return "\n".join(lines)


def _parse_verdict(text: str) -> str:
    raw = text or ""
    word = raw.strip().lower().split()[0] if raw.strip() else ""
    word = word.strip(".,:;")
    if word in {"approve", "revise", "reject"}:
        return word
    logger.warning("critic: нераспознанный вердикт, сырой текст: {}", raw)
    return "revise"


def _json_plan(plan: list[PlanStep]) -> str:
    return json.dumps({"steps": [step.model_dump() for step in plan]}, ensure_ascii=False)


RouterCritic = PlanCritic
