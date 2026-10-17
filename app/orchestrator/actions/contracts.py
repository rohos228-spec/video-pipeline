"""Локальный эквивалент контракта ws2-r2.2 (PR #378).

На ветке R0b файла контракта нет: он живёт в
``cursor/ws2-r2-action-results-contract``. Семантика имён, ``snapshot_id`` /
``archive_ref`` и конверта ``ActionResult`` совпадает с #378. Новых полей и
моделей нет. Успех ``frame.restore`` / ``group.restore`` /
``prompt.restore_master`` — это ``EditResult`` внутри ``ActionResult``:
отдельного тела восстановления контракт не задаёт, а ``SoftDeleteResult``
принимает только id удаления.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "ws2-r2.2"

_CHANGE_ID = re.compile(r"^orch-appr-[0-9]+-[0-9]+$")

SOFT_DELETE_RESTORE: dict[str, str] = {
    "montage.delete_frame": "frame.restore",
    "montage.delete_child_frame": "frame.restore",
    "group.delete": "group.restore",
    "prompt.delete_master": "prompt.restore_master",
}

RiskName = Literal["none", "low", "medium", "high"]
ActionStatusName = Literal["ok", "approval_required", "failed"]


class ActionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    risk: RiskName
    read_only: bool
    destructive: bool
    paid: bool


class NodeKind(StrEnum):
    node = "node"
    prompt = "prompt"
    params = "params"
    report = "report"


class NodeRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    node_id: str = Field(min_length=1)
    kind: NodeKind


class FieldChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["set", "removed"] = "set"
    before: Any = None
    after: Any = None

    @model_validator(mode="after")
    def _removed_has_no_value(self) -> FieldChange:
        if self.op == "removed" and self.after is not None:
            raise ValueError("removed не несёт новое значение")
        return self


class Diff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unified: str | None = None
    fields: dict[str, FieldChange] | None = None

    @model_validator(mode="after")
    def _one_form(self) -> Diff:
        if (self.unified is None) == (self.fields is None):
            raise ValueError("diff задаёт ровно одну форму: unified или fields")
        if self.unified is not None and not self.unified.strip():
            raise ValueError("unified пуст")
        return self


def _nonblank_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ссылка на снимок пуста")
    return value


class EditResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1)
    change_id: str
    node_ref: NodeRef
    before: Any = None
    after: Any = None
    snapshot_id: str | None = None
    version: int = Field(ge=1)
    base_version: int = Field(ge=1)
    diff: Diff
    open_url: str = Field(min_length=1)
    reversible: bool
    skipped: list[str] = Field(default_factory=list)
    partial: bool = False

    @field_validator("change_id")
    @classmethod
    def _change_id(cls, value: str) -> str:
        if not _CHANGE_ID.fullmatch(value):
            raise ValueError("change_id выдаёт только сервер: orch-appr-<task>-<n>")
        return value

    @field_validator("action")
    @classmethod
    def _known_action(cls, value: str) -> str:
        if value not in metadata_registry():
            raise ValueError("action вне реестра")
        return value

    @field_validator("snapshot_id")
    @classmethod
    def _snapshot_id(cls, value: str | None) -> str | None:
        return _nonblank_ref(value)

    @model_validator(mode="after")
    def _before_or_snapshot(self) -> EditResult:
        if self.before is None and not self.snapshot_id:
            raise ValueError("нужен before или snapshot_id")
        if self.skipped:
            self.partial = True
        if self.partial and not self.skipped:
            raise ValueError("partial требует список skipped")
        return self


class SoftDeleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_id: str
    action: str = Field(min_length=1)
    marked: bool
    restore_action: str = Field(min_length=1)
    snapshot_id: str | None = None
    archive_ref: str | None = None
    skipped: list[str] = Field(default_factory=list)
    partial: bool = False

    @field_validator("change_id")
    @classmethod
    def _change_id(cls, value: str) -> str:
        if not _CHANGE_ID.fullmatch(value):
            raise ValueError("change_id выдаёт только сервер: orch-appr-<task>-<n>")
        return value

    @field_validator("snapshot_id", "archive_ref")
    @classmethod
    def _refs(cls, value: str | None) -> str | None:
        return _nonblank_ref(value)

    @model_validator(mode="after")
    def _snapshot_ref(self) -> SoftDeleteResult:
        if not (self.snapshot_id or self.archive_ref):
            raise ValueError("нужен snapshot_id или archive_ref")
        expected = SOFT_DELETE_RESTORE.get(self.action)
        if expected is None:
            raise ValueError("action не из мягкого удаления")
        if self.restore_action != expected:
            raise ValueError("restore_action не восстанавливает этот kind")
        if self.restore_action not in metadata_registry():
            raise ValueError("restore_action вне реестра")
        if self.skipped:
            self.partial = True
        if self.partial and not self.skipped:
            raise ValueError("partial требует список skipped")
        return self


class ActionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ActionStatusName
    result: EditResult | SoftDeleteResult | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _pair(self) -> ActionResult:
        if self.status == "failed":
            if self.result is not None or not (self.error and self.error.strip()):
                raise ValueError("failed: нужен error и пустой result")
            return self
        if self.error is not None or self.result is None:
            raise ValueError("ok и approval_required: нужен result и пустой error")
        if self.status == "ok" and isinstance(self.result, SoftDeleteResult) and self.result.marked is not True:
            raise ValueError("ok требует marked=true")
        return self


def _restore_metadata() -> dict[str, ActionMetadata]:
    rows = (
        ("frame.restore", "high", False, True, False),
        ("group.restore", "high", False, True, False),
        ("prompt.restore_master", "high", False, True, False),
    )
    return {
        name: ActionMetadata(
            name=name, risk=risk, read_only=read_only, destructive=destructive, paid=paid,
        )
        for name, risk, read_only, destructive, paid in rows
    }


_METADATA = _restore_metadata()


def metadata_registry() -> dict[str, ActionMetadata]:
    return _METADATA


EDIT_CONFLICT_HTTP = 409


def reject_stale_edit(*, base_version: int, current_version: int) -> ActionResult:
    """Устаревшая base_version не меняет данные.

    Конверт ``status=failed``, ``error=conflict``. HTTP-статус — 409.
    """

    if base_version >= current_version:
        raise ValueError("версия не устарела")
    return ActionResult(status="failed", result=None, error="conflict")
