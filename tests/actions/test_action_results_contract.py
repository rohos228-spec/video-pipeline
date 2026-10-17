"""Контракт результатов R2: примеры из md, схема, метаданные, html."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.orchestrator.actions.contracts import (
    CONTRACT_VERSION,
    ActionResult,
    EditResult,
    ReportResult,
    SoftDeleteResult,
    metadata_of,
    schema_canonical_bytes,
)

_REPO = Path(__file__).resolve().parents[2]
_MD = _REPO / "docs" / "fable" / "action_results_contract.md"
_SCHEMA = _REPO / "docs" / "fable" / "action_results_contract.schema.json"

# sha256 канонического экспорта закреплён за CONTRACT_VERSION.
_SCHEMA_SHA256 = {
    "ws2-r2.0": "712645e80e6b2447bf49a440d9b74a3618c2bd9bdf98f532420912de3861d290",
}


def _examples() -> list[dict]:
    text = _MD.read_text(encoding="utf-8")
    blocks = re.findall(r"```json\n(.*?)```", text, re.DOTALL)
    assert blocks, "в action_results_contract.md нет JSON-примеров"
    return [json.loads(block) for block in blocks]


def test_markdown_examples_match_models() -> None:
    examples = _examples()
    assert len(examples) >= 8
    for example in examples:
        ActionResult.model_validate(example)


def test_schema_export_sha256_is_pinned_to_version() -> None:
    raw = schema_canonical_bytes()
    assert _SCHEMA.read_bytes() == raw
    digest = hashlib.sha256(raw).hexdigest()
    payload = json.loads(raw)
    assert payload["version"] == CONTRACT_VERSION
    assert _SCHEMA_SHA256[CONTRACT_VERSION] == digest


def test_unknown_name_is_paid_and_destructive() -> None:
    meta = metadata_of("not_in_registry")
    assert meta.name == "not_in_registry"
    assert meta.paid is True
    assert meta.destructive is True
    assert meta.read_only is False
    assert meta.needs_approval is True


def test_report_html_with_script_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ReportResult(
            report_id=1,
            html="<p>ok</p><script>alert(1)</script>",
            version=1,
        )


def test_revert_node_to_git_has_medium_risk() -> None:
    meta = metadata_of("revert_node_to_git")
    assert meta.risk == "medium"
    assert meta.paid is False
    assert meta.destructive is False
    assert meta.needs_approval is False


def test_prompt_create_master_is_destructive() -> None:
    meta = metadata_of("prompt.create_master")
    assert meta.destructive is True
    assert meta.needs_approval is True


def test_soft_delete_ok_requires_marked_true() -> None:
    payload = {
        "change_id": "orch-appr-1-1",
        "action": "montage.delete_frame",
        "marked": False,
        "restore_action": "frame.restore",
        "snapshot_id": "snap-1",
    }
    SoftDeleteResult.model_validate(payload)
    with pytest.raises(ValidationError):
        ActionResult.model_validate({"status": "ok", "result": payload, "error": None})


def test_soft_delete_result_requires_snapshot_ref() -> None:
    with pytest.raises(ValidationError):
        SoftDeleteResult(
            change_id="orch-appr-1-1",
            action="montage.delete_frame",
            marked=True,
            restore_action="frame.restore",
        )
    by_id = SoftDeleteResult(
        change_id="orch-appr-1-1",
        action="montage.delete_frame",
        marked=True,
        restore_action="frame.restore",
        snapshot_id="snap-1",
    )
    by_archive = SoftDeleteResult(
        change_id="orch-appr-1-1",
        action="group.delete",
        marked=True,
        restore_action="group.restore",
        archive_ref="old/scenes/1",
    )
    assert by_id.snapshot_id == "snap-1"
    assert by_archive.archive_ref == "old/scenes/1"


def test_restore_action_supports_deleted_kind() -> None:
    from app.orchestrator.actions.contracts import SOFT_DELETE_RESTORE

    for action, restore in SOFT_DELETE_RESTORE.items():
        result = SoftDeleteResult(
            change_id="orch-appr-1-1",
            action=action,
            marked=True,
            restore_action=restore,
            snapshot_id="snap-1",
        )
        assert result.restore_action == restore
        with pytest.raises(ValidationError):
            SoftDeleteResult(
                change_id="orch-appr-1-1",
                action=action,
                marked=True,
                restore_action="history.rollback",
                snapshot_id="snap-1",
            )


def _edit_payload(**extra: object) -> dict:
    payload = {
        "action": "edit_node_prompt",
        "change_id": "orch-appr-1-1",
        "node_ref": {"project_id": 1, "node_id": "n_scene_writer", "kind": "prompt"},
        "diff": {"unified": "--- a/prompt\n+++ b/prompt\n@@\n-было\n+стало\n"},
        "open_url": "/api/projects/1",
        "reversible": True,
    }
    payload.update(extra)
    return payload


def test_edit_result_requires_before_or_snapshot() -> None:
    with pytest.raises(ValidationError):
        EditResult.model_validate(_edit_payload())
    with_before = EditResult.model_validate(_edit_payload(before={"prompt_template": "было"}))
    with_snap = EditResult.model_validate(_edit_payload(snapshot_id="snap-1"))
    assert with_before.before == {"prompt_template": "было"}
    assert with_snap.snapshot_id == "snap-1"


def test_suggest_scene_variants_tier_is_paid() -> None:
    """Опечатка paid+paid живёт только в документе #344."""

    meta = metadata_of("montage.suggest_scene_variants")
    assert meta.paid is True
    assert meta.destructive is False
    assert meta.risk == "high"
