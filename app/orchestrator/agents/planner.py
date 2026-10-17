"""Planner R3: план через router, правила из rag.retrieve в system.

Чужой RAG и роутер не импортируются. В тестах — ScriptedRouter и мок retrieve.
"""

from __future__ import annotations

import inspect
import json
from typing import Any

from app.orchestrator.agents.llm_call import router_call
from app.orchestrator.contracts import AgentRole, BudgetScope, Chunk, PlanStep


class NullRetriever:
    async def retrieve(
        self,
        query: str,
        corpora: list[str] | None = None,
        top_k: int = 8,
    ) -> list[Chunk]:
        del query, corpora, top_k
        return []


class RouterPlanner:
    """strong-вызов. Ответ — JSON ``{"steps": [PlanStep, ...]}`` или список."""

    def __init__(self, router: Any, rag: Any | None = None) -> None:
        self.router = router
        self.rag = rag or NullRetriever()
        self.last_response: Any = None

    async def make_plan(self, goal: str, context: str = "", **call: Any) -> list[PlanStep]:
        rules = await _retrieve(self.rag, goal or context or "plan", ["rules"], 8)
        system = _system_prompt(rules)
        user = goal if not context else f"{goal}\n\n{context}"
        response = await router_call(
            self.router,
            "strong",
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            stream=False,
            run_id=call.get("run_id"),
            task_id=call.get("task_id"),
            agent_role=call.get("agent_role") or AgentRole.planner.value,
            budget_scope=call.get("budget_scope") or BudgetScope.internal.value,
            reasoning_effort=call.get("reasoning_effort"),
        )
        self.last_response = response
        return parse_plan(getattr(response, "text", "") or "")


def parse_plan(text: str) -> list[PlanStep]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        lines = [line for line in raw.splitlines() if not line.strip().startswith("```")]
        raw = "\n".join(lines).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(data, dict):
        data = data.get("steps", data.get("plan", []))
    if not isinstance(data, list):
        return []
    return [PlanStep.model_validate(item) for item in data]


def _system_prompt(rules: list[Chunk]) -> str:
    lines = [
        "Составь план как JSON {\"steps\": [...]} .",
        "Каждый шаг: action, params, expected, paid, mutating, risk, parallel_group, estimated_cost.",
        "Перед run_node обязателен шаг verify_code_state (read_pipeline_code + get_node).",
        "Не планируй удаление кадров и сцен.",
        "Правила пользователя:",
    ]
    if not rules:
        lines.append("(нет)")
    for chunk in rules:
        lines.append(chunk.text)
    return "\n".join(lines)


async def _retrieve(rag: Any, query: str, corpora: list[str], top_k: int) -> list[Chunk]:
    result = rag.retrieve(query, corpora, top_k)
    if inspect.isawaitable(result):
        result = await result
    chunks: list[Chunk] = []
    for item in result or []:
        if isinstance(item, Chunk):
            chunks.append(item)
        else:
            chunks.append(Chunk.model_validate(item))
    return chunks
