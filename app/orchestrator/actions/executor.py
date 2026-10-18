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
from app.services.frame_tombstones import forget_deleted, mark_frame_deleted
from app.services.redact import redact_text

_CORE_CHANGE_ID = re.compile(r"^orch-appr-[0-9]+-[0-9]+$")
_SOFT_FRAME_DELETE = frozenset({"montage.delete_frame", "montage.delete_child_frame"})
# Имена из SOFT_DELETE_RESTORE, у которых есть исполнитель. Остальные — только метаданные.
_IMPLEMENTED_RESTORES = frozenset({"frame.restore"})
# Помечены мягкими, но исполнитель необратим. До R2 их нет ни в списке, ни в вызове.
_HELD_UNTIL_R2 = frozenset({"group.delete", "prompt.delete_master"})


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
    """Живой реестр плюс реализованные restore из SOFT_DELETE_RESTORE.

    Документ аудита эту функцию не читает. Нереализованные restore
    (group.restore, prompt.restore_master) в список не входят.
    """

    ensure_builtin_actions()
    names = {spec.id for spec in registry.list()}
    names.difference_update(_HELD_UNTIL_R2)
    names.update(
        restore
        for restore in SOFT_DELETE_RESTORE.values()
        if restore in _IMPLEMENTED_RESTORES
    )
    return sorted(names)


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


def _group_is_new(params: dict, ctx: Any) -> bool:
    if params.get("group_is_new") is True or params.get("new_group") is True:
        return True
    return isinstance(ctx, dict) and ctx.get("group_is_new") is True


def _core_approval(name: str, ctx: Any) -> dict | None:
    """Одобрение ядра. Нет ключа, granted не True или чужой action — отказа нет."""

    if not isinstance(ctx, dict):
        return None
    raw = ctx.get("approval")
    if not isinstance(raw, dict):
        return None
    if raw.get("granted") is not True:
        return None
    if raw.get("action") != name:
        return None
    return raw


def _attach_cc(result: ActionResult, approval: dict | None) -> ActionResult:
    if not isinstance(approval, dict):
        return result
    cc = approval.get("cc_change_id")
    if not isinstance(cc, str) or not cc.strip():
        return result
    if _CORE_CHANGE_ID.fullmatch(cc.strip()) is not None:
        return result
    object.__setattr__(result, "cc_change_id", cc.strip())
    return result


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


def _unmark_deleted_shots(shots: list, number: int) -> list:
    """Снять только пометку кадра. Остальные поля и соседние шоты не трогать."""

    unmarked: list = []
    for shot in shots:
        if not isinstance(shot, dict):
            unmarked.append(shot)
            continue
        copy = dict(shot)
        token = copy.get("номер", copy.get("number"))
        try:
            hit = int(token) == int(number)
        except (TypeError, ValueError):
            hit = False
        if hit:
            copy["активен"] = True
        unmarked.append(copy)
    return unmarked


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


async def _soft_delete_frame(spec: Any, params: dict, ctx: Any, approval: dict | None) -> ActionResult:
    """Пометить кадр. Строку, сцену и файлы адаптер не удаляет. Снимает пометку frame.restore."""

    change_id = str((approval or {}).get("change_id") or "")
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
        target={"frame_id": frame_id, "number": number, "change_id": change_id},
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
    return _attach_cc(ActionResult(status="ok", result=body, error=None, warnings=[]), approval)


def _restore_lookup(params: dict) -> tuple[str | None, str | None, str | None]:
    """snapshot_id и change_id мягкого удаления. Пусто — текст отказа."""

    snapshot_id = params.get("snapshot_id")
    change_id = params.get("change_id")
    snapshot_text = str(snapshot_id).strip() if snapshot_id not in (None, "") else ""
    change_text = str(change_id).strip() if isinstance(change_id, str) else ""
    if not snapshot_text and not change_text:
        return None, None, "нет snapshot_id и change_id"
    return snapshot_text or None, change_text or None, None


async def _find_soft_delete_snapshot(project_id: int, snapshot_id: str | None, change_id: str | None) -> dict | str:
    """Снимок кадра или текст отказа. Поля скопированы до закрытия сессии."""

    from sqlalchemy import select

    factory = store.sessions()
    async with factory() as session:
        row = None
        if snapshot_id is not None:
            try:
                sid = int(snapshot_id)
            except (TypeError, ValueError):
                return "снимок не найден"
            row = await session.get(store.ActionSnapshot, sid)
        else:
            rows = list((await session.scalars(
                select(store.ActionSnapshot).where(
                    store.ActionSnapshot.project_id == project_id,
                    store.ActionSnapshot.kind == "soft_delete",
                )
            )).all())
            matches = [
                item for item in rows
                if str((item.target or {}).get("change_id") or "") == change_id
            ]
            if len(matches) != 1:
                return "снимок не найден"
            row = matches[0]
        if row is None:
            return "снимок не найден"
        target = dict(row.target or {})
        if change_id and str(target.get("change_id") or "") != change_id:
            return "снимок не найден"
        if row.kind != "soft_delete" or row.action_id not in _SOFT_FRAME_DELETE:
            return "снимок не кадр"
        if row.project_id != project_id:
            return "снимок не найден"
        if row.restored_at is not None:
            return "снимок уже восстановлен"
        if not row.restorable:
            return "снимок нельзя откатить"
        before = row.before if isinstance(row.before, dict) else {}
        return {"id": int(row.id), "target": target, "before": before}


async def _frame_restore(params: dict, ctx: Any) -> ActionResult:
    """Снять пометку кадра по snapshot_id или change_id мягкого удаления."""

    try:
        meta = metadata_of("frame.restore")
    except ValueError as exc:
        return _failed(str(exc))
    approval = _core_approval("frame.restore", ctx)
    if (meta.risk == "high" or meta.destructive) and approval is None:
        body = ApprovalRequired(
            action="frame.restore",
            metadata=_wire_metadata(meta),
            reason="нужно подтверждение",
            preview=ApprovalPreview(change="снять пометку кадра по снимку", estimate=None),
        )
        return ActionResult(status="approval_required", result=body, error=None, warnings=[])
    change_id = str((approval or {}).get("change_id") or "")
    if _CORE_CHANGE_ID.fullmatch(change_id) is None:
        return _failed("ядро не передало change_id")
    try:
        project_id = int(params["project_id"])
    except (KeyError, TypeError, ValueError):
        return _failed("проект не найден")
    snapshot_id, prior_change, lookup_error = _restore_lookup(params)
    if lookup_error:
        return _failed(lookup_error)
    found = await _find_soft_delete_snapshot(project_id, snapshot_id, prior_change)
    if isinstance(found, str):
        return _failed(found)
    number = int(found["target"]["number"])
    frame_id = int(found["target"]["frame_id"])
    factory = store.sessions()
    async with factory() as session:
        project = await session.get(Project, project_id)
        frame = await session.get(Frame, frame_id)
        if project is None or frame is None or frame.project_id != project_id:
            return _failed("кадр не найден")
        attrs = dict(frame.attrs or {})
        current = attrs.get("кадры") or []
        attrs["кадры"] = _unmark_deleted_shots(current, number)
        frame.attrs = attrs
        flag_modified(frame, "attrs")
        forget_deleted(project, number)
        await session.commit()
    await store.mark_snapshot_restored(
        found["id"],
        str(ctx.get("operator") or "system") if isinstance(ctx, dict) else "system",
        plan_id=str(ctx.get("plan_id") or "") if isinstance(ctx, dict) else "",
    )
    result = BoundResult(
        status="ok",
        result={
            "action": "frame.restore",
            "change_id": change_id,
            "snapshot_id": str(found["id"]),
            "restored": True,
        },
        error=None,
        warnings=[],
    )
    return _attach_cc(result, approval)


def _wire(result: ActionResult) -> dict[str, Any]:
    """model_dump схемы #378 плюс поля, которые читает ядро и которых в схеме нет.

    cost и cc_change_id не входят в общий контракт r2.3: extra=forbid.
    orch-appr в cc_change_id не кладётся.
    """

    payload = result.model_dump(mode="json")
    cc = getattr(result, "cc_change_id", None)
    text = cc.strip() if isinstance(cc, str) else ""
    if text and _CORE_CHANGE_ID.fullmatch(text) is None:
        payload["cc_change_id"] = text
    else:
        payload["cc_change_id"] = None
    payload["cost"] = None
    return payload


async def execute(name: str, params: dict | None, ctx: Any) -> dict[str, Any]:
    """Один ActionSpec. Ответ — dict: model_dump(mode=json) и поля ядра."""

    return _wire(await _execute(name, params, ctx))


async def _execute(name: str, params: dict | None, ctx: Any) -> ActionResult:
    """Исключения и отказы — status=failed. Наружу уходит model_dump."""

    ensure_builtin_actions()
    params = dict(params or {})
    if str(name) in _HELD_UNTIL_R2:
        return _failed("до R2 недоступно")
    if str(name) == "frame.restore":
        return await _frame_restore(params, ctx)
    spec = registry.find(str(name))
    if spec is None:
        return _failed(f"неизвестное действие {name}")
    problems = registry.validate_params(spec.id, params)
    if problems:
        return _failed("; ".join(problems))
    try:
        meta = metadata_of(spec.id, group_is_new=_group_is_new(params, ctx))
    except ValueError as exc:
        return _failed(str(exc))
    approval = _core_approval(spec.id, ctx)
    if (meta.risk == "high" or meta.destructive) and approval is None:
        return await _approval_required(spec, params, meta)
    if spec.id in _SOFT_FRAME_DELETE:
        return await _soft_delete_frame(spec, params, ctx, approval)
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
    body.pop("change_id", None)
    result = BoundResult(status="ok", result=body, error=None, warnings=warnings)
    return _attach_cc(result, approval)


ensure_builtin_actions()
