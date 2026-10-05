"""Первый срез каталога: безопасные действия монтажной панели.

Только ``read`` и ``local_safe``. Платные (apply, montage, scene-improve) и
разрушающие (удаление кадров/медиа, merge/split) здесь намеренно не заводятся:
их добавляет полный каталог через тот же ``registry.register``.

Все исполнители зовут существующие эндпоинты монтажа через ``ctx.call``.
"""
from __future__ import annotations

from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import (
    RISK_LOCAL_SAFE, RISK_READ, ActionRegistry, ActionSpec, Precondition)

_PID = {"type": "integer", "minimum": 1, "description": "ID проекта"}
_MAX_FRAMES_IN_RESULT = 300

# Типы coverage-операций, которые только пишут текстовое поле в БД.
# Исключены: coverage_delete (удаляет кадр), coverage_scene_action (перезаписывает
# цепочку шотов), coverage_anchors / coverage_vo_span (структурные поля).
_COVERAGE_TYPES = ("coverage_kind", "coverage_plan", "coverage_angle", "coverage_move",
                   "coverage_stitch", "coverage_light", "coverage_action")

_QUEUE_OP_TYPES = ("image_regen", "image_regen_prompt", "image_regen_correction",
                   "image_ai_change", "video_regen", "video_regen_prompt", "video_ai_change")


def _base(pid: int) -> str:
    return f"/projects/{pid}/montage-board"


async def _project_exists(ctx: ActionContext, params: dict) -> str | None:
    await ctx.call("GET", f"/projects/{params['project_id']}")
    return None


async def _no_apply_running(ctx: ActionContext, params: dict) -> str | None:
    data = await ctx.call("GET", f"{_base(params['project_id'])}/apply-status")
    job = (data or {}).get("job") or {}
    if job.get("status") == "running":
        return "идёт применение правок (apply)"
    return None


_PRE_PROJECT = Precondition("project_exists", "Проект существует", _project_exists)
_PRE_NO_APPLY = Precondition("no_apply_running", "Сейчас не идёт применение правок",
                             _no_apply_running)


# --- read -------------------------------------------------------------------

async def _get_board(ctx: ActionContext, params: dict) -> dict:
    board = await ctx.call("GET", _base(params["project_id"]))
    frames = board.get("frames") or []
    meta = board.get("meta") or {}
    return {
        "frame_count": board.get("frame_count", len(frames)),
        "frames": [{"frame_id": f.get("frame_id"), "number": f.get("number"),
                    "voiceover_text": (f.get("voiceover_text") or "")[:300],
                    "shot_kind": f.get("shot_kind"), "has_shot2": f.get("has_shot2")}
                   for f in frames[:_MAX_FRAMES_IN_RESULT]],
        "frames_truncated": len(frames) > _MAX_FRAMES_IN_RESULT,
        "pending_ops": list(meta.get("pending_ops") or []),
        "stale_videos": list(meta.get("stale_videos") or []),
    }


async def _status(ctx: ActionContext, params: dict, suffix: str) -> dict:
    data = await ctx.call("GET", f"{_base(params['project_id'])}/{suffix}")
    return {"job": (data or {}).get("job") or {}}


async def _apply_status(ctx, params):
    return await _status(ctx, params, "apply-status")


async def _montage_status(ctx, params):
    return await _status(ctx, params, "montage-status")


async def _recover_status(ctx, params):
    return await _status(ctx, params, "recover-outsee-status")


# --- local_safe -------------------------------------------------------------

async def _set_voiceover(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call(
        "PATCH", f"{_base(params['project_id'])}/frames/{params['frame_id']}/voiceover",
        json={"text": params["text"]})
    return {"frame_id": data.get("id"), "number": data.get("number"),
            "voiceover_text": data.get("voiceover_text")}


async def _set_coverage(ctx: ActionContext, params: dict) -> dict:
    op_type = params["type"]
    body: dict = {"type": op_type, "frame_number": params["frame_number"],
                  op_type.removeprefix("coverage_"): params["value"]}
    if "parent_number" in params:
        body["parent_number"] = params["parent_number"]
    data = await ctx.call("POST", f"{_base(params['project_id'])}/coverage", json=body)
    return {"ok": bool(data.get("ok")), "frame_number": params["frame_number"],
            "type": op_type, "shot_kind": data.get("shot_kind"),
            "shot_parent_number": (data.get("frame") or {}).get("shot_parent_number")}


def _op_key(op: dict) -> tuple:
    return (op.get("type"), op.get("frame_number"), 2 if op.get("shot") == 2 else 1,
            op.get("slot") or "")


async def _queue_ops(ctx: ActionContext, params: dict) -> dict:
    """Дописывает правки в очередь (замена одноимённой). Apply не запускается."""
    base = _base(params["project_id"])
    board = await ctx.call("GET", base)
    existing = list(((board.get("meta") or {}).get("pending_ops")) or [])
    new_ops = list(params["ops"])
    new_keys = {_op_key(o) for o in new_ops}
    merged = [o for o in existing if _op_key(o) not in new_keys] + new_ops
    data = await ctx.call("POST", f"{base}/queue", json={"pending_ops": merged})
    if not data.get("ok"):
        raise RuntimeError(data.get("message") or data.get("rejected") or "queue rejected")
    return {"queued": len(new_ops), "total_in_queue": len(data.get("pending_ops") or []),
            "applied": False}


async def _swap_shots(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("POST", f"{_base(params['project_id'])}/swap-shots",
                          params={"frame_number": params["frame_number"],
                                  "kind": params["kind"]})
    return {k: data.get(k) for k in ("frame_number", "kind", "images_swapped",
                                     "videos_swapped", "prompts_swapped")}


def _specs() -> list[ActionSpec]:
    status_schema = {"type": "object", "properties": {"project_id": _PID},
                     "required": ["project_id"], "additionalProperties": False}
    return [
        ActionSpec(
            id="montage.get_board", title_ru="Прочитать доску монтажа",
            description_ru="Кадры, закадр, очередь правок и устаревшие видео. Ничего не меняет "
                           "(доска только обновляет свой кэш).",
            params_schema=status_schema, executor=_get_board, risk=RISK_READ,
            idempotent=True, preconditions=(_PRE_PROJECT,), tags=("montage", "board")),
        ActionSpec(
            id="montage.apply_status", title_ru="Статус применения правок",
            description_ru="Состояние фоновой задачи apply: идёт ли, сколько операций сделано.",
            params_schema=status_schema, executor=_apply_status, risk=RISK_READ,
            idempotent=True, preconditions=(_PRE_PROJECT,), tags=("montage", "status")),
        ActionSpec(
            id="montage.montage_status", title_ru="Статус монтажа видео",
            description_ru="Состояние фоновой задачи пересборки видео (озвучка и сборка).",
            params_schema=status_schema, executor=_montage_status, risk=RISK_READ,
            idempotent=True, preconditions=(_PRE_PROJECT,), tags=("montage", "status")),
        ActionSpec(
            id="montage.recover_status", title_ru="Статус забора из галереи Outsee",
            description_ru="Состояние фоновой задачи подбора готовых карточек из галереи.",
            params_schema=status_schema, executor=_recover_status, risk=RISK_READ,
            idempotent=True, preconditions=(_PRE_PROJECT,), tags=("montage", "status")),
        ActionSpec(
            id="montage.set_voiceover", title_ru="Изменить закадровый текст кадра",
            description_ru="Записывает текст закадра кадра в БД. Таймкоды не пересчитываются. "
                           "Прежний текст не сохраняется.",
            params_schema={"type": "object", "properties": {
                "project_id": _PID,
                "frame_id": {"type": "integer", "minimum": 1,
                             "description": "frame_id из доски монтажа"},
                "text": {"type": "string", "maxLength": 20000}},
                "required": ["project_id", "frame_id", "text"], "additionalProperties": False},
            executor=_set_voiceover, risk=RISK_LOCAL_SAFE, idempotent=True,
            preconditions=(_PRE_PROJECT,), tags=("montage", "text")),
        ActionSpec(
            id="montage.set_coverage", title_ru="Изменить покрытие кадра",
            description_ru="Роль, крупность, ракурс, движение, стык, свет или действие кадра: "
                           "запись в БД без запуска генерации (картинка в очередь "
                           "автоматически не ставится).",
            params_schema={"type": "object", "properties": {
                "project_id": _PID,
                "frame_number": {"type": "integer", "minimum": 1},
                "type": {"type": "string", "enum": list(_COVERAGE_TYPES)},
                "value": {"type": "string", "minLength": 1, "maxLength": 4000},
                "parent_number": {"type": "integer", "minimum": 1,
                                  "description": "Только для coverage_kind (дочерний кадр)"}},
                "required": ["project_id", "frame_number", "type", "value"],
                "additionalProperties": False},
            executor=_set_coverage, risk=RISK_LOCAL_SAFE, idempotent=True,
            preconditions=(_PRE_PROJECT,), tags=("montage", "coverage")),
        ActionSpec(
            id="montage.queue_ops", title_ru="Поставить правки в очередь",
            description_ru="Дописывает операции перегенерации в очередь доски (одноимённые "
                           "заменяются). Деньги не тратятся: генерация стартует только при "
                           "применении правок, этого действия в каталоге нет.",
            params_schema={"type": "object", "properties": {
                "project_id": _PID,
                "ops": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
                    "type": "object", "properties": {
                        "type": {"type": "string", "enum": list(_QUEUE_OP_TYPES)},
                        "frame_number": {"type": "integer", "minimum": 1},
                        "shot": {"type": "integer", "enum": [1, 2]},
                        "slot": {"type": "string", "enum": ["parent"]},
                        "prompt": {"type": "string", "maxLength": 8000},
                        "correction": {"type": "string", "maxLength": 4000},
                        "instruction": {"type": "string", "maxLength": 4000}},
                    "required": ["type", "frame_number"], "additionalProperties": False}}},
                "required": ["project_id", "ops"], "additionalProperties": False},
            executor=_queue_ops, risk=RISK_LOCAL_SAFE, idempotent=True,
            preconditions=(_PRE_PROJECT, _PRE_NO_APPLY), tags=("montage", "queue")),
        ActionSpec(
            id="montage.swap_shots", title_ru="Поменять местами шот 1 и шот 2",
            description_ru="Меняет картинки и/или видео и промты шотов кадра. Обратимо "
                           "повторным вызовом, поэтому повтор после сбоя небезопасен.",
            params_schema={"type": "object", "properties": {
                "project_id": _PID,
                "frame_number": {"type": "integer", "minimum": 1},
                "kind": {"type": "string", "enum": ["image", "video", "both"]}},
                "required": ["project_id", "frame_number"], "additionalProperties": False},
            executor=_swap_shots_default, risk=RISK_LOCAL_SAFE, reversible=True,
            idempotent=False, preconditions=(_PRE_PROJECT, _PRE_NO_APPLY),
            tags=("montage", "media")),
    ]


async def _swap_shots_default(ctx: ActionContext, params: dict) -> dict:
    return await _swap_shots(ctx, {**params, "kind": params.get("kind", "both")})


def register_montage_actions(registry: ActionRegistry) -> None:
    registry.register_many(_specs())
    from app.services.action_layer.builtin_montage_more import register_more

    register_more(registry)
