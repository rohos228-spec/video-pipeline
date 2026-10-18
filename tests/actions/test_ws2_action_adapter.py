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


def test_metadata_of_covers_registry() -> None:
    """Метаданные контракта есть у каждого из 102 имён живого реестра."""

    from app.orchestrator.action_contracts.contracts import ActionMetadata
    from app.orchestrator.action_contracts.contracts import metadata_of as contract_metadata
    from app.orchestrator.actions.executor import metadata_of
    from app.services.action_layer.spec import registry

    ensure_builtin_actions()
    specs = registry.list()
    assert len(specs) == 102
    for spec in specs:
        meta = metadata_of(spec.id)
        assert isinstance(meta, ActionMetadata)
        assert meta.name == spec.id
        assert meta.risk in {"none", "low", "medium", "high"}
        assert meta == contract_metadata(spec.id)
    raised = metadata_of("create_node@new_group")
    assert raised.risk == "high"
    assert raised.destructive is True
    assert raised.needs_approval is True


def test_registry_populated_on_import() -> None:
    """Импорт адаптера сам вызывает ensure_builtin_actions: 102 имени."""

    import subprocess
    import sys

    code = (
        "import app.orchestrator.actions\n"
        "from app.services.action_layer.spec import registry\n"
        "ids = [spec.id for spec in registry.list()]\n"
        "assert len(ids) == 102, len(ids)\n"
        "assert len(set(ids)) == 102\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd="/workspace",
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
