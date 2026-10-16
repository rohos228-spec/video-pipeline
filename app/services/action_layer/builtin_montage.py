from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import (
    RISK_LOCAL_SAFE, RISK_READ, ActionRegistry, ActionSpec, Precondition)

_PID = {"type": "integer", "minimum": 1, "description": "ID проекта"}
_MAX_FRAMES_IN_RESULT = 300

_COVERAGE_TYPES = ("coverage_kind", "coverage_plan", "coverage_angle", "coverage_move",
                   "coverage_stitch", "coverage_light", "coverage_action")

_QUEUE_OP_TYPES = ("image_regen", "image_regen_prompt", "image_regen_correction",
                   "image_ai_change", "video_regen", "video_regen_prompt", "video_ai_change")


def _base(pid: int) -> str:
    return f"/projects/{pid}/montage-board"



QUEUE_ATTEMPTS = 4
_QUEUE_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


def _queue_lock(pid: int) -> asyncio.Lock:
    loop_id = id(asyncio.get_running_loop())
    for key in [k for k in _QUEUE_LOCKS if k[0] != loop_id]:
        _QUEUE_LOCKS.pop(key, None)
    return _QUEUE_LOCKS.setdefault((loop_id, int(pid)), asyncio.Lock())


def queue_fingerprint(board: dict) -> str:
    meta = (board or {}).get("meta") or {}
    return json.dumps({"ops": meta.get("pending_ops") or [], "trims": meta.get("video_trims") or {},
                       "rev": meta.get("queue_rev")}, sort_keys=True, ensure_ascii=False,
                      default=str)


@dataclass
class QueueChange:
    ops: list[dict]
    extra: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    skip: bool = False


async def queue_update(ctx: ActionContext, pid: int,
                       build: Callable[[dict], QueueChange]) -> tuple[QueueChange, dict]:
    base = _base(pid)
    async with _queue_lock(pid):
        for _ in range(QUEUE_ATTEMPTS):
            board = await ctx.call("GET", base)
            change = build(board)
            if change.skip:
                return change, {}
            if queue_fingerprint(await ctx.call("GET", base)) != queue_fingerprint(board):
                continue
            body: dict[str, Any] = {"pending_ops": change.ops, **change.extra}
            rev = ((board.get("meta") or {}).get("queue_rev"))
            if rev is not None:
                body["expected_rev"] = rev
            data = await ctx.call("POST", f"{base}/queue", json=body)
            if data.get("rejected") in ("queue_rev_mismatch", "queue_conflict"):
                continue
            if not data.get("ok"):
                raise RuntimeError(data.get("message") or data.get("rejected") or "queue rejected")
            return change, data
    raise RuntimeError("очередь правок меняется параллельно (интерфейс или другой план): "
                       "правка не сохранена, чтобы не затереть чужие операции; повторите")


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
    new_ops = list(params["ops"])
    new_keys = {_op_key(o) for o in new_ops}

    def build(board: dict) -> QueueChange:
        existing = list(((board.get("meta") or {}).get("pending_ops")) or [])
        return QueueChange([o for o in existing if _op_key(o) not in new_keys] + new_ops)

    _change, data = await queue_update(ctx, params["project_id"], build)
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
