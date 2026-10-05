"""Доступ к проекту только через существующие эндпоинты (GET/PATCH /projects/{id}).

Так сохраняются валидация, защита от затирания meta и события; свои записи в
проект контур не делает. В тестах подставляется любой объект с теми же методами.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Protocol


class ProjectNotFound(LookupError):
    pass


class ProjectGateway(Protocol):
    async def get(self, project_id: int) -> dict: ...
    async def patch(self, project_id: int, body: dict) -> None: ...


class CtxGateway:
    """Шлюз поверх ActionContext (in-process ASGI, без сети)."""

    def __init__(self, ctx: Any):
        self.ctx = ctx

    async def get(self, project_id: int) -> dict:
        from app.services.action_layer.context import ActionCallError
        try:
            data = await self.ctx.call("GET", f"/projects/{project_id}")
        except ActionCallError as exc:
            if exc.status == 404:
                raise ProjectNotFound(f"project {project_id} not found") from exc
            raise
        return project_state(data)

    async def patch(self, project_id: int, body: dict) -> None:
        await self.ctx.call("PATCH", f"/projects/{project_id}", json=body)


def project_state(data: dict) -> dict:
    d = data if isinstance(data, dict) else {}
    return {"id": d.get("id"), "status": d.get("status"),
            "topic": d.get("topic") or "",
            "meta": d.get("meta") if isinstance(d.get("meta"), dict) else {},
            "prompt_overrides": d.get("prompt_overrides")
            if isinstance(d.get("prompt_overrides"), dict) else {},
            "gpt_text_overrides": d.get("gpt_text_overrides")
            if isinstance(d.get("gpt_text_overrides"), dict) else {}}


def project_ns(state: dict) -> SimpleNamespace:
    """Объект, достаточный для prompt_library / gpt_operator (читают только эти поля)."""
    return SimpleNamespace(id=state.get("id"), meta=state.get("meta") or {},
                           prompt_overrides=state.get("prompt_overrides") or {},
                           gpt_text_overrides=state.get("gpt_text_overrides") or {},
                           topic=state.get("topic") or "", status=state.get("status"))
