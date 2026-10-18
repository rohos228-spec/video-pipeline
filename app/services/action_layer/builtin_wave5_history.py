from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.services.action_layer import store
from app.services.action_layer.builtin_group import _CONFIG_FIELDS, _get_project, _is_running
from app.services.action_layer.builtin_montage import _PID, _base
from app.services.action_layer.context import (
    ActionCallError, ActionContext, FileUpload, UploadRejected)
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE, RISK_READ, ActionRegistry, ActionSpec, Precondition)

SNAPSHOT_DIR = "action_layer_snapshots"
NOTE_SUFFIX = (" Прежнее значение сохраняется снимком в журнале (id возвращается в результате); "
               "откат: history.rollback.")


@dataclass(frozen=True)
class Handler:
    kind: str
    target_of: Callable[[dict], dict]
    read: Callable[[ActionContext, dict], Awaitable[Any]]
    write: Callable[[ActionContext, dict, Any], Awaitable[None]] | None
    restorable: Callable[[dict, Any], tuple[bool, str]]
    scope_project: bool = True


def _always(ok: bool, note: str = "") -> Callable[[dict, Any], tuple[bool, str]]:
    return lambda _t, _b: (ok, note)



async def _board_frames(ctx: ActionContext, pid: int) -> list[dict]:
    board = await ctx.call("GET", _base(pid))
    return list((board or {}).get("frames") or [])


async def _graph_frame(ctx: ActionContext, pid: int, uuid_: str) -> dict | None:
    graph = await ctx.call("GET", f"/db/projects/{pid}/graph")
    for fr in (graph or {}).get("frames") or []:
        if isinstance(fr, dict) and str(fr.get("uuid") or "") == str(uuid_):
            return fr
    return None



async def _vo_read(ctx, t):
    for fr in await _board_frames(ctx, t["project_id"]):
        if int(fr.get("frame_id") or -1) == int(t["frame_id"]):
            return str(fr.get("voiceover_text") or "")
    raise RuntimeError(f"кадр frame_id={t['frame_id']} не найден на доске")


async def _vo_write(ctx, t, value):
    await ctx.call("PATCH", f"{_base(t['project_id'])}/frames/{t['frame_id']}/voiceover",
                   json={"text": value})



_COVERAGE_FIELD = {"coverage_plan": "shot_plan", "coverage_action": "shot_action",
                   "coverage_angle": "shot_angle", "coverage_move": "shot_move",
                   "coverage_kind": "shot_kind", "coverage_stitch": "shot_stitch",
                   "coverage_light": "scene_lighting"}
_COVERAGE_RESTORABLE = frozenset({"coverage_plan", "coverage_action", "coverage_angle",
                                  "coverage_move"})


async def _cov_read(ctx, t):
    field = _COVERAGE_FIELD[t["type"]]
    for fr in await _board_frames(ctx, t["project_id"]):
        if int(fr.get("number") or 0) == int(t["frame_number"]):
            return fr.get(field)
    raise RuntimeError(f"кадра {t['frame_number']} нет на доске")


async def _cov_write(ctx, t, value):
    body = {"type": t["type"], "frame_number": t["frame_number"],
            t["type"].removeprefix("coverage_"): value}
    await ctx.call("POST", f"{_base(t['project_id'])}/coverage", json=body)


def _cov_restorable(t: dict, before: Any) -> tuple[bool, str]:
    if t["type"] not in _COVERAGE_RESTORABLE:
        return False, ("значение на доске нормализовано или производно от других полей: "
                       "записано для истории, точный откат не гарантируется")
    if not isinstance(before, str) or not before.strip():
        return False, "прежнее значение пустое: откат пустым значением сервер может не принять"
    return True, ""



_STRUCT_PREFIXES = ("scene_", "shot_", "anchor", "vo_scene")


async def _struct_read(ctx, t):
    for fr in await _board_frames(ctx, t["project_id"]):
        if int(fr.get("number") or 0) == int(t["frame_number"]):
            return {k: v for k, v in fr.items() if k.startswith(_STRUCT_PREFIXES)}
    raise RuntimeError(f"кадра {t['frame_number']} нет на доске")



async def _cell_read(ctx, t):
    fr = await _graph_frame(ctx, t["project_id"], t["parent_uuid"])
    if fr is None:
        raise RuntimeError(f"кадр {t['parent_uuid']!r} не найден")
    return str(fr.get("voiceover_text") or "")


async def _cell_write(ctx, t, value):
    await ctx.call("PATCH", f"/db/projects/{t['project_id']}/shot-menu/cell",
                   json={"parent_uuid": t["parent_uuid"], "voiceover": value})


_FIELD_COLUMN = {"img_prompt": "image_prompt", "video_prompt": "animation_prompt",
                 "vo": "voiceover_text"}
_FIELD_ATTR = {"action": "shot01_action", "characters": "characters",
               "stitch": "scene_transition", "scene": "id_scene"}
_FIELD_CAMERA = {"size": "крупность", "move": "движение"}


async def _field_read(ctx, t):
    fr = await _graph_frame(ctx, t["project_id"], t["frame_uuid"])
    if fr is None:
        raise RuntimeError(f"кадр {t['frame_uuid']!r} не найден")
    field = t["field"]
    attrs = fr.get("attrs") if isinstance(fr.get("attrs"), dict) else {}
    if field in _FIELD_COLUMN:
        return fr.get(_FIELD_COLUMN[field])
    if field in _FIELD_ATTR:
        return attrs.get(_FIELD_ATTR[field])
    if field in _FIELD_CAMERA:
        cs = attrs.get("camera_subdivide") if isinstance(attrs.get("camera_subdivide"), dict) else {}
        return cs.get(_FIELD_CAMERA[field])
    if field == "set":
        cs = attrs.get("camera_subdivide") if isinstance(attrs.get("camera_subdivide"), dict) else {}
        return {"place": attrs.get("place"), "camera_set": cs.get("набор")}
    raise RuntimeError(f"поле {field!r} не читается из графа")


async def _field_write(ctx, t, value):
    await ctx.call("PATCH", f"/db/projects/{t['project_id']}/shot-menu/shot-field",
                   json={"frame_uuid": t["frame_uuid"], "field": t["field"],
                         "value": "" if value is None else value})


def _field_restorable(t: dict, before: Any) -> tuple[bool, str]:
    if t["field"] == "set":
        return False, "поле «набор» хранится в двух местах: записано для истории, откат вручную"
    if before is not None and not isinstance(before, str):
        return False, "прежнее значение не строка (список): записано для истории, откат вручную"
    return True, ""



async def _prompt_read(ctx, t):
    from app.services.action_layer.builtin_wave3 import _read_prompt

    data = await _read_prompt(ctx, t)
    if data is None:
        raise RuntimeError("промт не найден")
    return str(data.get("content") or "")


async def _prompt_write(ctx, t, value):
    from app.services.action_layer.builtin_wave3 import _prompt_path

    await ctx.call("PUT", _prompt_path(t), json={"content": value})



_FILE_SLOTS = {
    "voice": ("montage_voice_path", "upload-voice", "voice"),
    "music": ("bgm_path", "upload-music", "music"),
}


def _data_root() -> Path:
    from app.settings import settings

    return Path(settings.data_dir).resolve()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


async def _file_read(ctx, t):
    meta_key = _FILE_SLOTS[t["slot"]][0]
    proj = await _get_project(ctx, t["project_id"])
    raw = (proj.get("meta") or {}).get(meta_key)
    if not raw:
        return {"exists": False}
    path = Path(str(raw))
    try:
        real = path.resolve(strict=True)
    except (OSError, RuntimeError):
        return {"exists": False, "meta_path": str(raw), "missing_on_disk": True}
    root = _data_root()
    if root not in real.parents or not real.is_file():
        return {"exists": False, "meta_path": str(raw), "outside_data_dir": True}
    return {"exists": True, "name": real.name, "size": real.stat().st_size,
            "sha256": _sha256_file(real), "real_path": str(real)}


def _backup_file(state: dict) -> dict:
    src = Path(state["real_path"])
    folder = _data_root() / SNAPSHOT_DIR / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / src.name
    shutil.copy2(src, dst)
    if _sha256_file(dst) != state["sha256"]:
        raise RuntimeError("копия прежнего файла не совпала с оригиналом по sha256")
    return {"exists": True, "name": src.name, "size": state["size"], "sha256": state["sha256"],
            "backup": dst.relative_to(_data_root()).as_posix()}


def _public_file_state(state: dict) -> dict:
    return {k: v for k, v in state.items() if k != "real_path"}


async def _file_write(ctx, t, value):
    if not isinstance(value, dict) or not value.get("backup"):
        raise RuntimeError("в снимке нет копии файла")
    _meta_key, endpoint, policy = _FILE_SLOTS[t["slot"]]
    up = FileUpload.from_data_path(policy, value["backup"])
    if hashlib.sha256(up.content).hexdigest() != value.get("sha256"):
        raise UploadRejected("копия файла изменена после снимка (sha256): откат отклонён")
    await ctx.call("POST", f"{_base(t['project_id'])}/{endpoint}", files={"file": up},
                   timeout=300.0)


def _file_restorable(_t: dict, before: Any) -> tuple[bool, str]:
    if isinstance(before, dict) and before.get("backup"):
        return True, ""
    return False, "прежнего файла не было: удалить файл эндпоинтом нельзя, откат невозможен"


async def _variant_read(ctx, t):
    proj = await _get_project(ctx, t["project_id"])
    slots = (proj.get("meta") or {}).get("prompt_slot_variants") or {}
    node = slots.get(t["node_key"]) if isinstance(slots, dict) else None
    if not isinstance(node, dict):
        return None
    return node.get(t["slot_id"])


async def _variant_write(ctx, t, value):
    await ctx.call("PATCH", f"/projects/{t['project_id']}", json={"meta": {
        "prompt_slot_variants": {t["node_key"]: {t["slot_id"]: value}}}})


async def _config_read(ctx, t):
    data = await ctx.call(
        "GET", f"/projects/{t['project_id']}/gpt-operator/{t['node_key']}/resolve")
    cfg = (data or {}).get("config") if isinstance(data, dict) else None
    cfg = cfg if isinstance(cfg, dict) else {}
    return {key: cfg.get(key) for key in _CONFIG_FIELDS}


async def _config_write(ctx, t, value):
    if not isinstance(value, dict):
        raise RuntimeError("снимок конфига ноды повреждён")
    body = {key: value[key] for key in _CONFIG_FIELDS if key in value and value[key] is not None}
    await ctx.call("PATCH", f"/projects/{t['project_id']}/gpt-operator/{t['node_key']}", json=body)


async def _disabled_read(ctx, t):
    proj = await _get_project(ctx, t["project_id"])
    cur = [str(item) for item in ((proj.get("meta") or {}).get("disabled_nodes") or [])]
    return str(t["node_key"]) in cur


async def _disabled_write(ctx, t, value):
    proj = await _get_project(ctx, t["project_id"])
    cur = [str(item) for item in ((proj.get("meta") or {}).get("disabled_nodes") or [])]
    key = str(t["node_key"])
    updated = cur if (value and key in cur) else ([*cur, key] if value else [item for item in cur if item != key])
    await ctx.call("PATCH", f"/projects/{t['project_id']}", json={"meta": {"disabled_nodes": updated}})


async def _assemble_read(ctx, t):
    proj = await _get_project(ctx, t["project_id"])
    nsp = (proj.get("meta") or {}).get("node_step_params") or {}
    bucket = nsp.get("assemble") if isinstance(nsp, dict) else None
    return dict(bucket) if isinstance(bucket, dict) else {}


async def _assemble_write(ctx, t, value):
    proj = await _get_project(ctx, t["project_id"])
    nsp = (proj.get("meta") or {}).get("node_step_params") or {}
    current = nsp.get("assemble") if isinstance(nsp, dict) else None
    current = current if isinstance(current, dict) else {}
    wanted = dict(value) if isinstance(value, dict) else {}
    patch = dict(wanted)
    for key in current:
        if key not in wanted:
            patch[key] = None
    await ctx.call("PATCH", f"/projects/{t['project_id']}", json={"meta": {
        "node_step_params": {"assemble": patch}}})


HANDLERS: dict[str, Handler] = {
    "montage.set_voiceover": Handler(
        "voiceover", lambda p: {"project_id": p["project_id"], "frame_id": p["frame_id"]},
        _vo_read, _vo_write, _always(True)),
    "montage.set_coverage": Handler(
        "coverage", lambda p: {"project_id": p["project_id"], "frame_number": p["frame_number"],
                               "type": p["type"]},
        _cov_read, _cov_write, _cov_restorable),
    "montage.set_anchors": Handler(
        "scene_structure", lambda p: {"project_id": p["project_id"],
                                      "frame_number": p["frame_number"]},
        _struct_read, None, _always(False, "структурная правка: шоты пересобираются, номера кадров "
                                           "могут сдвинуться; прежнее состояние записано для истории")),
    "montage.set_scene_chain": Handler(
        "scene_structure", lambda p: {"project_id": p["project_id"],
                                      "frame_number": p["frame_number"]},
        _struct_read, None, _always(False, "структурная правка: шоты пересобираются; прежнее "
                                           "состояние записано для истории")),
    "shot.edit_cell_voiceover": Handler(
        "cell_voiceover", lambda p: {"project_id": p["project_id"],
                                     "parent_uuid": p["parent_uuid"]},
        _cell_read, _cell_write, _always(True)),
    "shot.edit_field": Handler(
        "shot_field", lambda p: {"project_id": p["project_id"], "frame_uuid": p["frame_uuid"],
                                 "field": p["field"]},
        _field_read, _field_write, _field_restorable),
    "montage.upload_voice": Handler(
        "file_voice", lambda p: {"project_id": p["project_id"], "slot": "voice"},
        _file_read, _file_write, _file_restorable),
    "montage.upload_music": Handler(
        "file_music", lambda p: {"project_id": p["project_id"], "slot": "music"},
        _file_read, _file_write, _file_restorable),
    "prompt.save_master": Handler(
        "master_prompt", lambda p: {"step_code": p["step_code"], "name": p["name"]},
        _prompt_read, _prompt_write, _always(True), scope_project=False),
    "node.set_prompt_variant": Handler(
        "prompt_slot", lambda p: {"project_id": p["project_id"], "node_key": p["node_key"],
                                  "slot_id": p.get("slot_id") or "main"},
        _variant_read, _variant_write, _always(True)),
    "node.set_config": Handler(
        "node_config", lambda p: {"project_id": p["project_id"], "node_key": p["node_key"]},
        _config_read, _config_write, _always(True)),
    "node.set_disabled": Handler(
        "node_disabled", lambda p: {"project_id": p["project_id"], "node_key": p["node_key"]},
        _disabled_read, _disabled_write, _always(True)),
    "montage.set_assemble_params": Handler(
        "assemble_params", lambda p: {"project_id": p["project_id"]},
        _assemble_read, _assemble_write, _always(True)),
}

_FILE_KINDS = frozenset({"file_voice", "file_music"})


def _compare_value(kind: str, value: Any) -> Any:
    if kind in _FILE_KINDS and isinstance(value, dict):
        return {"exists": value.get("exists"), "sha256": value.get("sha256")}
    return value


def _wrap_executor(action_id: str, h: Handler, orig):
    async def executor(ctx: ActionContext, params: dict) -> dict:
        if not isinstance(ctx, ActionContext):
            return await orig(ctx, params)
        target = h.target_of(params)
        try:
            before = await h.read(ctx, target)
        except Exception as exc:
            raise RuntimeError(f"не удалось прочитать прежнее значение, правка не начата: "
                               f"{type(exc).__name__}: {exc}") from exc
        stored_before = before
        if h.kind in _FILE_KINDS and isinstance(before, dict) and before.get("exists"):
            stored_before = _backup_file(before)
        elif isinstance(before, dict):
            stored_before = _public_file_state(before)
        result = await orig(ctx, params)
        try:
            after = await h.read(ctx, target)
        except Exception:
            after = None
        if isinstance(after, dict):
            after = _public_file_state(after)
        ok, note = h.restorable(target, stored_before)
        pid = target.get("project_id") if h.scope_project else None
        sid = await store.save_snapshot(
            plan_id=ctx.plan_id or "", step_index=ctx.step_index, action_id=action_id,
            project_id=pid, kind=h.kind, target=target, before=stored_before, after=after,
            restorable=bool(ok and h.write is not None), note=note, actor=ctx.operator)
        out = dict(result) if isinstance(result, dict) else {"result": result}
        out["snapshot_id"] = sid
        out["rollback_possible"] = bool(ok and h.write is not None)
        return out
    return executor


def wrap_catalog(registry: ActionRegistry) -> list[str]:
    done: list[str] = []
    for action_id, h in HANDLERS.items():
        spec = registry.find(action_id)
        if spec is None:
            continue
        desc = spec.description_ru
        for old in (" Прежний текст не сохраняется.",
                    " Прежняя версия остаётся в истории промта, но восстановить её из каталога нельзя."):
            desc = desc.replace(old, "")
        effects = spec.destructive_effects
        if action_id == "prompt.save_master" and effects is not None:
            inner = effects
            effects = lambda p, _inner=inner: (
                _inner(p).replace("; прежняя версия остаётся только в истории версий Studio.", "")
                + "; прежний текст сохраняется снимком слоя, откат: history.rollback.")
        registry.unregister(action_id)
        registry.register(replace(spec, executor=_wrap_executor(action_id, h, spec.executor),
                                  description_ru=desc.rstrip() + NOTE_SUFFIX,
                                  destructive_effects=effects))
        done.append(action_id)
    return done



def _snapshot_json(row) -> dict:
    return {"snapshot_id": row.id, "created_at": row.created_at.isoformat() if row.created_at else None,
            "action_id": row.action_id, "plan_id": row.plan_id, "project_id": row.project_id,
            "kind": row.kind, "target": row.target, "before": _clip(row.before),
            "after": _clip(row.after), "restorable": bool(row.restorable), "note": row.note,
            "restored_at": row.restored_at.isoformat() if row.restored_at else None}


def _clip(value: Any, limit: int = 4000) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    return value


async def _list_snapshots(ctx: ActionContext, params: dict) -> dict:
    rows = await store.list_snapshots(params.get("project_id"), limit=params.get("limit", 20))
    return {"count": len(rows), "snapshots": [_snapshot_json(r) for r in rows]}


async def _pre_rollback(ctx: ActionContext, params: dict) -> str | None:
    row = await store.get_snapshot(params["snapshot_id"])
    if row is None:
        return f"снимка {params['snapshot_id']} нет"
    if row.restored_at is not None:
        return "этот снимок уже откатан"
    if not row.restorable:
        return row.note or "для этого снимка автоматический откат невозможен"
    h = _handler_for_kind(row.kind)
    if h is None or h.write is None:
        return f"откат вида {row.kind!r} не поддерживается"
    if row.project_id is not None:
        proj = await _get_project(ctx, row.project_id)
        if _is_running(proj.get("status")):
            return f"в проекте выполняется шаг (статус {proj.get('status')})"
    return None


def _handler_for_kind(kind: str) -> Handler | None:
    for h in HANDLERS.values():
        if h.kind == kind:
            return h
    return None


async def _rollback(ctx: ActionContext, params: dict) -> dict:
    row = await store.get_snapshot(params["snapshot_id"])
    if row is None or row.restored_at is not None or not row.restorable:
        raise RuntimeError("снимок недоступен для отката")
    h = _handler_for_kind(row.kind)
    if h is None or h.write is None:
        raise RuntimeError(f"откат вида {row.kind!r} не поддерживается")
    target = dict(row.target or {})
    current_full = await h.read(ctx, target)
    current = _public_file_state(current_full) if isinstance(current_full, dict) else current_full
    if not params.get("force") and _compare_value(h.kind, current) != _compare_value(h.kind, row.after):
        if h.kind == "master_prompt":
            raise ActionCallError(
                409, "текст мастер-промта изменился после снимка: откат отклонён")
        raise RuntimeError("значение изменилось после правки (его поправили ещё раз): откат "
                           "перезатёр бы чужую правку; повторите с force=true, если это нужно")
    saved = current
    if h.kind in _FILE_KINDS and isinstance(current_full, dict) and current_full.get("exists"):
        saved = _backup_file(current_full)
    await h.write(ctx, target, row.before)
    reverse_id = await store.save_snapshot(
        plan_id=ctx.plan_id or "", step_index=ctx.step_index, action_id="history.rollback",
        project_id=row.project_id, kind=row.kind, target=target, before=saved, after=row.before,
        restorable=h.write is not None and h.restorable(target, saved)[0],
        note=f"откат снимка {row.id}", actor=ctx.operator)
    await store.mark_snapshot_restored(row.id, ctx.operator, plan_id=ctx.plan_id or "")
    return {"restored_snapshot_id": row.id, "action_id": row.action_id, "kind": row.kind,
            "reverse_snapshot_id": reverse_id}


def history_specs() -> list[ActionSpec]:
    S = ActionSpec
    return [
        S(id="history.list_snapshots", title_ru="Показать прежние значения (снимки правок)",
          description_ru="Список снимков, которые слой сохранил перед правками закадра, покрытия, "
                         "полей шота, файлов озвучки и музыки, мастер-промтов: что было, что стало "
                         "и можно ли откатить. Читает журнал слоя, проект не меняет.",
          params_schema={"type": "object", "properties": {
              "project_id": _PID, "limit": {"type": "integer", "minimum": 1, "maximum": 100}},
              "required": [], "additionalProperties": False},
          executor=_list_snapshots, risk=RISK_READ, idempotent=True, scope="global",
          tags=("history", "rollback")),
        S(id="history.rollback", title_ru="Откатить правку по журналу",
          description_ru="Возвращает прежнее значение из снимка тем же эндпоинтом, которым шла "
                         "правка. Если значение уже поменяли после правки, откат отклоняется "
                         "(текущее содержимое сверяется с after снимка и отвечает 409; "
                         "force=true перезапишет). Сам откат тоже оставляет снимок, поэтому обратим. "
                         "Якоря, цепочка сцены и поле «набор» автоматически не откатываются.",
          params_schema={"type": "object", "properties": {
              "snapshot_id": {"type": "integer", "minimum": 1},
              "force": {"type": "boolean"}},
              "required": ["snapshot_id"], "additionalProperties": False},
          executor=_rollback, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False, scope="global",
          destructive_effects=lambda p: (
              f"Текущее значение по снимку {p['snapshot_id']} будет заменено прежним"
              + ("; проверка «значение не менялось после правки» отключена (force)."
                 if p.get("force") else "; текущее значение сохранится новым снимком.")),
          preconditions=(Precondition("snapshot_restorable",
                                      "Снимок существует, не откатан и поддерживает откат",
                                      _pre_rollback),),
          tags=("history", "rollback")),
    ]
