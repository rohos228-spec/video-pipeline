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

    import pytest

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
        assert meta.model_dump(include={"name", "risk", "read_only", "destructive", "paid"}) == (
            contract_metadata(spec.id).model_dump()
        )
    with pytest.raises(ValueError):
        metadata_of("create_node@new_group")
    raised = metadata_of("create_node", group_is_new=True)
    assert raised.name == "create_node"
    assert "@" not in raised.name
    assert raised.risk == "high"
    assert raised.destructive is True
    assert raised.needs_approval is True
    plain = metadata_of("create_node", group_is_new=False)
    assert plain.risk == "medium"
    assert plain.destructive is False


def test_metadata_marks_soft_delete_with_restore() -> None:
    """Мягкое удаление видно в metadata_of: soft_delete и restore_action."""

    from app.orchestrator.action_contracts.contracts import SOFT_DELETE_RESTORE
    from app.orchestrator.actions.executor import metadata_of

    expected = {
        "montage.delete_frame": "frame.restore",
        "montage.delete_child_frame": "frame.restore",
        "group.delete": "group.restore",
        "prompt.delete_master": "prompt.restore_master",
    }
    assert expected == SOFT_DELETE_RESTORE
    for action, restore in expected.items():
        meta = metadata_of(action)
        assert meta.soft_delete is True
        assert meta.restore_action == restore
        dumped = meta.model_dump()
        assert dumped["soft_delete"] is True
        assert dumped["restore_action"] == restore
    plain = metadata_of("history.list_snapshots")
    assert plain.model_dump().get("soft_delete") is not True
    assert metadata_of("frame.restore").model_dump().get("soft_delete") is not True


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
        assert published.model_dump(include={"name", "risk", "read_only", "destructive", "paid"}) == (
            contract_metadata(spec.id).model_dump()
        )
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
        probed = await execute(
            "adapter.probe_cc",
            {},
            {
                **ctx,
                "approval": {
                    "change_id": "orch-appr-7-1",
                    "action": "adapter.probe_cc",
                    "risk": "high",
                    "destructive": True,
                    "granted": True,
                    "cc_change_id": "cc-proposal-1",
                },
            },
        )
    finally:
        registry.unregister("adapter.probe_cc")
    assert probed.status == "ok"
    assert probed.cc_change_id == "cc-proposal-1"
    dumped = probed.model_dump()
    assert "orch-appr-" not in json.dumps(dumped)
    assert dumped["result"].get("change_id") is None
    assert dumped["result"].get("proposal_id") == "cc-proposal-1"


async def test_execute_requires_matching_granted_approval(env) -> None:
    """risk high или destructive: исполнитель только после совпавшего granted."""

    from app.orchestrator.action_contracts.contracts import ApprovalRequired
    from app.orchestrator.actions.executor import execute
    from app.services.action_layer.spec import registry
    from tests.action_layer_support import make_spec

    calls = {"n": 0}

    async def _run(_ctx, _params):
        calls["n"] += 1
        return {"ok": True, "marker": "ran"}

    spec = make_spec("adapter.approval_probe", _run, risk="local_safe")
    registry.register(spec)
    base = {"app": env.app, "operator": "adapter-test"}
    form = {
        "change_id": "orch-appr-4-2",
        "action": "adapter.approval_probe",
        "risk": "high",
        "destructive": True,
        "granted": True,
        "cc_change_id": "cc-from-core",
    }
    try:
        missing = await execute("adapter.approval_probe", {}, base)
        assert "approval" not in base
        denied = await execute(
            "adapter.approval_probe",
            {},
            {**base, "approval": {**form, "granted": False}},
        )
        foreign = await execute(
            "adapter.approval_probe",
            {},
            {**base, "approval": {**form, "action": "other.action"}},
        )
        for held in (missing, denied, foreign):
            assert held.status == "approval_required"
            assert held.error is None
            assert isinstance(held.result, ApprovalRequired)
            assert "change_id" not in held.result.model_dump()
            assert "orch-appr-" not in held.model_dump_json()
        assert calls["n"] == 0

        done = await execute(
            "adapter.approval_probe",
            {},
            {**base, "approval": form},
        )
        assert done.status == "ok"
        assert calls["n"] == 1
        assert done.result["marker"] == "ran"
        assert done.cc_change_id == "cc-from-core"
        assert "orch-appr-" not in done.model_dump_json()
        assert done.model_dump()["result"].get("change_id") is None
    finally:
        registry.unregister("adapter.approval_probe")


def test_whitelist_built_from_registry_matches_audit() -> None:
    """Белый список — живой реестр плюс реализованный frame.restore.

    AUDIT_ACTION_IDS только сверка. group.restore и prompt.restore_master
    не реализованы и в список не входят.
    """

    import inspect

    from app.orchestrator.action_contracts.contracts import AUDIT_ACTION_IDS
    from app.orchestrator.actions.executor import whitelist

    source = inspect.getsource(whitelist)
    assert "actions_audit.md" not in source
    assert "AUDIT_ACTION_IDS" not in source
    names = whitelist()
    assert names == sorted(names)
    assert len(names) == len(set(names))
    assert set(names) == set(AUDIT_ACTION_IDS) | {"frame.restore"}
    assert "group.restore" not in names
    assert "prompt.restore_master" not in names


def test_whitelist_names_match_action_pattern() -> None:
    """Каждое имя whitelist() подходит под pattern контракта, без @."""

    import re

    from app.orchestrator.action_contracts.contracts import AUDIT_ACTION_IDS
    from app.orchestrator.actions.executor import metadata_of, whitelist

    pattern = re.compile(r"^[a-z0-9_]+(\.[a-z0-9_]+)*$")
    names = whitelist()
    assert "frame.restore" in names
    assert pattern.fullmatch("frame.restore")
    assert set(names) == set(AUDIT_ACTION_IDS) | {"frame.restore"}
    assert all(pattern.fullmatch(name) for name in names)
    meta = metadata_of("frame.restore")
    assert meta.risk == "high"
    assert meta.needs_approval is True


async def test_delete_frame_via_adapter_is_soft(env) -> None:
    """delete_frame и delete_child_frame помечают кадр и не стирают строку."""

    from sqlalchemy import select

    from app.models import Frame, Project
    from app.orchestrator.action_contracts.contracts import SoftDeleteResult
    from app.orchestrator.actions.executor import execute
    from app.services.frame_tombstones import deleted_numbers

    pid = await env.add_project(frames=2)
    ids = await env.frame_ids(pid)
    shots = [
        {"id": "s1", "номер": 1, "активен": True, "текст": "первый"},
        {"id": "s2", "номер": 2, "активен": True, "текст": "второй"},
    ]
    async with env.factory() as session:
        for fid in ids.values():
            frame = await session.get(Frame, fid)
            frame.attrs = {"кадры": [dict(shot) for shot in shots]}
        await session.commit()

    bare = {"app": env.app, "operator": "adapter-test"}
    held = await execute(
        "montage.delete_frame",
        {"project_id": pid, "frame_id": ids[1]},
        bare,
    )
    assert held.status == "approval_required"
    assert "orch-appr-" not in held.model_dump_json()

    def grant(action: str, change_id: str = "orch-appr-7-1") -> dict:
        return {
            **bare,
            "approval": {
                "change_id": change_id,
                "action": action,
                "risk": "high",
                "destructive": True,
                "granted": True,
                "cc_change_id": None,
            },
        }

    missing_id = await execute(
        "montage.delete_frame",
        {"project_id": pid, "frame_id": ids[1]},
        grant("montage.delete_frame", change_id=""),
    )
    assert missing_id.status == "failed"
    assert "orch-appr-" not in (missing_id.error or "")

    deleted = await execute(
        "montage.delete_frame",
        {"project_id": pid, "frame_id": ids[1]},
        grant("montage.delete_frame"),
    )
    child = await execute(
        "montage.delete_child_frame",
        {"project_id": pid, "frame_number": 2},
        grant("montage.delete_child_frame"),
    )
    for result, action in (
        (deleted, "montage.delete_frame"),
        (child, "montage.delete_child_frame"),
    ):
        assert result.status == "ok"
        assert isinstance(result.result, SoftDeleteResult)
        assert result.result.marked is True
        assert result.result.snapshot_id
        assert result.result.change_id == "orch-appr-7-1"
        assert result.result.action == action
        assert result.result.restore_action == "frame.restore"

    async with env.factory() as session:
        rows = list((await session.scalars(select(Frame).where(Frame.project_id == pid))).all())
        assert {row.id for row in rows} == set(ids.values())
        project = await session.get(Project, pid)
        assert deleted_numbers(project) == {1, 2}
        for row in rows:
            kadry = row.attrs["кадры"]
            by_id = {shot["id"]: shot for shot in kadry}
            assert set(by_id) == {"s1", "s2"}
            marked = "s1" if row.number == 1 else "s2"
            kept = "s2" if row.number == 1 else "s1"
            assert by_id[marked]["активен"] is False
            assert by_id[kept]["активен"] is True
            assert by_id[kept]["текст"] == ("второй" if kept == "s2" else "первый")

    assert list(env.tmp_path.rglob("deleted-*")) == []
    bare_restore = await execute("frame.restore", {"project_id": pid, "snapshot_id": deleted.result.snapshot_id}, bare)
    assert bare_restore.status == "approval_required"
    assert bare_restore.result.action == "frame.restore"


async def test_delete_frame_then_frame_restore_roundtrip(env) -> None:
    """delete_frame помечает кадр, frame.restore снимает пометку по снимку и по change_id."""

    from sqlalchemy import select

    from app.models import Frame, Project
    from app.orchestrator.actions.executor import execute
    from app.services.frame_tombstones import deleted_numbers

    pid = await env.add_project(frames=2)
    ids = await env.frame_ids(pid)
    shots = [
        {"id": "s1", "номер": 1, "активен": True, "текст": "первый"},
        {"id": "s2", "номер": 2, "активен": True, "текст": "второй"},
    ]
    async with env.factory() as session:
        for fid in ids.values():
            frame = await session.get(Frame, fid)
            frame.attrs = {"кадры": [dict(shot) for shot in shots]}
        await session.commit()

    def grant(action: str, change_id: str) -> dict:
        return {
            "app": env.app,
            "operator": "adapter-test",
            "approval": {
                "change_id": change_id,
                "action": action,
                "risk": "high",
                "destructive": True,
                "granted": True,
                "cc_change_id": "cc-restore-1",
            },
        }

    deleted = await execute(
        "montage.delete_frame",
        {"project_id": pid, "frame_id": ids[1]},
        grant("montage.delete_frame", "orch-appr-7-1"),
    )
    assert deleted.status == "ok"
    snapshot_id = deleted.result.snapshot_id

    held = await execute(
        "frame.restore",
        {"project_id": pid, "snapshot_id": snapshot_id},
        {"app": env.app, "operator": "adapter-test"},
    )
    assert held.status == "approval_required"

    restored = await execute(
        "frame.restore",
        {"project_id": pid, "snapshot_id": snapshot_id},
        grant("frame.restore", "orch-appr-8-1"),
    )
    assert restored.status == "ok"
    assert restored.result["action"] == "frame.restore"
    assert restored.result["change_id"] == "orch-appr-8-1"
    assert restored.result["snapshot_id"] == snapshot_id
    assert restored.result["restored"] is True
    assert restored.cc_change_id == "cc-restore-1"

    async with env.factory() as session:
        frame = await session.get(Frame, ids[1])
        by_id = {shot["id"]: shot for shot in frame.attrs["кадры"]}
        assert by_id["s1"]["активен"] is True
        assert by_id["s2"]["активен"] is True
        project_row = await session.get(Project, pid)
        assert 1 not in deleted_numbers(project_row)
        assert frame.id == ids[1]

    again = await execute(
        "frame.restore",
        {"project_id": pid, "snapshot_id": snapshot_id},
        grant("frame.restore", "orch-appr-8-2"),
    )
    assert again.status == "failed"

    child = await execute(
        "montage.delete_child_frame",
        {"project_id": pid, "frame_number": 2},
        grant("montage.delete_child_frame", "orch-appr-7-2"),
    )
    assert child.status == "ok"
    by_change = await execute(
        "frame.restore",
        {"project_id": pid, "change_id": "orch-appr-7-2"},
        grant("frame.restore", "orch-appr-8-3"),
    )
    assert by_change.status == "ok"
    assert by_change.result["restored"] is True
    async with env.factory() as session:
        project_row = await session.get(Project, pid)
        assert deleted_numbers(project_row) == set()
        frame = await session.get(Frame, ids[2])
        marked = [shot for shot in frame.attrs["кадры"] if shot["номер"] == 2]
        assert marked[0]["активен"] is True
        rows = list((await session.scalars(select(Frame).where(Frame.project_id == pid))).all())
        assert {row.id for row in rows} == set(ids.values())


def _action_names(node, key=None):
    found = []
    if isinstance(node, dict):
        for child_key, value in node.items():
            found.extend(_action_names(value, child_key))
    elif isinstance(node, list):
        for value in node:
            found.extend(_action_names(value, key))
    elif isinstance(node, str) and key in {"action", "restore_action", "name"}:
        found.append(node)
    return found


async def test_adapter_result_matches_contract_schema(env) -> None:
    """Ответ адаптера проходит JSON Schema влитого #378. В action нет «@»."""

    import jsonschema

    from app.models import Frame
    from app.orchestrator.action_contracts.contracts import export_action_result_schema
    from app.orchestrator.actions.executor import execute

    schema = export_action_result_schema()["models"]["ActionResult"]
    pid = await env.add_project(frames=1)
    ids = await env.frame_ids(pid)
    async with env.factory() as session:
        frame = await session.get(Frame, ids[1])
        frame.attrs = {"кадры": [{"id": "s1", "номер": 1, "активен": True, "текст": "первый"}]}
        await session.commit()

    bare = {"app": env.app, "operator": "adapter-test"}
    held = await execute("montage.delete_frame", {"project_id": pid, "frame_id": ids[1]}, bare)
    done = await execute(
        "montage.delete_frame",
        {"project_id": pid, "frame_id": ids[1]},
        {
            **bare,
            "approval": {
                "change_id": "orch-appr-4-1",
                "action": "montage.delete_frame",
                "risk": "high",
                "destructive": True,
                "granted": True,
                "cc_change_id": None,
            },
        },
    )
    for result in (held, done):
        payload = result.model_dump(mode="json")
        jsonschema.validate(payload, schema)
        names = _action_names(payload)
        assert names
        assert all("@" not in name for name in names)
