"""Восстановление мягкого удаления: frame.restore, group.restore, prompt.restore_master.

Имена и конверт — из SOFT_DELETE_RESTORE / ActionResult (ws2-r2.2).
Физически кадры и сцены здесь не удаляются: удаление ставит флаги удалён/активен
в attrs, восстановление снимает их с той же строки; вставка из снимка — только
если строки правда нет.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from app.orchestrator.actions.contracts import (
    SOFT_DELETE_RESTORE,
    ActionResult,
    Diff,
    EditResult,
    FieldChange,
    NodeKind,
    NodeRef,
)
from app.orchestrator.node_store.kadry import merge_kadry_keep_missing
from app.services.action_layer import store
from app.services.action_layer.context import ActionCallError, ActionContext

_REF_PROPS = {
    "snapshot_id": {"type": "string", "minLength": 1, "maxLength": 80},
    "archive_ref": {"type": "string", "minLength": 1, "maxLength": 240},
}


def restore_params_schema(*, project: bool) -> dict:
    props = dict(_REF_PROPS)
    required: list[str] = []
    if project:
        props = {
            "project_id": {"type": "integer", "minimum": 1, "description": "ID проекта"},
            **props,
        }
        required.append("project_id")
    return {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,
    }


async def precondition_restore_ref(_ctx, params: dict) -> str | None:
    if _ref_text(params.get("snapshot_id")) or _ref_text(params.get("archive_ref")):
        return None
    return "нужен snapshot_id или archive_ref"


def _ref_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


async def remember_soft_delete(
    ctx: ActionContext,
    *,
    action_id: str,
    project_id: int | None,
    kind: str,
    target: dict,
    before: Any,
) -> str:
    """Снимок прежнего значения. id строкой, как snapshot_id в SoftDeleteResult."""

    sid = await store.save_snapshot(
        plan_id=ctx.plan_id or "",
        step_index=ctx.step_index,
        action_id=action_id,
        project_id=project_id,
        kind=kind,
        target=target,
        before=before,
        after={"marked": True},
        restorable=True,
        note="мягкое удаление: восстановление отдельным действием",
        actor=ctx.operator or "",
    )
    return str(sid)


async def _load_before(params: dict) -> tuple[str, Any]:
    sid = _ref_text(params.get("snapshot_id"))
    archive = _ref_text(params.get("archive_ref"))
    if sid:
        row = await store.get_snapshot(_parse_snapshot_id(sid))
        if row is None:
            raise ActionCallError(404, f"снимка {sid} нет")
        return sid, copy.deepcopy(row.before)
    if archive:
        return archive, _read_archive(archive)
    raise ActionCallError(422, "нужен snapshot_id или archive_ref")


def _parse_snapshot_id(value: str) -> int:
    token = value[5:] if value.startswith("snap-") and value[5:].isdigit() else value
    if not token.isdigit():
        raise ActionCallError(404, f"снимка {value} нет")
    return int(token)


def _read_archive(ref: str) -> Any:
    from app.settings import settings

    raw = Path(ref)
    if raw.is_absolute():
        raise ActionCallError(422, "archive_ref должен быть путём внутри data/old")
    root = Path(settings.data_dir).resolve()
    path = (root / raw).resolve()
    if path != root and root not in path.parents:
        raise ActionCallError(422, "archive_ref вне каталога данных")
    if not path.is_file():
        raise ActionCallError(404, f"архива {ref} нет")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ActionCallError(422, "архив не JSON") from exc


def _change_n(snapshot_id: str) -> int:
    token = snapshot_id[5:] if snapshot_id.startswith("snap-") else snapshot_id
    return int(token) if token.isdigit() else 1


def _edit_result(
    *,
    action: str,
    project_id: int,
    node_id: str,
    kind: NodeKind,
    snapshot_id: str,
    before: Any,
    after: Any,
    open_url: str,
    field: str,
) -> dict:
    payload = EditResult(
        action=action,
        change_id=f"orch-appr-{int(project_id)}-{_change_n(snapshot_id)}",
        node_ref=NodeRef(project_id=int(project_id), node_id=node_id, kind=kind),
        before=before,
        after=after,
        snapshot_id=snapshot_id,
        version=2,
        base_version=1,
        diff=Diff(fields={field: FieldChange(op="set", before=None, after=after)}),
        open_url=open_url,
        reversible=True,
    )
    return ActionResult(status="ok", result=payload, error=None).model_dump(mode="json")


async def capture_frame_by_number(project_id: int, number: int) -> dict | None:
    """Кадр по номеру, затем тот же снимок, что у capture_frame."""
    from sqlalchemy import select

    from app.models import Frame
    from app.services.action_layer.store import sessions

    async with sessions()() as session:
        frame_id = await session.scalar(
            select(Frame.id).where(
                Frame.project_id == int(project_id),
                Frame.number == int(number),
            )
        )
    if frame_id is None:
        return None
    return await capture_frame(int(project_id), int(frame_id))


async def capture_frame(project_id: int, frame_id: int) -> dict | None:
    from sqlalchemy import select

    from app.models import Frame
    from app.services.action_layer.store import sessions

    async with sessions()() as session:
        frame = await session.scalar(
            select(Frame).where(Frame.id == int(frame_id), Frame.project_id == int(project_id))
        )
        if frame is None:
            return None
        return {
            "project_id": int(frame.project_id),
            "frame_id": int(frame.id),
            "number": int(frame.number),
            "voiceover_text": frame.voiceover_text or "",
            "image_prompt": frame.image_prompt,
            "animation_prompt": frame.animation_prompt,
            "meaning": frame.meaning,
            "sort_key": frame.sort_key,
            "uuid": frame.uuid,
            "attrs": copy.deepcopy(frame.attrs or {}),
        }


async def frame_restore(ctx: ActionContext, params: dict) -> dict:
    snapshot_id, before = await _load_before(params)
    if not isinstance(before, dict) or "number" not in before:
        raise ActionCallError(422, "снимок кадра пуст")
    project_id = int(params.get("project_id") or before.get("project_id") or 0)
    if project_id < 1:
        raise ActionCallError(422, "в снимке кадра нет project_id")
    restored = await _restore_frame_row(project_id, before)
    return _edit_result(
        action="frame.restore",
        project_id=project_id,
        node_id=f"frame-{restored['number']}",
        kind=NodeKind.node,
        snapshot_id=snapshot_id,
        before={"marked": True, "number": restored["number"]},
        after={"voiceover_text": restored["voiceover_text"], "кадры": restored["кадры"]},
        open_url=f"/api/projects/{project_id}",
        field="кадры",
    )


_SOFT_DELETE_MARKS = ("удалён", "активен", "карантин", "пометка")


def soft_deleted_attrs(attrs: Any) -> dict:
    """Пометка мягкого удаления: строка, тексты, промты и кадры[] остаются на месте."""
    marked = copy.deepcopy(attrs) if isinstance(attrs, dict) else {}
    marked["удалён"] = True
    marked["активен"] = False
    return marked


def _soft_delete_marked(attrs: Any) -> bool:
    """Обратная к soft_deleted_attrs: стоит ли на строке пометка мягкого удаления.

    Карантин пометкой удаления не считается: restore не должен молча снимать карантин.
    """
    if not isinstance(attrs, dict):
        return False
    return bool(attrs.get("удалён")) or attrs.get("активен") is False


def restored_frame_attrs(current: Any, before: dict) -> dict:
    """attrs после восстановления: пометки сняты, кадры[] текущей строки слиты со снимком.

    Есть строка — её кадры[] (в т.ч. добавленные после удаления и скрытые пометки)
    сливаются со снимком. Строки нет — берутся attrs из снимка.
    """
    snap_attrs = before.get("attrs") if isinstance(before.get("attrs"), dict) else {}
    incoming = snap_attrs.get("кадры") or []
    if isinstance(current, dict):
        attrs = copy.deepcopy(current)
        attrs["кадры"] = merge_kadry_keep_missing(current.get("кадры") or [], incoming)
    else:
        attrs = copy.deepcopy(snap_attrs)
        attrs["кадры"] = copy.deepcopy(incoming) if isinstance(incoming, list) else []
    for key in _SOFT_DELETE_MARKS:
        attrs.pop(key, None)
    return attrs


async def mark_frame_soft_deleted(project_id: int, number: int) -> bool:
    """Мягкое удаление кадра без DELETE: ставит флаги, строку не трогает. False — кадра нет."""
    from sqlalchemy import select

    from app.models import Frame, Project
    from app.services.action_layer.store import sessions
    from app.services.frame_tombstones import remember_deleted

    async with sessions()() as session:
        frame = await session.scalar(
            select(Frame).where(Frame.project_id == int(project_id), Frame.number == int(number))
        )
        if frame is None:
            return False
        frame.attrs = soft_deleted_attrs(frame.attrs)
        # R0b: номер уходит в tombstone, чтобы импорт и регенерация не
        # воскресили удалённый пользователем кадр; restore снимает его forget_deleted.
        project = await session.get(Project, int(project_id))
        if project is not None:
            remember_deleted(project, int(number))
        await session.commit()
        return True


async def _restore_frame_row(project_id: int, before: dict) -> dict:
    from sqlalchemy import select

    from app.models import Frame, FrameStatus, Project
    from app.services.action_layer.store import sessions
    from app.services.frame_tombstones import forget_deleted

    number = int(before["number"])
    async with sessions()() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise ActionCallError(404, f"проекта {project_id} нет")
        frame = await session.scalar(
            select(Frame).where(Frame.project_id == project_id, Frame.number == number)
        )
        if frame is not None:
            if not _soft_delete_marked(frame.attrs):
                # Живую строку снимком не перезаписываем: merge разрешён
                # только со своей мягко удалённой строкой.
                raise ActionCallError(409, f"conflict: номер {number} занят живым кадром")
            # Строка мягко удалена: снимаем пометки, тексты и промты не трогаем.
            attrs = restored_frame_attrs(frame.attrs, before)
            frame.attrs = attrs
        else:
            # Запасной путь: строки правда нет — вставляем из снимка.
            attrs = restored_frame_attrs(None, before)
            frame = Frame(
                project_id=project_id,
                number=number,
                voiceover_text=str(before.get("voiceover_text") or ""),
                image_prompt=before.get("image_prompt"),
                animation_prompt=before.get("animation_prompt"),
                meaning=before.get("meaning"),
                sort_key=before.get("sort_key"),
                uuid=before.get("uuid"),
                status=FrameStatus.planned,
                attrs=attrs,
            )
            session.add(frame)
        forget_deleted(project, number)
        voiceover = str(frame.voiceover_text or "")
        await session.commit()
        return {
            "number": number,
            "voiceover_text": voiceover,
            "кадры": copy.deepcopy(attrs["кадры"]),
        }


async def _snapshot_live(
    ctx: ActionContext,
    *,
    action_id: str,
    project_id: int,
    kind: str,
    target: dict,
    before: Any,
) -> str:
    """Снимок того, что сейчас на диске. Ошибка записи — отказ, без POST/PUT."""
    sid = await store.save_snapshot(
        plan_id=ctx.plan_id or "",
        step_index=ctx.step_index,
        action_id=action_id,
        project_id=project_id,
        kind=kind,
        target=target,
        before=before,
        after={"restore_pending": True},
        restorable=True,
        note="снимок текущего значения перед восстановлением",
        actor=ctx.operator or "",
    )
    return str(sid)


async def _group_live(ctx: ActionContext, group_id: str) -> dict:
    try:
        detail = await ctx.call("GET", f"/node-groups/{group_id}")
    except ActionCallError as exc:
        if exc.status == 404:
            return {"group_id": group_id, "absent": True}
        raise
    if isinstance(detail, dict):
        return detail
    return {"group_id": group_id, "absent": True}


async def group_restore(ctx: ActionContext, params: dict) -> dict:
    snapshot_id, before = await _load_before(params)
    if not isinstance(before, dict) or not before.get("group_id"):
        raise ActionCallError(422, "снимок группы пуст")
    gid = str(before["group_id"])
    await _snapshot_live(
        ctx,
        action_id="group.restore",
        project_id=int(params.get("project_id") or 1),
        kind="group",
        target={"group_id": gid},
        before=await _group_live(ctx, gid),
    )
    await ctx.call("POST", "/node-groups", json=before)
    return _edit_result(
        action="group.restore",
        project_id=int(params.get("project_id") or 1),
        node_id=gid,
        kind=NodeKind.node,
        snapshot_id=snapshot_id,
        before={"marked": True, "group_id": gid},
        after={"group_id": gid, "title": before.get("title")},
        open_url=f"/api/node-groups/{gid}",
        field="title",
    )


async def prompt_restore_master(ctx: ActionContext, params: dict) -> dict:
    from app.services.action_layer.builtin_wave3 import _prompt_path, _read_prompt

    snapshot_id, before = await _load_before(params)
    if not isinstance(before, dict) or "content" not in before:
        raise ActionCallError(422, "снимок мастер-промта пуст")
    step = str(before.get("step_code") or "")
    name = str(before.get("name") or "")
    current = await _read_prompt(ctx, {"step_code": step, "name": name})
    if current is None:
        live = {"step_code": step, "name": name, "absent": True}
    else:
        live = {"step_code": step, "name": name, "content": str(current.get("content") or "")}
    await _snapshot_live(
        ctx,
        action_id="prompt.restore_master",
        project_id=int(params.get("project_id") or 1),
        kind="master_prompt",
        target={"step_code": step, "name": name},
        before=live,
    )
    await ctx.call("PUT", _prompt_path({"step_code": step, "name": name}), json={"content": before["content"]})
    return _edit_result(
        action="prompt.restore_master",
        project_id=int(params.get("project_id") or 1),
        node_id=f"{step}/{name}",
        kind=NodeKind.prompt,
        snapshot_id=snapshot_id,
        before={"marked": True, "step_code": step, "name": name},
        after={"content": before["content"]},
        open_url=f"/api/prompt-files/{step}/{name}/content",
        field="content",
    )


def restore_action_for(delete_action: str) -> str:
    found = SOFT_DELETE_RESTORE.get(delete_action)
    if found is None:
        raise KeyError(delete_action)
    return found
