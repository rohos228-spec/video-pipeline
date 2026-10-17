from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

from app.services.action_layer.builtin_montage import (
    _PID, _PRE_NO_APPLY, _PRE_PROJECT, QueueChange, _base, _op_key, queue_update)
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE, RISK_LOCAL_SAFE, RISK_PAID, RISK_READ, ActionRegistry,
    ActionSpec, CostEstimate, JobState, Precondition)

_IMAGE_OPS = frozenset({"image_regen", "image_regen_prompt", "image_regen_correction",
                        "image_ai_change"})
_VIDEO_OPS = frozenset({"video_regen", "video_regen_prompt", "video_ai_change"})

IMAGE_BILLED_ATTEMPTS = 3
VIDEO_BILLED_ATTEMPTS = 4
_LLM_USD_IN, _LLM_USD_OUT = 1.6, 8.4
_LLM_CALL_MIN = 0.01
MAX_IMPROVE_IMAGE_OPS = 25
_VIDEO_CLIP_SECONDS = 5


def _llm_credits(tokens_in: int, tokens_out: int) -> float:
    from app.services.kie_catalog import CREDIT_USD
    return (tokens_in * _LLM_USD_IN + tokens_out * _LLM_USD_OUT) / 1e6 / CREDIT_USD


LLM_SCENE_CALL_HIGH = _llm_credits(20_000, 4_000)
LLM_OP_CALL_HIGH = _llm_credits(4_000, 1_000)

_GEN = {"type": "string", "pattern": "^[a-z0-9_]{1,40}$"}
_IMG_RES = {"type": "string", "enum": ["1k", "2k", "3k", "4k"]}
_VID_RES = {"type": "string", "enum": ["720p", "1080p"]}
_FRAME_NO = {"type": "integer", "minimum": 1}
_SHOT = {"type": "integer", "enum": [1, 2]}
_ANCHOR_ITEM = {"type": "object", "properties": {
    "anchor": {"type": "string", "minLength": 1, "maxLength": 1000},
    "change": {"type": "string", "maxLength": 1000},
    "main": {"type": "boolean"}},
    "required": ["anchor"], "additionalProperties": False}

_BASIS_PROPS = {
    "image_generator": {**_GEN, "description": "Генератор картинок проекта (id), для оценки цены"},
    "image_resolution": {**_IMG_RES, "description": "Разрешение картинок проекта"},
    "video_generator": {**_GEN, "description": "Генератор видео проекта (id), для оценки цены"},
    "video_resolution": {**_VID_RES, "description": "Разрешение видео проекта"},
}



def _ops(board: dict) -> list[dict]:
    return [o for o in (((board.get("meta") or {}).get("pending_ops")) or []) if isinstance(o, dict)]


def _media_ops(board: dict) -> list[dict]:
    return [o for o in _ops(board) if str(o.get("type") or "").startswith(("image_", "video_"))]


def _select(ops: list[dict], numbers: list[int] | None) -> list[dict]:
    if not numbers:
        return list(ops)
    wanted = set(numbers)
    return [o for o in ops if int(o.get("frame_number") or 0) in wanted]


def _counts(ops: list[dict]) -> tuple[int, int]:
    img = sum(1 for o in ops if o.get("type") in _IMAGE_OPS)
    vid = sum(1 for o in ops if o.get("type") in _VIDEO_OPS)
    return img, vid


async def _job_running(ctx: ActionContext, path: str) -> bool:
    data = await ctx.call("GET", path)
    return ((data or {}).get("job") or {}).get("status") == "running"


async def _no_montage_running(ctx, params):
    if await _job_running(ctx, f"{_base(params['project_id'])}/montage-status"):
        return "идёт монтаж видео"
    return None


async def _no_recover_running(ctx, params):
    if await _job_running(ctx, f"{_base(params['project_id'])}/recover-outsee-status"):
        return "идёт забор из галереи Outsee"
    return None


async def _no_align_running(ctx, params):
    if await _job_running(ctx, f"/projects/{params['project_id']}/audio-align/status"):
        return "идёт разбор аудио"
    return None


_PRE_NO_MONTAGE = Precondition("no_montage_running", "Сейчас не идёт монтаж видео",
                               _no_montage_running)
_PRE_NO_RECOVER = Precondition("no_recover_running", "Сейчас не идёт забор из Outsee",
                               _no_recover_running)
_PRE_NO_ALIGN = Precondition("no_align_running", "Сейчас не идёт разбор аудио", _no_align_running)


def _pre_frame_ids(*keys: str) -> Precondition:
    async def check(ctx, params):
        for key in keys:
            if params.get(key) is None:
                continue
            await ctx.call("GET", f"{_base(params['project_id'])}/frames/{params[key]}/scene-editor")
        return None
    return Precondition("frames_exist", "Указанные кадры (frame_id) существуют", check)


def _pre_frame_numbers(*keys: str) -> Precondition:
    async def check(ctx, params):
        board = await ctx.call("GET", _base(params["project_id"]))
        have = {int(f.get("number") or 0) for f in board.get("frames") or []}
        for key in keys:
            vals = params.get(key)
            for n in (vals if isinstance(vals, list) else [vals]):
                if n is not None and int(n) not in have:
                    return f"кадра {n} нет на доске"
        return None
    return Precondition("frame_numbers_exist", "Указанные номера кадров есть на доске", check)


def _effective_settings(detail: dict) -> dict[str, str]:
    from app.generation_options import DEFAULTS
    from app.services.vibecode_catalog import resolve_node_media_settings

    proj = SimpleNamespace(
        meta=detail.get("meta") or {}, image_generator=detail.get("image_generator"),
        image_resolution=detail.get("image_resolution"), image_quality=detail.get("image_quality"),
        aspect_ratio=detail.get("aspect_ratio"))
    media = resolve_node_media_settings(proj, node_type="images")
    return {
        "image_generator": str(media["image_generator_id"]),
        "image_resolution": str(media["resolution_id"] or "").lower(),
        "video_generator": str(detail.get("video_generator") or DEFAULTS["video_generator"]),
        "video_resolution": str(detail.get("video_resolution") or DEFAULTS["video_resolution"]).lower(),
    }



def _kie_unit(kind: str, gen_id: str | None, res_id: str | None) -> tuple[float | None, str]:
    from app.generation_options import (
        IMAGE_GENERATORS_BY_ID, IMAGE_RESOLUTIONS_BY_ID, VIDEO_GENERATORS_BY_ID,
        VIDEO_RESOLUTIONS_BY_ID)
    from app.services import kie_catalog
    from app.services.media_route import image_provider_for, video_provider_for

    if not gen_id or not res_id:
        return None, f"не заданы {kind}_generator и {kind}_resolution"
    if kind == "image":
        choice, res = IMAGE_GENERATORS_BY_ID.get(gen_id), IMAGE_RESOLUTIONS_BY_ID.get(res_id)
        provider = image_provider_for(choice.outsee_slug) if choice else None
    else:
        choice, res = VIDEO_GENERATORS_BY_ID.get(gen_id), VIDEO_RESOLUTIONS_BY_ID.get(res_id)
        provider = video_provider_for(choice.outsee_slug) if choice else None
    if choice is None or res is None:
        return None, f"неизвестный {kind}-генератор или разрешение: {gen_id}/{res_id}"
    if provider != "kie":
        from app.settings import settings
        outsee = float(settings.action_layer_outsee_submit_credits or 0.0)
        if outsee > 0:
            return outsee, (f"{gen_id}: оплата через Outsee, тарифа нет; заложено {outsee:g} кр на отправку "
                            "(настройка ACTION_LAYER_OUTSEE_SUBMIT_CREDITS, консервативный потолок)")
        return None, f"{gen_id}: оплата через Outsee, тарифа в репозитории нет"
    from app.bots import kie_http
    table = kie_http.IMAGE_GENERATOR_TO_KIE_ID if kind == "image" else kie_http.VIDEO_GENERATOR_TO_KIE_ID
    kie_id = table.get(choice.outsee_slug) or choice.outsee_slug.replace("_", "-")
    spec = kie_catalog.get_model(kie_id)
    if spec is None:
        return None, f"{kie_id}: модели нет в kie_catalog (отдельный путь оплаты)"
    if kind == "image":
        values: dict[str, Any] = {"aspect_ratio": "9:16", "resolution": res.outsee_slug.upper()}
    else:
        values = {"aspect_ratio": "9:16", "duration": _VIDEO_CLIP_SECONDS,
                  "generate_audio": False, "resolution": res.outsee_slug.lower()}
    quote = kie_catalog.estimate_credits(spec, values)
    credits = float(quote["credits"])
    if credits <= 0:
        return None, f"{kie_id}: каталог вернул цену 0, считаем неизвестной"
    return credits, f"{kie_id} {res.outsee_slug} = {credits:g} кр ({quote.get('note') or quote['unit']})"


def _media_estimate(p: dict, n_img_low: int, n_img_high: int, n_vid: int,
                    extra_low: float = 0.0, extra_high: float = 0.0,
                    note: str = "") -> CostEstimate:
    low = extra_low
    high = extra_high
    parts = [note] if note else []
    if n_img_high > 0:
        unit, why = _kie_unit("image", p.get("image_generator"), p.get("image_resolution"))
        if unit is None:
            return CostEstimate.unknown(why)
        low += n_img_low * unit
        high += n_img_high * IMAGE_BILLED_ATTEMPTS * unit + n_img_high * LLM_OP_CALL_HIGH
        parts.append(f"картинки до {n_img_high} x {IMAGE_BILLED_ATTEMPTS} попытки: {why}")
    if n_vid > 0:
        unit, why = _kie_unit("video", p.get("video_generator"), p.get("video_resolution"))
        if unit is None:
            return CostEstimate.unknown(why)
        low += n_vid * unit
        high += n_vid * VIDEO_BILLED_ATTEMPTS * unit + n_vid * LLM_OP_CALL_HIGH
        parts.append(f"видео {n_vid} x {VIDEO_BILLED_ATTEMPTS} попытки: {why}")
    if n_img_high <= 0 and n_vid <= 0:
        return CostEstimate.unknown("не заявлено ни одной операции генерации")
    parts.append(f"LLM на операцию до {LLM_OP_CALL_HIGH:.1f} кр (ставки замера, объём допущен)")
    return CostEstimate(round(low, 2), round(high, 2), "; ".join(parts))


def _llm_only(calls: int, basis: str) -> CostEstimate:
    return CostEstimate(round(_LLM_CALL_MIN * calls, 2), round(LLM_SCENE_CALL_HIGH * calls, 2), basis)


def _basis_precondition(needs) -> Precondition:
    async def check(ctx, params):
        need_img, need_vid = needs(params)
        eff = _effective_settings(await ctx.call("GET", f"/projects/{params['project_id']}"))
        for kind, need in (("image", need_img), ("video", need_vid)):
            if not need:
                continue
            for field in (f"{kind}_generator", f"{kind}_resolution"):
                if params.get(field) is None:
                    return f"укажите {field} (текущее значение: {eff[field]})"
                if params[field] != eff[field]:
                    return f"{field}: заявлено {params[field]}, в проекте {eff[field]}"
        return None
    return Precondition("cost_basis_matches",
                        "Заявленные модель и разрешение совпадают с настройками проекта", check)


async def _verify_basis(ctx, params, need_img: bool, need_vid: bool) -> None:
    reason = await _basis_precondition(lambda p: (need_img, need_vid)).check(ctx, params)
    if reason:
        raise RuntimeError(f"оценка стоимости не совпадает с проектом: {reason}")



def _job_state(job: dict, label: str, keys: tuple[str, ...] = ()) -> JobState:
    status = job.get("status")
    done, total = job.get("done_ops"), job.get("total_ops")
    progress = (float(done) / float(total)) if isinstance(done, int) and isinstance(total, int) \
        and total > 0 else None
    note = f"{done}/{total}" if progress is not None else str(status or "")
    if status == "running":
        return JobState(done=False, progress=progress, note=note)
    result = {k: job.get(k) for k in keys if k in job}
    if status == "done":
        return JobState(done=True, ok=True, progress=1.0, note=note, result=result)
    if status == "cancelled":
        return JobState(done=True, ok=False, error=f"{label}: остановлено", note=note)
    if status == "error":
        return JobState(done=True, ok=False, error=job.get("error") or f"{label}: ошибка", note=note)
    return JobState(done=True, ok=False, error=f"{label}: задача не найдена (status={status})")


async def _stop_project(ctx: ActionContext, params: dict) -> None:
    await ctx.call("POST", f"/projects/{params['project_id']}/stop")


async def _poll_apply(ctx, params, started) -> JobState:
    if not started.get("apply_started", False):
        return JobState(done=True, ok=True, progress=1.0)
    data = await ctx.call("GET", f"{_base(params['project_id'])}/apply-status")
    return _job_state((data or {}).get("job") or {}, "применение правок",
                      ("done_ops", "total_ops", "finished_at"))


async def _poll_montage(ctx, params, started) -> JobState:
    data = await ctx.call("GET", f"{_base(params['project_id'])}/montage-status")
    return _job_state((data or {}).get("job") or {}, "монтаж", ("final_video", "finished_at"))


async def _poll_recover(ctx, params, started) -> JobState:
    data = await ctx.call("GET", f"{_base(params['project_id'])}/recover-outsee-status")
    return _job_state((data or {}).get("job") or {}, "забор из Outsee", ("finished_at",))


async def _poll_align(ctx, params, started) -> JobState:
    data = await ctx.call("GET", f"/projects/{params['project_id']}/audio-align/status")
    return _job_state((data or {}).get("job") or {}, "разбор аудио", ("result", "finished_at"))


async def _start_apply(ctx: ActionContext, pid: int, queue: list[dict],
                       numbers: list[int] | None) -> dict:
    resp = await ctx.call("POST", f"{_base(pid)}/apply",
                          json={"pending_ops": queue, "frame_numbers": numbers or []})
    if resp.get("already_running"):
        raise RuntimeError("применение правок уже идёт")
    if not resp.get("started"):
        raise RuntimeError("apply не запустился: " + str(resp.get("message") or resp)[:200])
    return {"apply_started": True, "total_ops": (resp.get("job") or {}).get("total_ops")}



async def _cancel_queue_ops(ctx, params):
    def build(board: dict) -> QueueChange:
        existing = _ops(board)
        if params.get("clear_all"):
            kept = []
        else:
            gone = {_op_key({**o, "shot": o.get("shot", 1)}) for o in params["ops"]}
            kept = [o for o in existing if _op_key(o) not in gone]
        return QueueChange(kept, {"force_clear": not kept},
                           {"removed": len(existing) - len(kept), "left_in_queue": len(kept)})

    change, _data = await queue_update(ctx, params["project_id"], build)
    return change.payload


async def _pre_cancel_args(ctx, params):
    if not params.get("clear_all") and not params.get("ops"):
        return "укажите ops или clear_all=true"
    return None


async def _apply_limits(ctx, params):
    board = await ctx.call("GET", _base(params["project_id"]))
    img, vid = _counts(_select(_media_ops(board), params.get("frame_numbers")))
    if img + vid == 0:
        return "в выбранной части очереди нет операций картинок/видео"
    if img > params["max_image_ops"] or vid > params["max_video_ops"]:
        return (f"в очереди картинок {img} (заявлено {params['max_image_ops']}), "
                f"видео {vid} (заявлено {params['max_video_ops']})")
    return None


async def _apply_queue(ctx, params):
    pid = params["project_id"]
    board = await ctx.call("GET", _base(pid))
    queue = _media_ops(board)
    selected = _select(queue, params.get("frame_numbers"))
    img, vid = _counts(selected)
    if img + vid == 0:
        raise RuntimeError("нечего применять: в выбранной части очереди нет операций")
    if img > params["max_image_ops"] or vid > params["max_video_ops"]:
        raise RuntimeError(f"очередь больше заявленной: картинок {img}, видео {vid}")
    await _verify_basis(ctx, params, img > 0, vid > 0)
    queue = sorted(queue, key=lambda o: 0 if o.get("type") in _IMAGE_OPS else 1)
    started = await _start_apply(ctx, pid, queue, params.get("frame_numbers"))
    return {**started, "image_ops": img, "video_ops": vid}


def _apply_estimate(p: dict) -> CostEstimate:
    return _media_estimate(p, p["max_image_ops"], p["max_image_ops"], p["max_video_ops"],
                           note="применение очереди правок")


def _apply_needs(p):
    return p.get("max_image_ops", 0) > 0, p.get("max_video_ops", 0) > 0



def _queue_replacing(existing_media: list[dict], new_ops: list[dict]) -> list[dict]:
    numbers = {int(o["frame_number"]) for o in new_ops}
    keep = [o for o in existing_media
            if not (o.get("type") in _IMAGE_OPS and int(o.get("frame_number") or 0) in numbers)]
    return keep + new_ops


async def _queue_and_apply(ctx, pid: int, new_ops: list[dict], max_ops: int,
                           strict: bool = False) -> dict:
    numbers = sorted({int(o["frame_number"]) for o in new_ops})

    def build(board: dict) -> QueueChange:
        merged = _queue_replacing(_media_ops(board), new_ops)
        refusal = None
        if len(new_ops) > max_ops:
            refusal = f"операций картинок {len(new_ops)} больше заявленных {max_ops}"
        elif any(o.get("type") in _VIDEO_OPS and int(o.get("frame_number") or 0) in numbers
                 for o in merged):
            refusal = ("для этих кадров в очереди есть видео-операции: примените их отдельно "
                       "(montage.apply_queue)")
        return QueueChange(merged, payload={"refusal": refusal}, skip=bool(refusal))

    change, _saved = await queue_update(ctx, pid, build)
    refusal = change.payload["refusal"]
    if refusal:
        if strict:
            raise RuntimeError(refusal)
        return {"applied": False, "apply_started": False, "image_ops": len(new_ops),
                "queued": 0, "reason": refusal}
    merged = list(change.ops)
    merged.sort(key=lambda o: 0 if o.get("type") in _IMAGE_OPS else 1)
    started = await _start_apply(ctx, pid, merged, numbers)
    return {**started, "applied": True, "queued": len(new_ops), "image_ops": len(new_ops)}


async def _improve_scene(ctx, params):
    pid = params["project_id"]
    await _verify_basis(ctx, params, True, False)
    body = {"prompt": params["prompt"],
            "anchors": [{"якорь": a["anchor"], "изменение": a.get("change", ""),
                         "главный": bool(a.get("main"))} for a in params.get("anchors") or []]}
    res = await ctx.call("POST", f"{_base(pid)}/frames/{params['frame_id']}/scene-improve",
                         json=body, timeout=300.0)
    ops = [o for o in res.get("image_ops") or []
           if isinstance(o, dict) and str(o.get("type") or "").startswith("image_")]
    out = {"improved": True, "inserted_frames": res.get("inserted_frames"),
           "message": res.get("message"), "apply_started": False}
    if not ops:
        return {**out, "applied": False, "reason": "сервер не вернул операций картинок"}
    return {**out, **await _queue_and_apply(ctx, pid, ops, params["max_image_ops"])}


def _improve_estimate(p: dict) -> CostEstimate:
    n = p["max_image_ops"]
    llm_note = f"улучшение сцены: до 3 шагов LLM по {LLM_SCENE_CALL_HIGH:.1f} кр"
    est = _media_estimate(p, 1, n, 0, extra_low=3 * _LLM_CALL_MIN,
                          extra_high=3 * LLM_SCENE_CALL_HIGH, note=llm_note)
    return est


def _scene_instruction(fr: dict) -> str:
    chars = str(fr.get("characters") or "").strip()
    parts = [f"План: {str(fr.get('shot_plan') or '').strip() or 'как в кадре'}"]
    for label, key in (("Ракурс", "shot_angle"), ("Движение", "shot_move")):
        if str(fr.get(key) or "").strip():
            parts.append(f"{label}: {str(fr[key]).strip()}")
    if chars:
        parts.append(f"Персонажи: {chars}")
    parts.append(f"Действие: {str(fr.get('shot_action') or '').strip() or 'как в кадре'}")
    return ". ".join(parts)


def scene_image_ops(frames: list[dict], numbers: list[int], scene_prompt: str = "") -> list[dict]:
    wanted = set(numbers)
    live = [f for f in frames if int(f.get("number") or 0) in wanted and not f.get("shot_leftover")]
    by_no = {int(f["number"]): f for f in live}
    parents: set[int] = set()
    for fr in live:
        n = int(fr["number"])
        if fr.get("shot_kind") == "parent":
            parents.add(n)
        p = fr.get("shot_parent_number")
        if isinstance(p, int) and p >= 1 and p != n and p in by_no:
            parents.add(p)
    ops: list[dict] = []
    for n in sorted(parents):
        ops.append({"type": "image_ai_change", "frame_number": n, "shot": 1, "slot": "parent",
                    "instruction": scene_prompt.strip() or _scene_instruction(by_no[n])})
    for fr in live:
        n = int(fr["number"])
        ops.append({"type": "image_ai_change", "frame_number": n, "shot": 1,
                    "instruction": _scene_instruction(fr)})
        if fr.get("has_end_still") is True:
            ops.append({"type": "image_ai_change", "frame_number": n, "shot": 2,
                        "instruction": _scene_instruction(fr) + " Конечный кадр сцены."})
    return ops


async def _generate_scene_images(ctx, params):
    pid = params["project_id"]
    await _verify_basis(ctx, params, True, False)
    board = await ctx.call("GET", _base(pid))
    ops = scene_image_ops(board.get("frames") or [], params["frame_numbers"],
                          params.get("scene_prompt", ""))
    if not ops:
        raise RuntimeError("нет живых кадров сцены для картинок")
    return await _queue_and_apply(ctx, pid, ops, 3 * len(params["frame_numbers"]), strict=True)


def _scene_images_estimate(p: dict) -> CostEstimate:
    n = len(p["frame_numbers"])
    return _media_estimate(p, n, 3 * n, 0, note=f"картинки сцены: {n} кадров, до 3 слотов на кадр")



async def _generate_scene(ctx, params):
    res = await ctx.call("POST", f"{_base(params['project_id'])}/frames/{params['frame_id']}/scene-action-generate",
                         json={"prompt": params["prompt"], "replace_ns": params.get("replace_ns") or []},
                         timeout=300.0)
    return {"frame_id": res.get("frame_id"), "frame_number": res.get("frame_number"),
            "replace_ns": res.get("replace_ns"), "inserted_frames": res.get("inserted_frames"),
            "chain": str(res.get("chain") or "")[:2000]}


async def _suggest_variants(ctx, params):
    res = await ctx.call("POST", f"{_base(params['project_id'])}/frames/{params['frame_id']}/scene-variants",
                         json={"kind": params["kind"], "desc": params.get("desc", ""),
                               "count": params.get("count", 3)}, timeout=300.0)
    return {"kind": params["kind"], "variants": res.get("variants", res)}


async def _set_scene_chain(ctx, params):
    data = await ctx.call("POST", f"{_base(params['project_id'])}/coverage", json={
        "type": "coverage_scene_action", "frame_number": params["frame_number"],
        "shot": 1, "action": params["action"]})
    return {"ok": bool(data.get("ok")), "frame_number": params["frame_number"]}


async def _set_anchors(ctx, params):
    anchors = [{"якорь": a["anchor"], "изменение": a.get("change", ""),
                "главный": bool(a.get("main"))} for a in params["anchors"]]
    data = await ctx.call("POST", f"{_base(params['project_id'])}/coverage", json={
        "type": "coverage_anchors", "frame_number": params["frame_number"], "anchors": anchors})
    return {"ok": bool(data.get("ok")), "frame_number": params["frame_number"],
            "anchors": len(anchors)}


async def _set_vo_span(ctx, params):
    body: dict = {"type": "coverage_vo_span", "frame_number": params["frame_number"]}
    if params.get("clear"):
        body["clear"] = True
    else:
        for key in ("start", "end", "text", "full"):
            if key in params:
                body[key] = params[key]
    data = await ctx.call("POST", f"{_base(params['project_id'])}/coverage", json=body)
    return {"ok": bool(data.get("ok")), "frame_number": params["frame_number"],
            "cleared": bool(params.get("clear"))}


async def _pre_vo_span_args(ctx, params):
    if not params.get("clear") and not params.get("text"):
        return "укажите text или clear=true"
    return None


async def _delete_child(ctx, params):
    data = await ctx.call("POST", f"{_base(params['project_id'])}/coverage", json={
        "type": "coverage_delete", "frame_number": params["frame_number"]})
    return {"ok": bool(data.get("ok")), "deleted_frame_number": params["frame_number"]}



async def _insert_frame(ctx, params):
    data = await ctx.call("POST", f"{_base(params['project_id'])}/frames/insert", json={
        "after_frame_id": params.get("after_frame_id"), "voiceover": params.get("voiceover", ""),
        "kind": params.get("kind", "parent")})
    return {k: data.get(k) for k in ("id", "number", "uuid", "sort_key")}


async def _pre_insert_args(ctx, params):
    if params.get("kind") == "child" and params.get("after_frame_id") is None:
        return "для шота в сцене нужен after_frame_id"
    return None


async def _merge_scenes(ctx, params):
    data = await ctx.call("POST", f"{_base(params['project_id'])}/scenes/merge", json={
        "left_frame_id": params["left_frame_id"], "right_frame_id": params["right_frame_id"]})
    return {k: data.get(k) for k in ("parent_id", "parent_number", "merged_frames", "vo_scene_size")}


async def _split_scene(ctx, params):
    body: dict = {"frame_id": params["frame_id"], "all": False}
    if params.get("frame_ids"):
        body["frame_ids"] = params["frame_ids"]
    data = await ctx.call("POST", f"{_base(params['project_id'])}/scenes/split", json=body)
    return {k: data.get(k) for k in ("left_id", "left_number", "right_id", "right_number",
                                     "left_size", "right_size")}


async def _explode_scene(ctx, params):
    body: dict = {"frame_id": params["frame_id"], "all": True}
    if params.get("frame_ids"):
        body["frame_ids"] = params["frame_ids"]
    data = await ctx.call("POST", f"{_base(params['project_id'])}/scenes/split", json=body)
    return {k: data.get(k) for k in ("scenes", "parent_id", "parent_number")}


async def _delete_frame(ctx, params):
    data = await ctx.call("DELETE", f"{_base(params['project_id'])}/frames/{params['frame_id']}")
    return {k: data.get(k) for k in ("frame_id", "number", "deleted", "promoted")}



async def _swap_slots(ctx, params):
    data = await ctx.call("POST", f"{_base(params['project_id'])}/swap-slots", params={
        "kind": params["kind"], "a_frame": params["a_frame"], "a_shot": params["a_shot"],
        "b_frame": params["b_frame"], "b_shot": params["b_shot"]})
    return {"ok": bool(data.get("ok")), "kind": params["kind"]}


async def _move_image(ctx, params):
    q = {k: params[k] for k in ("from_frame", "from_shot", "to_frame", "to_shot")}
    q["from_slot"], q["to_slot"] = params.get("from_slot", ""), params.get("to_slot", "")
    data = await ctx.call("POST", f"{_base(params['project_id'])}/move-image", params=q)
    return {"ok": bool(data.get("ok")), "action": data.get("action")}


async def _delete_image(ctx, params):
    data = await ctx.call("POST", f"{_base(params['project_id'])}/delete-image", params={
        "frame_number": params["frame_number"], "shot": params.get("shot", 1),
        "slot": params.get("slot", "")})
    return {"deleted": bool(data.get("ok")), "frame_number": params["frame_number"]}


async def _delete_video(ctx, params):
    data = await ctx.call("POST", f"{_base(params['project_id'])}/delete-video", params={
        "frame_number": params["frame_number"], "shot": params.get("shot", 1)})
    return {"deleted": bool(data.get("ok")), "frame_number": params["frame_number"]}


async def _set_video_trim(ctx, params):
    if "end" in params and params["end"] <= params["start"]:
        raise ValueError("end должен быть больше start")
    key = f"{params['frame_number']}:{params['shot']}"

    def build(board: dict) -> QueueChange:
        trims = copy.deepcopy((board.get("meta") or {}).get("video_trims") or {})
        if params.get("clear"):
            trims.pop(key, None)
        else:
            trims[key] = {"start": float(params["start"]),
                          **({"end": float(params["end"])} if "end" in params else {})}
        return QueueChange(_ops(board), {"video_trims": trims},
                           {"key": key, "trim": trims.get(key)})

    change, _data = await queue_update(ctx, params["project_id"], build)
    return change.payload


async def _pre_trim_args(ctx, params):
    if not params.get("clear") and "start" not in params:
        return "укажите start или clear=true"
    return None


async def _list_ref_assets(ctx, params):
    data = await ctx.call("GET", f"{_base(params['project_id'])}/ref-assets")
    return {"assets": [{k: a.get(k) for k in ("kind", "code", "name", "file")}
                       for a in data.get("assets") or []]}


async def _link_ref(ctx, params):
    data = await ctx.call("POST", f"{_base(params['project_id'])}/link-ref", params={
        "frame_number": params["frame_number"], "file": params["file"],
        "kind": params.get("kind", ""), "name": params.get("name", "")})
    return {"ok": bool(data.get("ok")), "refs": data.get("refs")}


async def _delete_ref(ctx, params):
    data = await ctx.call("POST", f"{_base(params['project_id'])}/delete-ref", params={
        "frame_number": params["frame_number"], "ref_id": params["ref_id"], "kind": params["kind"]})
    return {"deleted": bool(data.get("ok")), "refs": data.get("refs")}


async def _unlink_ref(ctx, params):
    if params.get("kind") not in ("character", "item"):
        raise ValueError("unlink_ref: допустимы только kind=character|item; "
                         "удаление загруженного рефа - отдельное действие delete_manual_ref")
    return await _delete_ref(ctx, params)


async def _delete_manual_ref(ctx, params):
    if params.get("kind") != "manual":
        raise ValueError("delete_manual_ref: допустим только kind=manual")
    return await _delete_ref(ctx, params)



async def _recover_outsee(ctx, params):
    resp = await ctx.call("POST", f"{_base(params['project_id'])}/recover-outsee")
    if resp.get("already_running"):
        raise RuntimeError("забор из Outsee уже идёт")
    if not resp.get("started"):
        raise RuntimeError(str(resp.get("message") or "забор не запустился"))
    return {"started": True}


async def _run_montage(ctx, params):
    resp = await ctx.call("POST", f"{_base(params['project_id'])}/montage")
    if resp.get("already_running"):
        raise RuntimeError("монтаж уже идёт")
    if not resp.get("started"):
        raise RuntimeError("монтаж не запустился")
    return {"started": True}


async def _align_audio(ctx, params):
    resp = await ctx.call("POST", f"/projects/{params['project_id']}/audio-align", params={
        "method": params.get("method", "nemo_auto"), "force_asr": bool(params.get("force_asr")),
        "run_assemble": False})
    if resp.get("already_running"):
        raise RuntimeError("разбор аудио уже идёт")
    if not resp.get("started"):
        raise RuntimeError("разбор аудио не запустился")
    return {"started": True, "method": (resp.get("job") or {}).get("method")}


async def _set_assemble_params(ctx, params):
    pid = params["project_id"]
    detail = await ctx.call("GET", f"/projects/{pid}")
    nsp = copy.deepcopy((detail.get("meta") or {}).get("node_step_params") or {})
    bucket = dict(nsp.get("assemble") or {})
    changed = {k: params[k] for k in _ASSEMBLE_KEYS if k in params}
    if not changed:
        raise ValueError("не указано ни одного параметра сборки")
    bucket.update(changed)
    nsp["assemble"] = bucket
    await ctx.call("PATCH", f"/projects/{pid}", json={"meta": {"node_step_params": nsp}})
    return {"assemble": changed}


_ASSEMBLE_KEYS = ("post_voiceover_tail_seconds", "bgm_level", "subtitles_enabled",
                  "skip_intro_enabled", "skip_intro_seconds")


async def _pre_assemble_args(ctx, params):
    if not any(k in params for k in _ASSEMBLE_KEYS):
        return "не указано ни одного параметра сборки"
    return None


async def _get_cost_basis(ctx, params):
    pid = params["project_id"]
    eff = _effective_settings(await ctx.call("GET", f"/projects/{pid}"))
    board = await ctx.call("GET", _base(pid))
    img, vid = _counts(_media_ops(board))
    out: dict = {"effective": eff, "queue": {"image_ops": img, "video_ops": vid}}
    for kind in ("image", "video"):
        unit, why = _kie_unit(kind, eff[f"{kind}_generator"], eff[f"{kind}_resolution"])
        out[f"{kind}_priced"] = unit is not None
        out[f"{kind}_price_basis"] = why
    return out



def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": {"project_id": _PID, **props},
            "required": ["project_id", *required], "additionalProperties": False}


def _specs() -> list[ActionSpec]:
    S = ActionSpec
    frame_id = {"type": "integer", "minimum": 1, "description": "frame_id из доски монтажа"}
    frame_ids = {"type": "array", "minItems": 1, "maxItems": 60, "items": frame_id}
    basis_img = {k: _BASIS_PROPS[k] for k in ("image_generator", "image_resolution")}
    stop_note = " Стоп - общий ⏹ проекта: блокирует автопродвижение до ручного ▶."
    return [
        S(id="montage.cancel_queue_ops", title_ru="Убрать правки из очереди",
          description_ru="Удаляет выбранные операции из очереди доски или всю очередь. Файлы не "
                         "трогает; убранную операцию можно поставить заново.",
          params_schema=_obj({
              "clear_all": {"type": "boolean"},
              "ops": {"type": "array", "minItems": 1, "maxItems": 100, "items": {
                  "type": "object", "properties": {
                      "type": {"type": "string", "enum": sorted(_IMAGE_OPS | _VIDEO_OPS)},
                      "frame_number": _FRAME_NO, "shot": _SHOT,
                      "slot": {"type": "string", "enum": ["parent"]}},
                  "required": ["type", "frame_number"], "additionalProperties": False}}}, []),
          executor=_cancel_queue_ops, risk=RISK_LOCAL_SAFE, idempotent=True,
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY,
                         Precondition("cancel_args", "Указаны ops или clear_all", _pre_cancel_args)),
          tags=("montage", "queue")),
        S(id="montage.apply_queue", title_ru="Применить правки очереди (генерация)",
          description_ru="Запускает фоновую генерацию картинок и видео по очереди доски напрямую "
                         "через /apply (кнопка панели на очереди только из видео не включается). "
                         "Новый файл заменяет текущий в слоте, прежний уходит в old/. Заявите "
                         "max_*_ops и модель проекта (montage.get_cost_basis)."
                         + stop_note,
          params_schema=_obj({
              "frame_numbers": {"type": "array", "maxItems": 100, "items": _FRAME_NO,
                                "description": "Пусто или нет = вся очередь"},
              "max_image_ops": {"type": "integer", "minimum": 0, "maximum": 200},
              "max_video_ops": {"type": "integer", "minimum": 0, "maximum": 100},
              **_BASIS_PROPS}, ["max_image_ops", "max_video_ops"]),
          executor=_apply_queue, poll=_poll_apply, stop=_stop_project, poll_interval_s=3.0,
          risk=RISK_DESTRUCTIVE, spends_money=True, destructive=True, reversible=False,
          idempotent=False, estimator=_apply_estimate,
          destructive_effects=lambda p: (
              "Генерация заменит текущие картинки/видео слотов очереди новыми. Прежние файлы "
              "уходят в old/ проекта, но программного отката нет; prompt-версии и artifact-записи "
              "слотов переписываются."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY,
                         Precondition("queue_within_limits",
                                      "В очереди есть операции и их не больше заявленных max_*_ops",
                                      _apply_limits),
                         _basis_precondition(_apply_needs)),
          tags=("montage", "generate", "paid")),
        S(id="montage.improve_scene", title_ru="Улучшить сцену (LLM + картинки кадров)",
          description_ru="Самое дорогое действие панели: до 3 шагов LLM (действие, кадры, QC) "
                         "переписывают сцену ячейки, затем картинки всех кадров сцены идут в "
                         "фоновую генерацию. Если операций больше max_image_ops или в очереди "
                         "есть видео для этих кадров, генерация не стартует (сцена уже переписана)."
                         + stop_note,
          params_schema=_obj({
              "frame_id": frame_id,
              "prompt": {"type": "string", "maxLength": 8000},
              "anchors": {"type": "array", "maxItems": 12, "items": _ANCHOR_ITEM},
              "max_image_ops": {"type": "integer", "minimum": 1, "maximum": MAX_IMPROVE_IMAGE_OPS},
              **basis_img}, ["frame_id", "prompt", "max_image_ops", "image_generator",
                             "image_resolution"]),
          executor=_improve_scene, poll=_poll_apply, stop=_stop_project, poll_interval_s=3.0,
          risk=RISK_DESTRUCTIVE, spends_money=True, destructive=True, reversible=False,
          idempotent=False, estimator=_improve_estimate,
          destructive_effects=lambda p: (
              "Сцена ячейки будет переписана (действия, кадры, возможны новые кадры), затем "
              "картинки кадров заменены новыми. Текст прежней сцены не сохраняется; прежние "
              "картинки уходят в old/ без программного отката."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_ids("frame_id"),
                         _basis_precondition(lambda p: (True, False))),
          tags=("montage", "scene", "llm", "generate", "paid")),
        S(id="montage.generate_scene_images", title_ru="Сгенерировать картинки сцены",
          description_ru="Ставит в очередь image_ai_change для кадров сцены (still родителя, затем "
                         "кадры, при наличии конечного кадра и он) и запускает генерацию. По одному "
                         "LLM-вызову на операцию. Видео в очереди для этих кадров блокирует запуск."
                         + stop_note,
          params_schema=_obj({
              "frame_numbers": {"type": "array", "minItems": 1, "maxItems": 40, "items": _FRAME_NO},
              "scene_prompt": {"type": "string", "maxLength": 4000}, **basis_img},
              ["frame_numbers", "image_generator", "image_resolution"]),
          executor=_generate_scene_images, poll=_poll_apply, stop=_stop_project,
          poll_interval_s=3.0, risk=RISK_DESTRUCTIVE, spends_money=True, destructive=True,
          reversible=False, idempotent=False, estimator=_scene_images_estimate,
          destructive_effects=lambda p: (
              "Картинки кадров " + ", ".join(str(n) for n in p["frame_numbers"]) +
              " будут заменены новыми; прежние файлы уходят в old/ без программного отката."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("frame_numbers"),
                         _basis_precondition(lambda p: (True, False))),
          tags=("montage", "scene", "generate", "paid")),
        S(id="montage.generate_scene", title_ru="Сгенерировать сцены ячейки (LLM)",
          description_ru="Один вызов LLM пишет сцены ячейки по заказу оператора; replace_ns "
                         "пересобирает только выбранные куски цепочки. Могут появиться кадры. "
                         "Картинки не запускаются.",
          params_schema=_obj({
              "frame_id": frame_id, "prompt": {"type": "string", "maxLength": 8000},
              "replace_ns": {"type": "array", "maxItems": 12,
                             "items": {"type": "integer", "minimum": 1, "maximum": 99}}},
              ["frame_id", "prompt"]),
          executor=_generate_scene, risk=RISK_DESTRUCTIVE, spends_money=True, destructive=True,
          reversible=False, idempotent=False,
          estimator=lambda p: _llm_only(1, "один вызов LLM сцены; замеренные ставки, объём допущен"),
          destructive_effects=lambda p: (
              "Действия и сцена ячейки (или выбранные куски) будут перезаписаны ответом LLM, "
              "прежний текст не сохраняется; в ячейке могут появиться новые кадры."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_ids("frame_id")),
          tags=("montage", "scene", "llm", "paid")),
        S(id="montage.suggest_scene_variants", title_ru="Подобрать варианты для сцены (LLM)",
          description_ru="Один вызов LLM возвращает варианты действия, формата сцены или якорей. "
                         "Ничего не записывает.",
          params_schema=_obj({
              "frame_id": frame_id, "kind": {"type": "string", "enum": ["action", "template", "anchors"]},
              "desc": {"type": "string", "maxLength": 4000},
              "count": {"type": "integer", "minimum": 1, "maximum": 5}}, ["frame_id", "kind"]),
          executor=_suggest_variants, risk=RISK_PAID, spends_money=True, idempotent=True,
          estimator=lambda p: _llm_only(1, "один вызов LLM вариантов; замеренные ставки, объём допущен"),
          preconditions=(_PRE_PROJECT, _pre_frame_ids("frame_id")), tags=("montage", "scene", "llm")),
        S(id="montage.set_scene_chain", title_ru="Разобрать последовательность на кадры",
          description_ru="Режет цепочку «действие1 → действие2 → ...» на шоты ячейки без LLM. "
                         "Перезаписывает поля шотов ячейки.",
          params_schema=_obj({"frame_number": _FRAME_NO,
                              "action": {"type": "string", "minLength": 1, "maxLength": 8000}},
                             ["frame_number", "action"]),
          executor=_set_scene_chain, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=True,
          destructive_effects=lambda p: (
              f"Шоты ячейки кадра {p['frame_number']} пересобираются из текста; их прежние "
              "действия, планы и ракурсы перезаписываются без сохранения."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("frame_number")),
          tags=("montage", "scene", "coverage")),
        S(id="montage.set_anchors", title_ru="Задать якоря закадра",
          description_ru="Якоря режут закадр ячейки по кадрам. Якорей больше кадров: после "
                         "правленого кадра создаются шоты и номера кадров могут сдвинуться.",
          params_schema=_obj({"frame_number": _FRAME_NO,
                              "anchors": {"type": "array", "maxItems": 12, "items": _ANCHOR_ITEM}},
                             ["frame_number", "anchors"]),
          executor=_set_anchors, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False,
          destructive_effects=lambda p: (
              "Прежние якоря заменяются; закадр ячейки пересобирается, могут добавиться шоты и "
              "перенумероваться кадры (имена frame_NNN_* и очередь правок разойдутся с новыми "
              "номерами)."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("frame_number")),
          tags=("montage", "scene", "coverage")),
        S(id="montage.set_voiceover_span", title_ru="Закрепить кусок закадра за сценой",
          description_ru="Закрепляет за сценой фрагмент закадра или снимает закрепление (clear).",
          params_schema=_obj({
              "frame_number": _FRAME_NO, "clear": {"type": "boolean"},
              "text": {"type": "string", "minLength": 1, "maxLength": 20000},
              "start": {"type": "integer", "minimum": 0}, "end": {"type": "integer", "minimum": 0},
              "full": {"type": "string", "maxLength": 20000}}, ["frame_number"]),
          executor=_set_vo_span, risk=RISK_LOCAL_SAFE, idempotent=True,
          preconditions=(_PRE_PROJECT, _pre_frame_numbers("frame_number"),
                         Precondition("vo_span_args", "Указаны text или clear", _pre_vo_span_args)),
          tags=("montage", "scene", "coverage")),
        S(id="montage.delete_child_frame", title_ru="Удалить дочерний кадр сцены",
          description_ru="Удаляет дочерний шот (не голову сцены). Медиа кадра переносится в "
                         "old/scenes/deleted-*, записи БД кадра удаляются.",
          params_schema=_obj({"frame_number": _FRAME_NO}, ["frame_number"]),
          executor=_delete_child, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False,
          destructive_effects=lambda p: (
              f"Кадр {p['frame_number']} удаляется из БД (промты, тексты, связи). Его картинки и "
              "видео переносятся в old/scenes/deleted-*; вернуть кадр программно нельзя."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("frame_number")),
          tags=("montage", "frames")),
        S(id="montage.insert_frame", title_ru="Добавить кадр или сцену",
          description_ru="kind=parent: новая ячейка закадра после кадра (или в начало); kind=child: "
                         "шот в ту же сцену. Номера существующих кадров не меняются, новый получает "
                         "max+1.",
          params_schema=_obj({"after_frame_id": frame_id,
                              "voiceover": {"type": "string", "maxLength": 20000},
                              "kind": {"type": "string", "enum": ["parent", "child"]}}, []),
          executor=_insert_frame, risk=RISK_LOCAL_SAFE, idempotent=False,
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_ids("after_frame_id"),
                         Precondition("insert_args", "Для шота указан after_frame_id", _pre_insert_args)),
          tags=("montage", "frames")),
        S(id="montage.merge_scenes", title_ru="Объединить две сцены",
          description_ru="Склеивает две соседние ячейки закадра в одну сцену. Номера кадров не меняются.",
          params_schema=_obj({"left_frame_id": frame_id, "right_frame_id": frame_id},
                             ["left_frame_id", "right_frame_id"]),
          executor=_merge_scenes, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False,
          destructive_effects=lambda p: (
              "Сцена правой ячейки теряет собственную цепочку действий и якоря (переезжают на "
              "левую), закадр склеивается; обратное разделение не вернёт прежнюю цепочку."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_ids("left_frame_id", "right_frame_id")),
          tags=("montage", "scene", "frames")),
        S(id="montage.split_scene", title_ru="Отделить кадр и правее в новую сцену",
          description_ru="Кадр и все правее становятся новой сценой (первый кадр сцены отделить нельзя).",
          params_schema=_obj({"frame_id": frame_id, "frame_ids": frame_ids}, ["frame_id"]),
          executor=_split_scene, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False,
          destructive_effects=lambda p: (
              "Ячейка делится: цепочки сцены дочерних кадров сбрасываются, закадр и якоря "
              "распределяются заново; прежнее состояние сцены не сохраняется."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_ids("frame_id")),
          tags=("montage", "scene", "frames")),
        S(id="montage.explode_scene", title_ru="Разделить сцену: каждый кадр - своя сцена",
          description_ru="Каждый видимый кадр ячейки становится отдельной ячейкой закадра.",
          params_schema=_obj({"frame_id": frame_id, "frame_ids": frame_ids}, ["frame_id"]),
          executor=_explode_scene, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False,
          destructive_effects=lambda p: (
              "Ячейка распадается на отдельные сцены: цепочки сцены кадров сбрасываются, закадр и "
              "якоря распределяются заново; прежнее состояние не сохраняется."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_ids("frame_id")),
          tags=("montage", "scene", "frames")),
        S(id="montage.delete_frame", title_ru="Удалить кадр",
          description_ru="Удаляет кадр; у головы сцены первый дочерний кадр повышается. Очередь "
                         "правок кадра чистится.",
          params_schema=_obj({"frame_id": frame_id}, ["frame_id"]),
          executor=_delete_frame, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False,
          destructive_effects=lambda p: (
              "Кадр удаляется из БД (промты, тексты, связи, операции очереди). Его картинки и видео "
              "переносятся в old/scenes/deleted-*; вернуть кадр программно нельзя."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_ids("frame_id")),
          tags=("montage", "frames")),
        S(id="montage.swap_slots", title_ru="Поменять местами два слота медиа",
          description_ru="Обмен слотов картинок или видео между любыми кадрами; слоты помечаются "
                         "устаревшими. Повторный вызов возвращает как было, поэтому повтор после "
                         "сбоя небезопасен.",
          params_schema=_obj({"kind": {"type": "string", "enum": ["image", "video"]},
                              "a_frame": _FRAME_NO, "a_shot": _SHOT,
                              "b_frame": _FRAME_NO, "b_shot": _SHOT},
                             ["kind", "a_frame", "a_shot", "b_frame", "b_shot"]),
          executor=_swap_slots, risk=RISK_LOCAL_SAFE, reversible=True, idempotent=False,
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("a_frame", "b_frame")),
          tags=("montage", "media")),
        S(id="montage.move_image", title_ru="Перенести картинку в другой слот",
          description_ru="Перенос в пустой слот или обмен, если цель занята; промты едут с файлами.",
          params_schema=_obj({"from_frame": _FRAME_NO, "from_shot": _SHOT, "to_frame": _FRAME_NO,
                              "to_shot": _SHOT, "from_slot": {"type": "string", "enum": ["", "parent"]},
                              "to_slot": {"type": "string", "enum": ["", "parent"]}},
                             ["from_frame", "from_shot", "to_frame", "to_shot"]),
          executor=_move_image, risk=RISK_LOCAL_SAFE, reversible=True, idempotent=False,
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("from_frame", "to_frame")),
          tags=("montage", "media")),
        S(id="montage.delete_image", title_ru="Удалить картинку слота",
          description_ru="Файл уходит в old/scenes, запись картинки удаляется, видео кадра "
                         "помечается устаревшим.",
          params_schema=_obj({"frame_number": _FRAME_NO, "shot": _SHOT,
                              "slot": {"type": "string", "enum": ["", "parent"]}},
                             ["frame_number"]),
          executor=_delete_image, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=True,
          destructive_effects=lambda p: (
              f"Картинка кадра {p['frame_number']} убирается с доски; файл остаётся в old/scenes, "
              "но программного возврата нет, видео помечается устаревшим."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("frame_number")),
          tags=("montage", "media")),
        S(id="montage.delete_video", title_ru="Удалить видео слота",
          description_ru="Файл клипа уходит в old/videos, запись видео удаляется.",
          params_schema=_obj({"frame_number": _FRAME_NO, "shot": _SHOT}, ["frame_number"]),
          executor=_delete_video, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=True,
          destructive_effects=lambda p: (
              f"Видео кадра {p['frame_number']} убирается с доски; файл остаётся в old/videos, но "
              "программного возврата нет."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("frame_number")),
          tags=("montage", "media")),
        S(id="montage.set_video_trim", title_ru="Задать обрезку видео",
          description_ru="Пишет start/end (секунды) обрезки слота видео в настройки доски; влияет на "
                         "следующую сборку. clear=true снимает обрезку.",
          params_schema=_obj({"frame_number": _FRAME_NO, "shot": _SHOT, "clear": {"type": "boolean"},
                              "start": {"type": "number", "minimum": 0, "maximum": 3600},
                              "end": {"type": "number", "minimum": 0, "maximum": 3600}},
                             ["frame_number", "shot"]),
          executor=_set_video_trim, risk=RISK_LOCAL_SAFE, idempotent=True,
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("frame_number"),
                         Precondition("trim_args", "Указан start или clear", _pre_trim_args)),
          tags=("montage", "media")),
        S(id="montage.list_ref_assets", title_ru="Готовые референсы проекта",
          description_ru="Персонажи и предметы проекта, которые можно приложить кадру.",
          params_schema=_obj({}, []), executor=_list_ref_assets, risk=RISK_READ, idempotent=True,
          preconditions=(_PRE_PROJECT,), tags=("montage", "refs")),
        S(id="montage.link_ref", title_ru="Приложить готовый реф кадру",
          description_ru="Привязывает файл из ref-assets к кадру; файл не копируется.",
          params_schema=_obj({"frame_number": _FRAME_NO,
                              "file": {"type": "string", "minLength": 1, "maxLength": 500},
                              "kind": {"type": "string", "maxLength": 40},
                              "name": {"type": "string", "maxLength": 200}},
                             ["frame_number", "file"]),
          executor=_link_ref, risk=RISK_LOCAL_SAFE, idempotent=False,
          preconditions=(_PRE_PROJECT, _pre_frame_numbers("frame_number")), tags=("montage", "refs")),
        S(id="montage.unlink_ref", title_ru="Убрать авто-реф персонажа или предмета",
          description_ru="Скрывает персонажа/предмет из рефов ячейки. Файлы проекта не трогает.",
          params_schema=_obj({"frame_number": _FRAME_NO,
                              "ref_id": {"type": "string", "minLength": 1, "maxLength": 100},
                              "kind": {"type": "string", "enum": ["character", "item"]}},
                             ["frame_number", "ref_id", "kind"]),
          executor=_unlink_ref, risk=RISK_LOCAL_SAFE, idempotent=True,
          preconditions=(_PRE_PROJECT, _pre_frame_numbers("frame_number")), tags=("montage", "refs")),
        S(id="montage.delete_manual_ref", title_ru="Удалить загруженный реф кадра",
          description_ru="Убирает ручной реф кадра; если файл был загружен для этого кадра, он "
                         "удаляется с диска (приложенный готовый ассет остаётся).",
          params_schema=_obj({"frame_number": _FRAME_NO,
                              "ref_id": {"type": "string", "minLength": 1, "maxLength": 100},
                              "kind": {"type": "string", "const": "manual"}},
                             ["frame_number", "ref_id", "kind"]),
          executor=_delete_manual_ref, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=True,
          destructive_effects=lambda p: (
              f"Реф {p['ref_id']} снимается с кадра {p['frame_number']}; если он был загружен "
              "вручную, файл удаляется с диска без корзины."),
          preconditions=(_PRE_PROJECT, _pre_frame_numbers("frame_number")), tags=("montage", "refs")),
        S(id="montage.recover_outsee", title_ru="Забрать правки из галереи Outsee",
          description_ru="Фоновый скан галереи Outsee (нужен CDP-Chrome) и замена кадров найденными "
                         "карточками. Генерации нет; занятая линия Outsee даёт отказ." + stop_note,
          params_schema=_obj({}, []), executor=_recover_outsee, poll=_poll_recover,
          stop=_stop_project, poll_interval_s=3.0, risk=RISK_DESTRUCTIVE, destructive=True,
          reversible=False, idempotent=False,
          destructive_effects=lambda p: (
              "Файлы кадров, для которых найдены готовые карточки в галерее Outsee, будут "
              "заменены; прежние файлы архивируются без программного отката."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _PRE_NO_RECOVER),
          tags=("montage", "outsee")),
        S(id="montage.run_montage", title_ru="Смонтировать видео (озвучка + сборка)",
          description_ru="Фон: синхронизация, повторный ASR существующей озвучки (локально) и сборка "
                         "FFmpeg. Прежний final.mp4 и ASR-артефакты удаляются, затем строятся заново; "
                         "TTS не вызывается (без готовой озвучки задача завершается ошибкой)."
                         + stop_note,
          params_schema=_obj({}, []), executor=_run_montage, poll=_poll_montage, stop=_stop_project,
          poll_interval_s=3.0, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=True,
          destructive_effects=lambda p: (
              "Будет удалён прежний final.mp4 и ASR-артефакты озвучки, затем ролик соберётся заново; "
              "сборка займёт время; платных вызовов нет."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _PRE_NO_MONTAGE, _PRE_NO_ALIGN),
          tags=("montage", "assemble")),
        S(id="montage.align_audio", title_ru="Разбор аудио (таймкоды кадров)",
          description_ru="Фон: локальный NeMo/ffmpeg разбирает озвучку на таймкоды кадров. Сборка "
                         "ролика не запускается (для неё montage.run_montage)." + stop_note,
          params_schema=_obj({
              "method": {"type": "string",
                         "enum": ["nemo_direct", "nemo_contiguous", "nemo_chunks", "silence", "nemo_auto"]},
              "force_asr": {"type": "boolean"}}, []),
          executor=_align_audio, poll=_poll_align, stop=_stop_project, poll_interval_s=2.0,
          risk=RISK_LOCAL_SAFE, idempotent=True,
          preconditions=(_PRE_PROJECT, _PRE_NO_MONTAGE, _PRE_NO_ALIGN), tags=("montage", "audio")),
        S(id="montage.set_assemble_params", title_ru="Настройки сборки ролика",
          description_ru="Хвост после озвучки, громкость музыки, субтитры, пропуск первых секунд. "
                         "Пишет meta.node_step_params.assemble для следующей сборки.",
          params_schema=_obj({
              "post_voiceover_tail_seconds": {"type": "number", "minimum": 0, "maximum": 120},
              "bgm_level": {"type": "integer", "minimum": 0, "maximum": 100},
              "subtitles_enabled": {"type": "boolean"}, "skip_intro_enabled": {"type": "boolean"},
              "skip_intro_seconds": {"type": "number", "minimum": 0, "maximum": 2}}, []),
          executor=_set_assemble_params, risk=RISK_LOCAL_SAFE, idempotent=True,
          preconditions=(_PRE_PROJECT, Precondition("assemble_args", "Указан хотя бы один параметр",
                                                    _pre_assemble_args)),
          tags=("montage", "assemble")),
        S(id="montage.get_cost_basis", title_ru="Модель оплаты проекта и очередь",
          description_ru="Текущие генераторы и разрешения, как их берёт регенерация панели, их цена "
                         "по каталогу Kie (или признак «цена неизвестна») и число операций в очереди. "
                         "Нужно для заявки в платных действиях.",
          params_schema=_obj({}, []), executor=_get_cost_basis, risk=RISK_READ, idempotent=True,
          preconditions=(_PRE_PROJECT,), tags=("montage", "cost")),
    ]


def register_more(registry: ActionRegistry) -> None:
    registry.register_many(_specs())
