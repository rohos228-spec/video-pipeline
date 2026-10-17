"""Контракт результатов действий R2 для веб-чата WS4.

Только схемы. Исполнителей, записи в БД и HTTP здесь нет.
Имена действий взяты из аудита (102 id) и из плана §6 / ``revert_node_to_git``.
Файл ``app/orchestrator/contracts.py`` принадлежит WS3 и здесь не используется.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "ws2-r2.0"

# Форма id как у WS3 #358: выдаёт только сервер, ``n`` растёт внутри задачи.
_CHANGE_ID = re.compile(r"^orch-appr-[0-9]+-[0-9]+$")
_SCRIPT = re.compile(
    r"<\s*/?\s*script\b|javascript\s*:|\son[a-z]+\s*=",
    re.IGNORECASE,
)
_AUDIT_PATH = Path(__file__).resolve().parents[3] / "docs" / "fable" / "actions_audit.md"

# Правка отчёта из чата. Имя из плана §6.3, не новое.
REPORT_EDIT_ACTION = "update_report"

# Мягкое удаление восстанавливает только свой kind. history.rollback
# умеет другие снимки и эти четыре действия не возвращает.
SOFT_DELETE_RESTORE: dict[str, str] = {
    "montage.delete_frame": "frame.restore",
    "montage.delete_child_frame": "frame.restore",
    "group.delete": "group.restore",
    "prompt.delete_master": "prompt.restore_master",
}

RiskName = Literal["none", "low", "medium", "high"]
ActionStatusName = Literal["ok", "approval_required", "failed"]


class NodeKind(StrEnum):
    node = "node"
    prompt = "prompt"
    params = "params"
    report = "report"


class ActionMetadata(BaseModel):
    """Метаданные действия.

    ``risk`` — шкала плана: none / low / medium / high.
    ``read_only``, ``destructive``, ``paid`` — флаги яруса реестра.
    Имя вне реестра сюда не кладётся готовым: его собирает ``metadata_of``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    risk: RiskName
    read_only: bool
    destructive: bool
    paid: bool

    @property
    def needs_approval(self) -> bool:
        """Платное или деструктивное действие сначала ждёт подтверждения.

        Имя вне реестра помечается обоими флагами, поэтому тоже ждёт.
        """

        return self.paid or self.destructive


class NodeRef(BaseModel):
    """Ссылка на то, что изменилось.

    ``node_id`` для kind node/prompt/params — ``node_key`` плана.
    Для kind report — строковый id отчёта.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    node_id: str = Field(min_length=1)
    kind: NodeKind


class FieldChange(BaseModel):
    """Один ключ полевого diff."""

    model_config = ConfigDict(extra="forbid")

    before: Any = None
    after: Any = None


class Diff(BaseModel):
    """Либо unified-текст, либо набор полей. Оба сразу нельзя."""

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


def _check_change_id(value: str) -> str:
    if not isinstance(value, str) or not _CHANGE_ID.fullmatch(value):
        raise ValueError("change_id выдаёт только сервер: orch-appr-<task>-<n>")
    return value


class EditResult(BaseModel):
    """Правка ноды, промта, параметров или отчёта."""

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

    @field_validator("change_id")
    @classmethod
    def _change_id(cls, value: str) -> str:
        return _check_change_id(value)

    @field_validator("action")
    @classmethod
    def _known_action(cls, value: str) -> str:
        if value not in metadata_registry():
            raise ValueError("action вне реестра")
        return value

    @model_validator(mode="after")
    def _before_or_snapshot(self) -> EditResult:
        if self.before is None and not self.snapshot_id:
            raise ValueError("нужен before или snapshot_id")
        return self


class ReportResult(BaseModel):
    """Уже санитизированный HTML отчёта для чата.

    Кнопка «Править» шлёт ``edit_action`` сообщением в чат.
    DOM отчёта на месте не меняется.
    """

    model_config = ConfigDict(extra="forbid")

    report_id: int = Field(ge=1)
    html: str = Field(min_length=1)
    version: int = Field(ge=1)
    edit_action: Literal["update_report"] = REPORT_EDIT_ACTION

    @field_validator("html")
    @classmethod
    def _no_script(cls, value: str) -> str:
        if _SCRIPT.search(value):
            raise ValueError("html содержит скрипт и не прошёл санитизацию")
        return value


class ApprovalRequired(BaseModel):
    """Первый ответ на платное или деструктивное действие."""

    model_config = ConfigDict(extra="forbid")

    change_id: str
    metadata: ActionMetadata
    summary: str = Field(min_length=1)
    estimate: float = Field(ge=0)

    @field_validator("change_id")
    @classmethod
    def _change_id(cls, value: str) -> str:
        return _check_change_id(value)


class SoftDeleteResult(BaseModel):
    """Пометка вместо физического удаления. Восстановление — отдельным действием."""

    model_config = ConfigDict(extra="forbid")

    change_id: str
    action: str = Field(min_length=1)
    marked: bool
    restore_action: str = Field(min_length=1)
    snapshot_id: str | None = None
    archive_ref: str | None = None

    @field_validator("change_id")
    @classmethod
    def _change_id(cls, value: str) -> str:
        return _check_change_id(value)

    @model_validator(mode="after")
    def _snapshot_ref(self) -> SoftDeleteResult:
        """Снимок обязателен, restore_action умеет именно этот kind."""

        if not (self.snapshot_id or self.archive_ref):
            raise ValueError("нужен snapshot_id или archive_ref")
        expected = SOFT_DELETE_RESTORE.get(self.action)
        if expected is None:
            raise ValueError("action не из мягкого удаления")
        if self.restore_action != expected:
            raise ValueError("restore_action не восстанавливает этот kind")
        if self.restore_action not in metadata_registry():
            raise ValueError("restore_action вне реестра")
        return self


class ActionResult(BaseModel):
    """Общий конверт ответа действия."""

    model_config = ConfigDict(extra="forbid")

    status: ActionStatusName
    result: EditResult | ReportResult | ApprovalRequired | SoftDeleteResult | None = None
    error: str | None = None

    @model_validator(mode="after")
    def _pair(self) -> ActionResult:
        if self.status == "failed":
            if self.result is not None or not (self.error and self.error.strip()):
                raise ValueError("failed: нужен error и пустой result")
            return self
        if self.error is not None or self.result is None:
            raise ValueError("ok и approval_required: нужен result и пустой error")
        if self.status == "approval_required" and not isinstance(self.result, ApprovalRequired):
            raise ValueError("approval_required: result это ApprovalRequired")
        if self.status == "ok" and isinstance(self.result, ApprovalRequired):
            raise ValueError("ok не несёт ApprovalRequired")
        if (
            self.status == "ok"
            and isinstance(self.result, SoftDeleteResult)
            and self.result.marked is not True
        ):
            raise ValueError("ok требует marked=true")
        return self


def _audit_rows(text: str) -> list[tuple[str, str, str]]:
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


def _flags(name: str, tier: str) -> tuple[bool, bool, bool]:
    """read_only, destructive, paid.

    Ячейка ``paid+paid`` у ``montage.suggest_scene_variants`` — опечатка
    таблицы #344. В коде ярус ``paid``, контракт хранит его так и не
    переписывает документ аудита.
    ``prompt.create_master`` пишет общий файл промта: ярус в таблице
    ``local_safe``, для контракта флаг ``destructive``.
    """

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


def _r2_metadata() -> dict[str, ActionMetadata]:
    """Имена из плана §6 и ``revert_node_to_git`` из аудита. Не из 102."""

    rows: tuple[tuple[str, RiskName, bool, bool, bool], ...] = (
        ("read_pipeline_code", "none", True, False, False),
        ("rag_search", "none", True, False, False),
        ("run_validators", "none", True, False, False),
        ("stop_node", "medium", False, False, False),
        ("resume_node", "medium", False, False, True),
        ("create_node", "medium", False, False, False),
        ("edit_node_prompt", "medium", False, False, False),
        ("edit_node_params", "medium", False, False, False),
        ("create_report", "low", False, False, False),
        ("update_report", "low", False, False, False),
        ("export_nodes_snapshot", "low", True, False, False),
        ("revert_node_to_git", "medium", False, False, False),
        ("frame.restore", "high", False, True, False),
        ("group.restore", "high", False, True, False),
        ("prompt.restore_master", "high", False, True, False),
    )
    return {
        name: ActionMetadata(
            name=name,
            risk=risk,
            read_only=read_only,
            destructive=destructive,
            paid=paid,
        )
        for name, risk, read_only, destructive, paid in rows
    }


def _load_registry() -> tuple[tuple[str, ...], dict[str, ActionMetadata]]:
    text = _AUDIT_PATH.read_text(encoding="utf-8")
    parsed = _audit_rows(text)
    if len(parsed) != 102 or len({name for name, _, _ in parsed}) != 102:
        raise RuntimeError("таблица аудита должна дать 102 уникальных действия")
    ids = tuple(name for name, _, _ in parsed)
    meta: dict[str, ActionMetadata] = {}
    for name, risk, tier in parsed:
        read_only, destructive, paid = _flags(name, tier)
        meta[name] = ActionMetadata(
            name=name,
            risk=risk,  # type: ignore[arg-type]
            read_only=read_only,
            destructive=destructive,
            paid=paid,
        )
    extra = _r2_metadata()
    overlap = set(extra) & set(meta)
    if overlap:
        raise RuntimeError("R2 пересекается с живым реестром: " + ", ".join(sorted(overlap)))
    meta.update(extra)
    return ids, meta


ACTION_IDS, _METADATA = _load_registry()


def metadata_registry() -> dict[str, ActionMetadata]:
    """Имена, для которых метаданные известны. Не список исполнителей."""

    return _METADATA


def metadata_of(name: str) -> ActionMetadata:
    """Метаданные реестра.

    Имени нет ни в 102, ни в R2: ``paid`` и ``destructive`` истинны,
    ``risk`` равен ``high``, нужно подтверждение.
    """

    found = _METADATA.get(name)
    if found is not None:
        return found
    return ActionMetadata(
        name=name,
        risk="high",
        read_only=False,
        destructive=True,
        paid=True,
    )


_WIRE_MODELS: dict[str, type[BaseModel]] = {
    "ActionMetadata": ActionMetadata,
    "NodeRef": NodeRef,
    "FieldChange": FieldChange,
    "Diff": Diff,
    "EditResult": EditResult,
    "ReportResult": ReportResult,
    "ApprovalRequired": ApprovalRequired,
    "SoftDeleteResult": SoftDeleteResult,
    "ActionResult": ActionResult,
}


EDIT_CONFLICT_HTTP = 409


def reject_stale_edit(*, base_version: int, current_version: int) -> ActionResult:
    """Устаревшая base_version не меняет данные.

    Конверт ``status=failed``, ``error=conflict``. HTTP-статус — 409.
    """

    if base_version >= current_version:
        raise ValueError("версия не устарела")
    return ActionResult(status="failed", result=None, error="conflict")


def export_action_result_schema() -> dict[str, Any]:
    """JSON Schema конверта для моков WS4. Версия — ``CONTRACT_VERSION``."""

    return {
        "version": CONTRACT_VERSION,
        "models": {name: model.model_json_schema() for name, model in _WIRE_MODELS.items()},
    }


def schema_canonical_bytes() -> bytes:
    """Канонический JSON экспорта: по нему считается sha256 версии."""

    blob = json.dumps(
        export_action_result_schema(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return (blob + "\n").encode("utf-8")
