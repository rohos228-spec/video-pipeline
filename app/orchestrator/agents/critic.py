"""Critic R3: approve | revise | reject.

Сначала детерминированные проверки (удаления, правило 9, прогноз spend),
затем strong-вердикт роутера. Правила из rag.retrieve попадают в system.
≤2 revise считает супервизор и эскалирует reason=critic_escalation.
"""

from __future__ import annotations

import json
from typing import Any

from app.orchestrator.agents.planner import _retrieve
from app.orchestrator.contracts import (
    PIPELINE_FORECAST_LIMIT_USD,
    Chunk,
    PlanStep,
    forecast_cost,
    plan_deletions,
    plan_missing_verify,
)


class PlanCritic:
    def __init__(self, router: Any, rag: Any | None = None) -> None:
        self.router = router
        self.rag = rag

    async def review(self, plan: list[PlanStep], context: str = "") -> str:
        rules: list[Chunk] = []
        if self.rag is not None:
            rules = await _retrieve(self.rag, context or "critic", ["rules"], 8)
        if plan_deletions(plan):
            return "reject"
        if plan_missing_verify(plan):
            return "revise"
        if forecast_cost(plan) > PIPELINE_FORECAST_LIMIT_USD:
            return "revise"
        if self.router is None:
            return "approve"
        response = await self.router.call(
            "strong",
            [
                {"role": "system", "content": _critic_system(rules)},
                {
                    "role": "user",
                    "content": _json_plan(plan) + ("\n" + context if context else ""),
                },
            ],
            stream=False,
        )
        return _parse_verdict(getattr(response, "text", "") or "")


def _critic_system(rules: list[Chunk]) -> str:
    lines = [
        "Вердикт одним словом: approve, revise или reject.",
        "reject — удаление кадров или сцен, либо нарушение правила пользователя.",
        "revise — нет verify_code_state перед run_node или прогноз spend выше лимита.",
        "Правила:",
    ]
    if not rules:
        lines.append("(нет)")
    for chunk in rules:
        lines.append(chunk.text)
    return "\n".join(lines)


def _parse_verdict(text: str) -> str:
    word = (text or "").strip().lower().split()[0] if (text or "").strip() else ""
    word = word.strip(".,:;")
    if word in {"approve", "revise", "reject"}:
        return word
    return "revise"


def _json_plan(plan: list[PlanStep]) -> str:
    return json.dumps({"steps": [step.model_dump() for step in plan]}, ensure_ascii=False)
