"""R0b: PATCH /projects/{id} не должен обходить POST spend-approval (операторский токен)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.models import Project, ProjectStatus
from app.services.project_meta import merge_project_meta, set_meta_fields
from app.services.step_failure_policy import SPEND_APPROVAL_META_KEYS
from tests.action_layer_support import env  # noqa: F401

_WAITING_META = {
    "approval_status": "waiting_approval",
    "approval_reason": "budget_exhausted",
    "paused_from_status": "running",
}


async def _waiting_project(env) -> int:
    async with env.factory() as s:
        p = Project(
            slug="r0b-patch-bypass",
            topic="r0b",
            title="r0b",
            status=ProjectStatus.paused,
            meta=dict(_WAITING_META),
        )
        s.add(p)
        await s.commit()
        return p.id


@pytest.mark.raw_client
async def test_patch_meta_cannot_approve_spend(env) -> None:  # noqa: F811
    pid = await _waiting_project(env)
    r = await env.client.patch(
        f"/api/projects/{pid}",
        json={
            "approval_status": "approved",
            "meta": {
                "approval_status": "approved",
                "approval_reason": "",
                "paused_from_status": "done",
                "note": "x",
            },
        },
    )
    assert r.status_code == 200, r.text
    r = await env.client.get(f"/api/projects/{pid}")
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["approval_status"] == "waiting_approval"
    assert meta["approval_reason"] == "budget_exhausted"
    assert meta["paused_from_status"] == "running"
    assert meta["note"] == "x"


@pytest.mark.raw_client
async def test_spend_approval_requires_operator_token(env) -> None:  # noqa: F811
    pid = await _waiting_project(env)
    r = await env.client.post(f"/api/projects/{pid}/spend-approval")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "operator_token_required"
    r = await env.client.get(f"/api/projects/{pid}")
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["approval_status"] == "waiting_approval"


def test_spend_approval_keys_set() -> None:
    assert SPEND_APPROVAL_META_KEYS == {"approval_status", "approval_reason", "paused_from_status"}


def test_merge_project_meta_patch_project_keeps_approval_keys() -> None:
    merged = merge_project_meta(
        dict(_WAITING_META),
        {"approval_status": "approved", "approval_reason": None, "note": "x"},
        source="patch_project",
    )
    assert merged["approval_status"] == "waiting_approval"
    assert merged["approval_reason"] == "budget_exhausted"
    assert merged["paused_from_status"] == "running"
    assert merged["note"] == "x"


def test_merge_project_meta_patch_project_cannot_add_approval_keys() -> None:
    merged = merge_project_meta(
        {"note": "a"},
        {"approval_status": "approved", "paused_from_status": "done", "note": "b"},
        source="patch_project",
    )
    assert "approval_status" not in merged
    assert "paused_from_status" not in merged
    assert merged["note"] == "b"


def test_internal_callers_still_set_approval_keys() -> None:
    merged = merge_project_meta(dict(_WAITING_META), {"approval_status": "approved"}, source="worker")
    assert merged["approval_status"] == "approved"
    project = SimpleNamespace(id=1, meta=dict(_WAITING_META))
    set_meta_fields(project, approval_status="approved")
    assert project.meta["approval_status"] == "approved"
    assert project.meta["approval_reason"] == "budget_exhausted"
