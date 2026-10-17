"""Адаптер WS2: ядро вызывает действия Никиты и получает ActionResult ws2-r2.3."""

from __future__ import annotations

from app.models import Project
from app.orchestrator.action_contracts.contracts import CONTRACT_VERSION, ActionResult
from app.services.action_layer.spec import ensure_builtin_actions
from tests.action_layer_support import env

__all__ = ["env"]


async def test_execute_returns_r22_action_result(env) -> None:
    """Чтение и локальная запись на фикстуре, без сети: конверт r2.3."""

    from app.orchestrator.actions.executor import execute

    ensure_builtin_actions()
    assert CONTRACT_VERSION == "ws2-r2.3"
    ctx = {"app": env.app, "operator": "adapter-test"}

    read = await execute("history.list_snapshots", {"limit": 5}, ctx)
    assert isinstance(read, ActionResult)
    assert read.status == "ok"
    assert read.error is None
    assert isinstance(read.warnings, list)
    assert isinstance(read.result, dict)
    assert "snapshots" in read.result
    assert read.model_dump()["status"] == "ok"

    pid = await env.add_project()
    write = await execute("project.pause", {"project_id": pid}, ctx)
    assert isinstance(write, ActionResult)
    assert write.status == "ok"
    assert write.error is None
    assert isinstance(write.result, dict)
    assert write.result.get("status") == "paused"

    async with env.factory() as session:
        project = await session.get(Project, pid)
    assert project is not None
    assert project.status == "paused"
