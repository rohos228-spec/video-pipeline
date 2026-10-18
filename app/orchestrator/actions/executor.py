"""Прямой вызов одного ActionSpec. Ответ — ActionResult ws2-r2.3.

Слой планов Никиты и одобрение по plan_hash не используются.
change_id вида orch-appr-<task>-<n> адаптер не выдаёт.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ConfigDict

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.models import Frame, Project
from app.orchestrator.action_contracts.contracts import SOFT_DELETE_RESTORE
from app.orchestrator.action_contracts.contracts import ActionMetadata
from app.orchestrator.action_contracts.contracts import ActionResult
from app.orchestrator.action_contracts.contracts import ApprovalPreview
from app.orchestrator.action_contracts.contracts import ApprovalRequired
from app.orchestrator.action_contracts.contracts import SoftDeleteResult
from app.orchestrator.action_contracts.contracts import escalated_metadata as contract_escalated
from app.services.action_layer import store
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.services.frame_tombstones import mark_frame_deleted
from app.services.redact import redact_text

_CORE_CHANGE_ID = re.compile(r"^orch-appr-[0-9]+-[0-9]+$")
_SOFT_FRAME_DELETE = frozenset({"montage.delete_frame", "montage.delete_child_frame"})


class BoundResult(ActionResult):
    """Обычный ответ ActionSpec: свободный dict не входит в союз r2.3.

    Поля конверта те же. Схема контракта не меняется: это подкласс вызова,
    а не новая модель провода для карточек правки и одобрения.
    """

    model_config = ConfigDict(extra="forbid")

    result: Any = None


def _failed(message: str, warnings: list[str] | None = None) -> ActionResult:
    text = redact_text(str(message or "")).strip() or "ошибка"
    return ActionResult(status="failed", result=None, error=text[:500], warnings=list(warnings or []))


def _warnings_of(raw: dict) -> list[str]:
    found: list[str] = []
    extra = raw.get("warnings")
    if isinstance(extra, list):
        found.extend(str(item) for item in extra if str(item).strip())
    one = raw.get("warning")
    if isinstance(one, str) and one.strip():
        found.append(one.strip())
    return found


def _action_context(ctx: Any) -> ActionContext:
    if isinstance(ctx, ActionContext):
        return ctx
    if not isinstance(ctx, dict):
        raise TypeError("ctx должен быть ActionContext или dict")
    return ActionContext(
        app=ctx.get("app"),
        operator=str(ctx.get("operator") or "system"),
        plan_id=ctx.get("plan_id"),
        step_index=ctx.get("step_index"),
        idempotency_key=ctx.get("idempotency_key"),
        read_only=bool(ctx.get("read_only")),
    )


def _refusal(raw: dict) -> str | None:
    if raw.get("rejected") or raw.get("ok") is False:
        detail = raw.get("error") or raw.get("detail") or raw.get("message") or "отказ"
        return str(detail)
    return None


_NIKITA_TO_CONTRACT: dict[str, tuple[str, bool, bool, bool]] = {
    "read": ("none", True, False, False),
    "local_safe": ("low", False, False, False),
    "paid": ("medium", False, False, True),
    "destructive": ("high", False, True, False),
}

_RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3}


def map_nikita_risk(nikita_risk: str, *, name: str) -> ActionMetadata:
    """Пол яруса Никиты на шкале контракта.

    paid → medium и флаг paid: при одобрении нужна оценка больше нуля.
    destructive → high, destructive и подтверждение.
    """

    try:
        risk, read_only, destructive, paid = _NIKITA_TO_CONTRACT[nikita_risk]
    except KeyError as exc:
        raise ValueError(f"неизвестный ярус {nikita_risk}") from exc
    return ActionMetadata(
        name=name,
        risk=risk,  # type: ignore[arg-type]
        read_only=read_only,
        destructive=destructive,
        paid=paid,
    )


def _raise_to_floor(published: ActionMetadata, floor: ActionMetadata) -> ActionMetadata:
    """Совпадение или ужесточение. Ослабить json или пол яруса нельзя."""

    risk = published.risk if _RISK_RANK[published.risk] >= _RISK_RANK[floor.risk] else floor.risk
    return ActionMetadata(
        name=published.name,
        risk=risk,
        read_only=published.read_only and floor.read_only,
        destructive=published.destructive or floor.destructive,
        paid=published.paid or floor.paid,
    )


def whitelist() -> list[str]:
    """Имена живого реестра. Документ аудита эту функцию не читает."""

    ensure_builtin_actions()
    return sorted(spec.id for spec in registry.list())


class SoftDeleteMetadata(ActionMetadata):
    """Мягкое удаление для plan-sanity. Схема провода ActionMetadata не расширяется."""

    soft_delete: bool = True
    restore_action: str


def _wire_metadata(meta: ActionMetadata) -> ActionMetadata:
    """Пять полей контракта. soft_delete в карточку одобрения не входит."""

    return ActionMetadata(
        name=meta.name,
        risk=meta.risk,
        read_only=meta.read_only,
        destructive=meta.destructive,
        paid=meta.paid,
    )


def metadata_of(name: str, *, group_is_new: bool = False) -> ActionMetadata:
    """Флаги из audit_metadata.json и RISK_ESCALATIONS, не слабее пола яруса.

    ``action`` остаётся без «@». Новая группа поднимает create_node до high/destructive.
    prompt.create_master остаётся destructive. У мягкого удаления есть restore_action.
    """

    published = contract_escalated(str(name), group_is_new=group_is_new)
    spec = registry.find(published.name)
    base = published if spec is None else _raise_to_floor(
        published, map_nikita_risk(spec.risk, name=published.name)
    )
    restore = SOFT_DELETE_RESTORE.get(base.name)
    if restore is None:
        return base
    return SoftDeleteMetadata(
        name=base.name,
        risk=base.risk,
        read_only=base.read_only,
        destructive=base.destructive,
        paid=base.paid,
        soft_delete=True,
        restore_action=restore,
    )


def _approved(ctx: Any) -> bool:
    if not isinstance(ctx, dict):
        return False
    if ctx.get("approved") is True:
        return True
    return ctx.get("approval") == "granted"


def _preview_change(spec: Any, params: dict) -> str:
    effects = getattr(spec, "destructive_effects", None)
    text = ""
    if callable(effects):
        try:
            text = str(effects(params) or "")
        except Exception:
            text = ""
    cleaned = redact_text(text).strip()
    if cleaned:
        return cleaned[:500]
    title = str(getattr(spec, "title_ru", "") or spec.id).strip() or spec.id
    return f"{title}: нужно подтверждение"


async def _paid_estimate(spec: Any, params: dict, meta: ActionMetadata) -> float | None:
    """Платный ярус: положительная оценка, если она уже есть. Иначе поле пустое."""

    if not meta.paid or spec.estimator is None:
        return None
    try:
        estimate = spec.estimator(params)
        if hasattr(estimate, "__await__"):
            estimate = await estimate
    except Exception:
        return None
    high = getattr(estimate, "high", None)
    if isinstance(high, bool) or not isinstance(high, (int, float)):
        return None
    if float(high) <= 0:
        return None
    return float(high)


def _detach_cc(body: dict) -> str | None:
    """Идентификатор контроля изменений Никиты. orch-appr-* в ответ не попадает."""

    cc: str | None = None
    proposal = body.get("proposal_id")
    if isinstance(proposal, str) and proposal.strip() and _CORE_CHANGE_ID.match(proposal.strip()) is None:
        cc = proposal.strip()
    change = body.get("change_id")
    if isinstance(change, str) and change.strip():
        token = change.strip()
        if cc is None and _CORE_CHANGE_ID.match(token) is None:
            cc = token
    body.pop("change_id", None)
    return cc


async def _approval_required(spec: Any, params: dict, meta: ActionMetadata) -> ActionResult:
    preview = ApprovalPreview(
        change=_preview_change(spec, params),
        estimate=await _paid_estimate(spec, params, meta),
    )
    body = ApprovalRequired(
        action=meta.name,
        metadata=_wire_metadata(meta),
        reason="нужно подтверждение",
        preview=preview,
    )
    return ActionResult(status="approval_required", result=body, error=None, warnings=[])


def _shot_key(shot: dict) -> tuple:
    if shot.get("id") not in (None, ""):
        return ("id", shot["id"])
    for field in ("номер", "number"):
        if shot.get(field) not in (None, ""):
            return (field, shot[field])
    return ("raw", tuple(sorted((str(key), str(value)) for key, value in shot.items())))


def merge_kadry_keep_missing(existing: list | None, incoming: list | None) -> list:
    """Обновить шоты по id, иначе по номеру. Шот, которого нет во входящем списке, остаётся."""

    merged: list = []
    index: dict[tuple, int] = {}
    for shot in existing or []:
        if isinstance(shot, dict):
            item = dict(shot)
            index[_shot_key(item)] = len(merged)
            merged.append(item)
        else:
            merged.append(shot)
    for shot in incoming or []:
        if not isinstance(shot, dict):
            merged.append(shot)
            continue
        item = dict(shot)
        key = _shot_key(item)
        slot = index.get(key)
        if slot is None:
            index[key] = len(merged)
            merged.append(item)
            continue
        current = merged[slot]
        if isinstance(current, dict):
            updated = dict(current)
            updated.update(item)
            merged[slot] = updated
        else:
            merged[slot] = item
    return merged


def _marked_shot(shots: list, number: int) -> list:
    marked: list = []
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        token = shot.get("номер", shot.get("number"))
        try:
            hit = int(token) == int(number)
        except (TypeError, ValueError):
            hit = False
        if not hit:
            continue
        copy = dict(shot)
        copy["активен"] = False
        marked.append(copy)
    return marked


async def _soft_delete_frame(spec: Any, params: dict, ctx: Any) -> ActionResult:
    """Пометить кадр. Строку, сцену и файлы адаптер не удаляет. frame.restore не реализован."""

    change_id = str(ctx.get("change_id") or "") if isinstance(ctx, dict) else ""
    if _CORE_CHANGE_ID.fullmatch(change_id) is None:
        return _failed("ядро не передало change_id")
    project_id = int(params["project_id"])
    factory = store.sessions()
    async with factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            return _failed("проект не найден")
        if spec.id == "montage.delete_frame":
            frame = await session.get(Frame, int(params["frame_id"]))
            if frame is None or frame.project_id != project_id:
                return _failed("кадр не найден")
        else:
            frame = await session.scalar(
                select(Frame).where(
                    Frame.project_id == project_id,
                    Frame.number == int(params["frame_number"]),
                )
            )
            if frame is None:
                return _failed("кадр не найден")
        before = [dict(shot) if isinstance(shot, dict) else shot for shot in (frame.attrs or {}).get("кадры") or []]
        merged = merge_kadry_keep_missing(before, _marked_shot(before, frame.number))
        attrs = dict(frame.attrs or {})
        attrs["кадры"] = merged
        frame.attrs = attrs
        flag_modified(frame, "attrs")
        mark_frame_deleted(project, frame.number)
        number = int(frame.number)
        frame_id = int(frame.id)
        await session.commit()
    snapshot_id = await store.save_snapshot(
        plan_id=str(ctx.get("plan_id") or "") if isinstance(ctx, dict) else "",
        step_index=ctx.get("step_index") if isinstance(ctx, dict) else None,
        action_id=spec.id,
        project_id=project_id,
        kind="soft_delete",
        target={"frame_id": frame_id, "number": number},
        before={"кадры": before},
        after={"кадры": merged},
        restorable=True,
        note="кадр помечен, строка не удалена",
        actor=str(ctx.get("operator") or "system") if isinstance(ctx, dict) else "system",
    )
    body = SoftDeleteResult(
        change_id=change_id,
        action=spec.id,
        marked=True,
        restore_action=SOFT_DELETE_RESTORE[spec.id],
        snapshot_id=str(snapshot_id),
    )
    return ActionResult(status="ok", result=body, error=None, warnings=[])


async def execute(name: str, params: dict | None, ctx: Any) -> ActionResult:
    """Один ActionSpec. Исключения и отказы — status=failed."""

    ensure_builtin_actions()
    params = dict(params or {})
    spec = registry.find(str(name))
    if spec is None:
        return _failed(f"неизвестное действие {name}")
    problems = registry.validate_params(spec.id, params)
    if problems:
        return _failed("; ".join(problems))
    meta = metadata_of(spec.id)
    if meta.needs_approval and not _approved(ctx):
        return await _approval_required(spec, params, meta)
    if spec.id in _SOFT_FRAME_DELETE:
        return await _soft_delete_frame(spec, params, ctx)
    try:
        action_ctx = _action_context(ctx)
        if spec.risk == "read":
            action_ctx.read_only = True
        for pre in spec.preconditions:
            refusal = await pre.check(action_ctx, params)
            if refusal:
                return _failed(str(refusal))
        raw = await spec.executor(action_ctx, params)
    except Exception as exc:
        return _failed(f"{type(exc).__name__}: {exc}")
    if not isinstance(raw, dict):
        raw = {"value": raw}
    warnings = _warnings_of(raw)
    refusal = _refusal(raw)
    if refusal:
        return _failed(refusal, warnings)
    body = {key: value for key, value in raw.items() if key not in {"warnings", "warning"}}
    cc_change_id = _detach_cc(body)
    result = BoundResult(status="ok", result=body, error=None, warnings=warnings)
    if cc_change_id:
        object.__setattr__(result, "cc_change_id", cc_change_id)
    return result


ensure_builtin_actions()
