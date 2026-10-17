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
    ApprovalRequired,
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
    "ws2-r2.1": "e4eb29277f0d2128fcae5ec1006c7b95ba5522529e362129a90d39884c1fab9d",
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
    assert meta.destructive is True
    assert meta.needs_approval is True


def test_r2_flags_not_weaker_than_342_analogues() -> None:
    rank = {"none": 0, "low": 1, "medium": 2, "high": 3}
    analogue = metadata_of("project.resume")
    resume = metadata_of("resume_node")
    assert rank[resume.risk] >= rank[analogue.risk]
    if analogue.destructive:
        assert resume.destructive is True
    if analogue.paid:
        assert resume.paid is True
    assert resume.needs_approval is True


def test_unversioned_edit_actions_need_approval() -> None:
    for name in (
        "edit_node_prompt",
        "edit_node_params",
        "update_report",
        "revert_node_to_git",
    ):
        meta = metadata_of(name)
        assert meta.destructive is True
        assert meta.needs_approval is True


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
        "version": 2,
        "base_version": 1,
    }
    payload.update(extra)
    return payload


def test_approval_metadata_matches_metadata_of() -> None:
    payload = {
        "action": "prompt.delete_master",
        "metadata": metadata_of("prompt.delete_master").model_dump(),
        "reason": "пометка",
        "preview": {"change": "файл остаётся", "estimate": 0},
    }
    ApprovalRequired.model_validate(payload)
    lowered = {
        **payload,
        "metadata": {**payload["metadata"], "destructive": False, "paid": False, "risk": "low"},
    }
    with pytest.raises(ValidationError):
        ApprovalRequired.model_validate(lowered)
    unknown = {
        "action": "not_in_registry",
        "metadata": {
            "name": "not_in_registry",
            "risk": "low",
            "read_only": True,
            "destructive": False,
            "paid": False,
        },
        "reason": "обход",
        "preview": {"change": "что-то", "estimate": None},
    }
    with pytest.raises(ValidationError):
        ApprovalRequired.model_validate(unknown)


def test_paid_approval_estimate_positive_or_flagged() -> None:
    def build(name: str, estimate: float | None) -> dict:
        return {
            "action": name,
            "metadata": metadata_of(name).model_dump(),
            "reason": "проверка оценки",
            "preview": {"change": "вызов", "estimate": estimate},
        }

    unknown = ApprovalRequired.model_validate(build("montage.suggest_scene_variants", None))
    priced = ApprovalRequired.model_validate(build("montage.suggest_scene_variants", 0.4))
    assert unknown.preview.estimate is None
    assert priced.preview.estimate == 0.4
    with pytest.raises(ValidationError):
        ApprovalRequired.model_validate(build("montage.suggest_scene_variants", 0))
    free = ApprovalRequired.model_validate(build("prompt.delete_master", 0))
    assert free.preview.estimate == 0


def test_approval_required_has_no_change_id() -> None:
    assert "change_id" not in ApprovalRequired.model_fields
    payload = {
        "action": "prompt.delete_master",
        "metadata": metadata_of("prompt.delete_master").model_dump(),
        "reason": "пометка вместо удаления",
        "preview": {"change": "файл остаётся", "estimate": 0},
        "change_id": "orch-appr-1-1",
    }
    with pytest.raises(ValidationError):
        ApprovalRequired.model_validate(payload)
    clean = dict(payload)
    clean.pop("change_id")
    ApprovalRequired.model_validate(clean)


def test_diff_distinguishes_removed_key() -> None:
    from app.orchestrator.actions.contracts import Diff, FieldChange

    removed = FieldChange(op="removed", before=4)
    nulled = FieldChange(op="set", before=4, after=None)
    assert removed.op == "removed"
    assert nulled.after is None
    assert removed.model_dump() != nulled.model_dump()
    diff = Diff(fields={"db_version": removed, "source": nulled})
    assert diff.fields["db_version"].op == "removed"
    with pytest.raises(ValidationError):
        FieldChange(op="removed", before=4, after=1)


def test_action_result_has_warnings_list() -> None:
    quiet = ActionResult(status="failed", result=None, error="сбой")
    assert quiet.warnings == []
    loud = ActionResult(
        status="failed",
        result=None,
        error="сбой",
        warnings=["shot 2 пропущен"],
    )
    assert loud.warnings == ["shot 2 пропущен"]


def test_partial_edit_lists_skipped_items() -> None:
    edited = EditResult.model_validate(
        _edit_payload(before={"a": 1}, partial=True, skipped=["shot-2"])
    )
    assert edited.partial is True
    assert edited.skipped == ["shot-2"]
    with pytest.raises(ValidationError):
        EditResult.model_validate(_edit_payload(before={"a": 1}, partial=True, skipped=[]))
    deleted = SoftDeleteResult(
        change_id="orch-appr-1-1",
        action="montage.delete_frame",
        marked=True,
        restore_action="frame.restore",
        snapshot_id="snap-1",
        partial=True,
        skipped=["frame-9"],
    )
    assert deleted.skipped == ["frame-9"]
    with pytest.raises(ValidationError):
        SoftDeleteResult(
            change_id="orch-appr-1-1",
            action="montage.delete_frame",
            marked=True,
            restore_action="frame.restore",
            snapshot_id="snap-1",
            partial=True,
        )


def test_skipped_implies_partial() -> None:
    edited = EditResult.model_validate(_edit_payload(before={"a": 1}, skipped=["shot-2"]))
    deleted = SoftDeleteResult(
        change_id="orch-appr-1-1",
        action="montage.delete_frame",
        marked=True,
        restore_action="frame.restore",
        snapshot_id="snap-1",
        skipped=["frame-9"],
    )
    assert edited.partial is True
    assert deleted.partial is True


def test_snapshot_ref_not_blank() -> None:
    for blank in ("", " ", " \n\t"):
        with pytest.raises(ValidationError):
            EditResult.model_validate(_edit_payload(before={"a": 1}, snapshot_id=blank))
        with pytest.raises(ValidationError):
            SoftDeleteResult(
                change_id="orch-appr-1-1",
                action="montage.delete_frame",
                marked=True,
                restore_action="frame.restore",
                snapshot_id=blank,
                archive_ref="old/scenes/1",
            )
        with pytest.raises(ValidationError):
            SoftDeleteResult(
                change_id="orch-appr-1-1",
                action="group.delete",
                marked=True,
                restore_action="group.restore",
                snapshot_id="snap-1",
                archive_ref=blank,
            )
    EditResult.model_validate(_edit_payload(snapshot_id="snap-1"))
    kept = SoftDeleteResult(
        change_id="orch-appr-1-1",
        action="group.delete",
        marked=True,
        restore_action="group.restore",
        archive_ref="old/scenes/1",
    )
    assert kept.archive_ref == "old/scenes/1"


def test_edit_result_version_after_base() -> None:
    with pytest.raises(ValidationError):
        EditResult.model_validate(_edit_payload(before={"a": 1}, version=1, base_version=5))
    with pytest.raises(ValidationError):
        EditResult.model_validate(_edit_payload(before={"a": 1}, version=2, base_version=2))
    edited = EditResult.model_validate(_edit_payload(before={"a": 1}, version=6, base_version=5))
    assert edited.version > edited.base_version


def test_edit_result_has_required_version() -> None:
    missing = _edit_payload(before={"prompt_template": "было"})
    missing.pop("version")
    missing.pop("base_version")
    with pytest.raises(ValidationError):
        EditResult.model_validate(missing)
    edited = EditResult.model_validate(
        _edit_payload(before={"prompt_template": "было"}, version=2, base_version=1)
    )
    report = EditResult.model_validate(
        _edit_payload(
            action="update_report",
            before={"html": "<p>a</p>"},
            node_ref={"project_id": 1, "node_id": "7", "kind": "report"},
            version=4,
            base_version=3,
        )
    )
    assert edited.version == 2 and edited.base_version == 1
    assert report.action == "update_report"
    assert report.version == 4 and report.base_version == 3
    with pytest.raises(ValidationError):
        EditResult.model_validate(_edit_payload(before={"a": 1}, version=0, base_version=1))


def test_edit_stale_version_returns_409() -> None:
    from app.orchestrator.actions.contracts import EDIT_CONFLICT_HTTP, reject_stale_edit

    assert EDIT_CONFLICT_HTTP == 409
    failed = reject_stale_edit(base_version=1, current_version=3)
    assert failed.status == "failed"
    assert failed.error == "conflict"
    assert failed.result is None
    with pytest.raises(ValueError):
        reject_stale_edit(base_version=3, current_version=3)


def test_edit_result_requires_before_or_snapshot() -> None:
    with pytest.raises(ValidationError):
        EditResult.model_validate(_edit_payload())
    with_before = EditResult.model_validate(_edit_payload(before={"prompt_template": "было"}))
    with_snap = EditResult.model_validate(_edit_payload(snapshot_id="snap-1"))
    assert with_before.before == {"prompt_template": "было"}
    assert with_snap.snapshot_id == "snap-1"


def test_suggest_scene_variants_tier_is_paid() -> None:
    """Ярус paid. Ячейка paid+paid, если вернётся в таблицу, не становится destructive."""

    meta = metadata_of("montage.suggest_scene_variants")
    assert meta.paid is True
    assert meta.destructive is False
    assert meta.risk == "high"


def _flags_from_doc_tier(name: str, tier: str) -> tuple[bool, bool, bool]:
    """Те же правила, что текст контракта: read / destructive / paid и две особые строки."""

    parts = set(tier.split("+"))
    if name == "montage.suggest_scene_variants" and tier == "paid+paid":
        parts = {"paid"}
    read_only = tier == "read"
    paid = "paid" in parts
    destructive = "destructive" in parts
    if name == "prompt.create_master":
        destructive = True
        read_only = False
    return read_only, destructive, paid


def _audit_flag_rows(text: str) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("## Таблица действий"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells or not cells[0].isdigit():
            continue
        rows.append((cells[1].strip("`").strip(), cells[4], cells[5]))
    return rows


def _audit_headings(text: str) -> list[tuple[str, str, str]]:
    return re.findall(
        r"^#### `([^`]+)` \(risk: ([a-z]+), tier: ([a-z0-9+]+)\)",
        text,
        re.MULTILINE,
    )


def _contract_flag_rows(text: str) -> list[tuple[str, str, bool, bool, bool]]:
    rows: list[tuple[str, str, bool, bool, bool]] = []
    in_table = False
    for line in text.splitlines():
        if line.startswith("## Имена R2"):
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        rows.append(
            (
                cells[0].strip("`"),
                cells[1],
                cells[2] == "true",
                cells[3] == "true",
                cells[4] == "true",
            )
        )
    return rows


def test_md_approval_card_comes_from_core_events() -> None:
    text = _MD.read_text(encoding="utf-8")
    assert "ws3-r3.4" in text
    assert "approval_decided" in text
    assert "только сообщает ядру" in text
    assert "change_id" not in ApprovalRequired.model_fields


def test_doc_flags_match_contract_metadata() -> None:
    from app.orchestrator.actions.contracts import metadata_registry

    audit = (_REPO / "docs" / "fable" / "actions_audit.md").read_text(encoding="utf-8")
    contract = _MD.read_text(encoding="utf-8")
    registry = metadata_registry()
    covered: set[str] = set()

    table = _audit_flag_rows(audit)
    assert len(table) == 102
    for name, risk, tier in table:
        meta = registry[name]
        assert meta.risk == risk, name
        assert (meta.read_only, meta.destructive, meta.paid) == _flags_from_doc_tier(name, tier), name
        covered.add(name)

    headings = _audit_headings(audit)
    assert headings, "в аудите нет ярусов новых действий"
    for name, risk, tier in headings:
        meta = metadata_of(name)
        assert meta.risk == risk, name
        assert (meta.read_only, meta.destructive, meta.paid) == _flags_from_doc_tier(name, tier), name
        covered.add(name)
    revert = metadata_of("revert_node_to_git")
    assert revert.destructive is True
    assert any(name == "revert_node_to_git" and tier == "destructive" for name, _, tier in headings)

    documented = _contract_flag_rows(contract)
    assert documented, "в контракте нет таблицы флагов"
    for name, risk, read_only, destructive, paid in documented:
        meta = metadata_of(name)
        assert meta.risk == risk, name
        assert meta.read_only is read_only, name
        assert meta.destructive is destructive, name
        assert meta.paid is paid, name
        covered.add(name)

    assert set(registry) == covered
