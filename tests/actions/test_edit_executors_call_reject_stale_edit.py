"""N5: правки с устаревшей base_version не пишут данные и отвечают 409."""

from __future__ import annotations

import inspect

import pytest

from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import ensure_builtin_actions, registry

EDIT_IDS = frozenset({
    "prompt.save_master",
    "node.set_config",
    "node.set_prompt_variant",
    "node.set_disabled",
    "montage.set_assemble_params",
})

_CASES = {
    "prompt.save_master": (
        {"step_code": "plan", "name": "variant_a", "content": "новый", "base_version": 1},
        {("GET", "/prompt-files/plan/variant_a/content"): {"content": "старый", "version": 5}},
    ),
    "node.set_config": (
        {"project_id": 1, "node_key": "n1", "role": "review", "base_version": 1},
        {
            ("GET", "/projects/1"): {"id": 1, "status": "plan_ready", "meta": {"meta_version": 5}},
            ("GET", "/projects/1/gpt-operator/n1/resolve"): {"config": {"role": "assist"}},
        },
    ),
    "node.set_prompt_variant": (
        {"project_id": 1, "node_key": "n1", "slot_id": "main", "variant": "a.md", "base_version": 1},
        {("GET", "/projects/1"): {"id": 1, "status": "plan_ready", "meta": {"meta_version": 5}}},
    ),
    "node.set_disabled": (
        {"project_id": 1, "node_key": "n1", "disabled": True, "base_version": 1},
        {("GET", "/projects/1"): {
            "id": 1, "status": "plan_ready",
            "meta": {"meta_version": 5, "disabled_nodes": []},
        }},
    ),
    "montage.set_assemble_params": (
        {"project_id": 1, "bgm_level": 40, "base_version": 1},
        {("GET", "/projects/1"): {"id": 1, "status": "plan_ready", "meta": {"meta_version": 5}}},
    ),
}


class _Ctx:
    def __init__(self, routes):
        self.routes = routes
        self.calls: list[tuple] = []

    async def call(self, method, path, *, params=None, json=None, files=None, timeout=0):
        self.calls.append((method, path, params, json))
        value = self.routes.get((method, path), {})
        if isinstance(value, Exception):
            raise value
        return value


def _callable_sources(fn) -> str:
    chunks: list[str] = []
    seen: set[int] = set()
    stack = [fn]
    while stack:
        current = stack.pop()
        if not callable(current) or id(current) in seen:
            continue
        seen.add(id(current))
        try:
            chunks.append(inspect.getsource(current))
        except (OSError, TypeError):
            pass
        for cell in getattr(current, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:
                continue
            if callable(value):
                stack.append(value)
    return "\n".join(chunks)


def _edit_ids() -> list[str]:
    ensure_builtin_actions()
    found = sorted(spec.id for spec in registry.list() if "edit" in spec.tags)
    return found or ["__no_edit_actions__"]


@pytest.mark.parametrize("action_id", _edit_ids())
async def test_edit_executors_call_reject_stale_edit(action_id: str) -> None:
    ensure_builtin_actions()
    found = {spec.id for spec in registry.list() if "edit" in spec.tags}
    assert found == EDIT_IDS
    assert action_id in _CASES
    from app.orchestrator.actions.contracts import guard_stale_edit, reject_stale_edit

    spec = registry.get(action_id)
    source = _callable_sources(spec.executor)
    assert "guard_stale_edit" in source or "reject_stale_edit" in source
    assert "reject_stale_edit" in inspect.getsource(guard_stale_edit)
    params, routes = _CASES[action_id]
    ctx = _Ctx(routes)
    with pytest.raises(ActionCallError) as exc:
        await spec.executor(ctx, dict(params))
    assert exc.value.status == 409
    assert exc.value.detail == "conflict"
    assert not [call for call in ctx.calls if call[0] in ("PATCH", "PUT", "POST", "DELETE")]
    fresh = reject_stale_edit(base_version=1, current_version=5)
    assert fresh.status == "failed" and fresh.error == "conflict" and fresh.result is None
