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


def test_risk_mapping_read_local_safe_paid_destructive() -> None:
    """read→none, local_safe→low, paid→medium, destructive→high.

    Опубликованные флаги не слабее этой шкалы и не слабее audit_metadata.json.
    prompt.create_master остаётся destructive, хотя в коде ярус local_safe.
    """

    from app.orchestrator.action_contracts.contracts import metadata_of as contract_metadata
    from app.orchestrator.actions.executor import map_nikita_risk, metadata_of
    from app.services.action_layer.spec import registry

    read = map_nikita_risk("read", name="tier.read")
    assert read.risk == "none"
    assert read.read_only is True
    assert read.destructive is False
    assert read.paid is False
    assert read.needs_approval is False

    local = map_nikita_risk("local_safe", name="tier.local")
    assert local.risk == "low"
    assert local.read_only is False
    assert local.destructive is False
    assert local.paid is False
    assert local.needs_approval is False

    paid = map_nikita_risk("paid", name="tier.paid")
    assert paid.risk == "medium"
    assert paid.paid is True
    assert paid.read_only is False
    assert paid.needs_approval is True

    destructive = map_nikita_risk("destructive", name="tier.destructive")
    assert destructive.risk == "high"
    assert destructive.destructive is True
    assert destructive.needs_approval is True

    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    ensure_builtin_actions()
    master_spec = registry.find("prompt.create_master")
    assert master_spec is not None
    assert master_spec.risk == "local_safe"
    master = metadata_of("prompt.create_master")
    assert master.destructive is True
    assert master.needs_approval is True
    assert master == contract_metadata("prompt.create_master")

    for spec in registry.list():
        floor = map_nikita_risk(spec.risk, name=spec.id)
        published = metadata_of(spec.id)
        assert published == contract_metadata(spec.id)
        assert rank[published.risk] >= rank[floor.risk]
        if floor.destructive:
            assert published.destructive is True
        if floor.paid:
            assert published.paid is True
        if floor.read_only:
            assert published.read_only is True


async def test_adapter_never_emits_core_change_id(env) -> None:
    """Одобрение без change_id. Идентификатор Никиты — только cc_change_id."""

    import json

    from app.orchestrator.action_contracts.contracts import ApprovalRequired
    from app.orchestrator.actions.executor import execute
    from app.services.action_layer.spec import registry
    from tests.action_layer_support import make_spec

    ctx = {"app": env.app, "operator": "adapter-test"}
    held = await execute(
        "prompt.create_master",
        {"step_code": "anim_pr", "name": "adapter-probe", "content": "текст"},
        ctx,
    )
    assert held.status == "approval_required"
    assert held.error is None
    assert isinstance(held.result, ApprovalRequired)
    assert held.result.metadata.destructive is True
    assert held.result.metadata.needs_approval is True
    envelope = json.dumps(held.model_dump(), ensure_ascii=False)
    assert "orch-appr-" not in envelope
    assert "change_id" not in held.model_dump()["result"]

    async def _nikita(_ctx, _params):
        return {
            "ok": True,
            "change_id": "orch-appr-7-1",
            "proposal_id": "cc-proposal-1",
        }

    spec = make_spec("adapter.probe_cc", _nikita, risk="local_safe")
    registry.register(spec)
    try:
        probed = await execute("adapter.probe_cc", {}, {**ctx, "approved": True})
    finally:
        registry.unregister("adapter.probe_cc")
    assert probed.status == "ok"
    assert probed.cc_change_id == "cc-proposal-1"
    dumped = probed.model_dump()
    assert "orch-appr-" not in json.dumps(dumped)
    assert dumped["result"].get("change_id") is None
    assert dumped["result"].get("proposal_id") == "cc-proposal-1"
