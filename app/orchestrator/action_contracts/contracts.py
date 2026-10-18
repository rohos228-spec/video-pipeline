"""Контракт результатов действий R2 для веб-чата WS4.

Только схемы. Исполнителей, записи в БД и HTTP здесь нет.
102 id и их флаги зафиксированы в ``audit_metadata.json``.
Markdown аудита при импорте не читается: его сверяет тест.
Файл ``app/orchestrator/contracts.py`` принадлежит WS3 и здесь не используется.
"""

from __future__ import annotations

import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CONTRACT_VERSION = "ws2-r2.3"

# Форма id как у WS3 #358: выдаёт только сервер, ``n`` растёт внутри задачи.
_CHANGE_ID = re.compile(r"^orch-appr-[0-9]+-[0-9]+$")
# Имя действия на проводе. Сегменты [a-z0-9_], между ними точка.
# Знака «@» нет: create_node@new_group — не id, а условие риска.
_ACTION_NAME = r"^[a-z0-9_]+(\.[a-z0-9_]+)*$"
# Детектор явных скриптов, не санитайзер. Не ловит ``<svg/onload=…>``,
# ``<img/onerror=…>`` и ``&#106;avascript:``.
# ``on*=`` только внутри тега. Текст «onload=» вне разметки детектор не трогает.
_SCRIPT_DETECTOR = re.compile(
    r"<\s*/?\s*script\b|javascript\s*:|<[^>]*\son[a-z]+\s*=",
    re.IGNORECASE,
)
_SKIPPED_REQUIRES_PARTIAL: dict[str, Any] = {
    "if": {
        "properties": {"skipped": {"minItems": 1}},
        "required": ["skipped"],
    },
    "then": {
        "properties": {"partial": {"const": True}},
        "required": ["partial"],
    },
}
_META_PATH = Path(__file__).with_name("audit_metadata.json")

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

    name: str = Field(min_length=1, pattern=_ACTION_NAME)
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
    """Один ключ полевого diff.

    ``op=set`` и ``after=null`` — значение стало null.
    ``op=removed`` — ключ исчез. Это не одно и то же.
    """

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


def _plain_action(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(_ACTION_NAME, value) is None:
        raise ValueError("action — имя из [a-z0-9_.] без @")
    return value


def _nonblank_ref(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("ссылка на снимок пуста")
    return value


class EditResult(BaseModel):
    """Правка ноды, промта, параметров или отчёта."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=_SKIPPED_REQUIRES_PARTIAL)

    action: str = Field(min_length=1, pattern=_ACTION_NAME)
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
        return _check_change_id(value)

    @field_validator("action")
    @classmethod
    def _known_action(cls, value: str) -> str:
        value = _plain_action(value)
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
        if self.version <= self.base_version:
            raise ValueError("version должна быть больше base_version")
        if self.skipped and not self.partial:
            raise ValueError("skipped требует partial=true")
        if self.partial and not self.skipped:
            raise ValueError("partial требует список skipped")
        return self


class ReportResult(BaseModel):
    """HTML отчёта для чата.

    ``_SCRIPT_DETECTOR`` только отмечает явный script, ``javascript:``
    и ``on*=``. Это не санитайзер.
    Кнопка «Править» шлёт ``edit_action`` сообщением в чат.
    DOM отчёта на месте не меняется.
    """

    model_config = ConfigDict(extra="forbid")

    report_id: int = Field(ge=1)
    html: str = Field(min_length=1)
    version: int = Field(ge=1)
    edit_action: Literal["update_report"] = Field(
        default=REPORT_EDIT_ACTION,
        json_schema_extra={"pattern": _ACTION_NAME},
    )

    @field_validator("html")
    @classmethod
    def _no_script(cls, value: str) -> str:
        if _SCRIPT_DETECTOR.search(value):
            raise ValueError("детектор нашёл script, javascript: или on*=; это не санитайзер")
        return value


class ApprovalPreview(BaseModel):
    """Что изменится и сколько это стоит.

    ``estimate is None`` — оценки нет. ``0`` — бесплатно.
    """

    model_config = ConfigDict(extra="forbid")

    change: str = Field(min_length=1)
    estimate: float | None = None


class ApprovalRequired(BaseModel):
    """Запрос на одобрение, не карточка.

    ``change_id`` и карточка живут в ядре WS3. Лишнее поле здесь запрещено.
    """

    model_config = ConfigDict(extra="forbid")

    action: str = Field(min_length=1, pattern=_ACTION_NAME)
    metadata: ActionMetadata
    reason: str = Field(min_length=1)
    preview: ApprovalPreview

    @field_validator("action")
    @classmethod
    def _action(cls, value: str) -> str:
        return _plain_action(value)

    @model_validator(mode="after")
    def _estimate(self) -> ApprovalRequired:
        """None — оценки нет, 0 — бесплатно. Платное действие не бывает бесплатным молча."""

        estimate = self.preview.estimate
        if estimate is not None and estimate < 0:
            raise ValueError("оценка не бывает отрицательной")
        if self.metadata.paid and estimate is not None and estimate <= 0:
            raise ValueError("платная оценка должна быть больше 0 или отсутствовать")
        expected = metadata_of(self.metadata.name)
        if not _metadata_not_weaker(self.metadata, expected):
            raise ValueError("metadata слабее реестра")
        if self.action != self.metadata.name:
            raise ValueError("action не совпадает с metadata.name")
        return self


class SoftDeleteResult(BaseModel):
    """Пометка вместо физического удаления. Восстановление — отдельным действием."""

    model_config = ConfigDict(extra="forbid", json_schema_extra=_SKIPPED_REQUIRES_PARTIAL)

    change_id: str
    action: str = Field(min_length=1, pattern=_ACTION_NAME)
    marked: bool
    restore_action: str = Field(min_length=1, pattern=_ACTION_NAME)
    snapshot_id: str | None = None
    archive_ref: str | None = None
    skipped: list[str] = Field(default_factory=list)
    partial: bool = False

    @field_validator("change_id")
    @classmethod
    def _change_id(cls, value: str) -> str:
        return _check_change_id(value)

    @field_validator("action")
    @classmethod
    def _action(cls, value: str) -> str:
        return _plain_action(value)

    @field_validator("snapshot_id", "archive_ref")
    @classmethod
    def _refs(cls, value: str | None) -> str | None:
        return _nonblank_ref(value)

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
        if self.skipped and not self.partial:
            raise ValueError("skipped требует partial=true")
        if self.partial and not self.skipped:
            raise ValueError("partial требует список skipped")
        return self


class ActionResult(BaseModel):
    """Общий конверт ответа действия."""

    model_config = ConfigDict(extra="forbid")

    status: ActionStatusName
    result: EditResult | ReportResult | ApprovalRequired | SoftDeleteResult | None = None
    error: str | None = None
    warnings: list[str] = Field(default_factory=list)
    cc_change_id: str | None = None

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


def _r2_metadata() -> dict[str, ActionMetadata]:
    """Имена из плана §6 и ``revert_node_to_git`` из аудита. Не из 102."""

    rows: tuple[tuple[str, RiskName, bool, bool, bool], ...] = (
        ("read_pipeline_code", "none", True, False, False),
        ("rag_search", "none", True, False, False),
        ("run_validators", "none", True, False, False),
        ("stop_node", "medium", False, False, False),
        ("resume_node", "high", False, True, True),
        ("create_node", "medium", False, False, False),
        ("edit_node_prompt", "medium", False, True, False),
        ("edit_node_params", "medium", False, True, False),
        ("create_report", "low", False, False, False),
        ("update_report", "low", False, True, False),
        ("export_nodes_snapshot", "low", True, False, False),
        ("revert_node_to_git", "medium", False, True, False),
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


_RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def _metadata_not_weaker(offered: ActionMetadata, base: ActionMetadata) -> bool:
    """Разрешено совпадение и ужесточение. Ослабить риск или флаги нельзя."""

    if offered.name != base.name:
        return False
    if _RISK_RANK[offered.risk] < _RISK_RANK[base.risk]:
        return False
    if offered.read_only and not base.read_only:
        return False
    if base.destructive and not offered.destructive:
        return False
    if base.paid and not offered.paid:
        return False
    return True


def _load_registry() -> tuple[tuple[str, ...], dict[str, ActionMetadata]]:
    payload = json.loads(_META_PATH.read_text(encoding="utf-8"))
    parsed = payload["actions"]
    if len(parsed) != 102 or len({row["name"] for row in parsed}) != 102:
        raise RuntimeError("audit_metadata.json должен дать 102 уникальных действия")
    ids = tuple(row["name"] for row in parsed)
    meta: dict[str, ActionMetadata] = {}
    for row in parsed:
        meta[row["name"]] = ActionMetadata(
            name=row["name"],
            risk=row["risk"],
            read_only=row["read_only"],
            destructive=row["destructive"],
            paid=row["paid"],
        )
    extra = _r2_metadata()
    overlap = set(extra) & set(meta)
    if overlap:
        raise RuntimeError("R2 пересекается с живым реестром: " + ", ".join(sorted(overlap)))
    meta.update(extra)
    return ids, meta


AUDIT_ACTION_IDS, _METADATA = _load_registry()


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


class RiskEscalation(NamedTuple):
    """Повышение риска по условию. Это не id действия и не поле ``action``."""

    action: str
    when: str
    risk: RiskName
    read_only: bool
    destructive: bool
    paid: bool


# Новая группа у create_node. Ядро исполняет action=create_node, не имя с «@».
RISK_ESCALATIONS: tuple[RiskEscalation, ...] = (
    RiskEscalation("create_node", "group_is_new", "high", False, True, False),
)


def escalated_metadata(action: str, *, group_is_new: bool = False) -> ActionMetadata:
    """Базовые флаги или ужесточение из ``RISK_ESCALATIONS``."""

    action = _plain_action(action)
    base = metadata_of(action)
    if not group_is_new:
        return base
    for rule in RISK_ESCALATIONS:
        if rule.action == action and rule.when == "group_is_new":
            return ActionMetadata(
                name=action,
                risk=rule.risk,
                read_only=rule.read_only,
                destructive=rule.destructive,
                paid=rule.paid,
            )
    return base


_WIRE_MODELS: dict[str, type[BaseModel]] = {
    "ActionMetadata": ActionMetadata,
    "NodeRef": NodeRef,
    "FieldChange": FieldChange,
    "Diff": Diff,
    "EditResult": EditResult,
    "ReportResult": ReportResult,
    "ApprovalPreview": ApprovalPreview,
    "ApprovalRequired": ApprovalRequired,
    "SoftDeleteResult": SoftDeleteResult,
    "ActionResult": ActionResult,
}


EDIT_CONFLICT_HTTP = 409


def reject_stale_edit(*, base_version: int, current_version: int) -> ActionResult:
    """База не равна текущей версии: данные не меняются.

    И прошлое, и будущее — ``status=failed``, ``error=conflict``, HTTP 409.
    Совпадение с текущей версией конфликтом не является.
    """

    if base_version == current_version:
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
