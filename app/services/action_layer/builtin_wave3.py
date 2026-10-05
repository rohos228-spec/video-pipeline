"""Каталог, волна 3: загрузки файлов, пауза/продолжение, мастер-промты, оценка прогона ноды.

Загрузки идут через ``ctx.call(files=...)``: файл берётся из пути внутри DATA_DIR или из
base64 в параметрах, размер и тип проверяются (см. ``context.FileUpload``).

Продолжение проекта (``project.resume``, ``project.continue_pipeline``) может запустить
платный шаг воркера: стоимость такого запуска из параметров не вычислить, поэтому оценка
явно неизвестна и план нельзя подтвердить (fail-closed).

``node.run_step`` здесь пересоздаётся с оценкой для текстовых нод script_frames_qc
(``node_run_cost``): исходное определение в ``builtin_group`` остаётся для остальных нод
без изменений и заменяется при регистрации волны.
"""
from __future__ import annotations

import hashlib

from app.services.action_layer import node_run_cost as nrc
from app.services.action_layer.builtin_group import (
    _NODE_KEY, _PRE_IDLE, _PRE_NODE, _PRE_NODE_GPT, _PRE_NOT_MANAGED, _PRE_NO_MONTAGE,
    _get_project, _node_run_effects, _node_run_poll, _stop_project)
from app.services.action_layer.builtin_montage import _PID, _PRE_NO_APPLY, _PRE_PROJECT, _base
from app.services.action_layer.builtin_montage_more import (
    _FRAME_NO, _PRE_NO_ALIGN, _SHOT, _pre_frame_numbers)
from app.services.action_layer.context import (
    ActionCallError, ActionContext, source_to_upload, validate_source_params)
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE, RISK_LOCAL_SAFE, RISK_READ, ActionRegistry, ActionSpec, CostEstimate,
    JobState, Precondition)

_STEP_CODE = {"type": "string", "minLength": 1, "maxLength": 40, "pattern": r"^[a-z0-9_]+$"}
_PROMPT_NAME = {"type": "string", "minLength": 1, "maxLength": 120,
                "pattern": r"^[0-9A-Za-zА-Яа-яЁё _.\-]+$"}
_SRC_PROPS = {
    "source_path": {"type": "string", "minLength": 1, "maxLength": 500,
                    "description": "Путь к файлу на сервере внутри DATA_DIR"},
    "data_base64": {"type": "string", "minLength": 1, "maxLength": 700000,
                    "description": "Содержимое файла в base64 (до 512 КБ после декодирования)"},
    "filename": {"type": "string", "minLength": 1, "maxLength": 120,
                 "description": "Имя файла с расширением (обязательно с data_base64)"},
}
_BASIS_INTS = {
    "min_llm_requests": {"type": "integer", "minimum": 1, "maximum": 100000},
    "max_llm_requests": {"type": "integer", "minimum": 1, "maximum": 100000},
    "max_input_tokens": {"type": "integer", "minimum": 1, "maximum": 2000000},
    "max_output_tokens": {"type": "integer", "minimum": 1, "maximum": 2000000},
}


def _obj(props: dict, required: list[str], *, project: bool = True) -> dict:
    head = {"project_id": _PID} if project else {}
    return {"type": "object", "properties": {**head, **props},
            "required": [*(["project_id"] if project else []), *required],
            "additionalProperties": False}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- загрузки ------------------------------------------------------------------

def _pre_source(kind: str) -> Precondition:
    async def check(ctx, params):
        return validate_source_params(params, kind)
    return Precondition("upload_source", "Файл допустим: путь внутри DATA_DIR или base64, "
                        "тип, размер и содержимое проверены", check)


async def _post_file(ctx: ActionContext, kind: str, path: str, params: dict, *,
                     query: dict | None = None) -> tuple[dict, dict]:
    up = source_to_upload(kind, params)
    data = await ctx.call("POST", path, params=query, files={"file": up}, timeout=300.0)
    info = {"uploaded_name": up.filename, "uploaded_bytes": len(up.content),
            "uploaded_sha256": _sha(up.content)}
    return (data if isinstance(data, dict) else {}), info


async def _upload_voice(ctx, params):
    data, info = await _post_file(
        ctx, "voice", f"{_base(params['project_id'])}/upload-voice", params)
    return {"ok": bool(data.get("ok")), "path": data.get("path"), **info}


async def _upload_music(ctx, params):
    data, info = await _post_file(
        ctx, "music", f"{_base(params['project_id'])}/upload-music", params)
    return {"ok": bool(data.get("ok")), "path": data.get("path"), **info}


async def _upload_image(ctx, params):
    data, info = await _post_file(
        ctx, "scene_image", f"{_base(params['project_id'])}/upload-image", params,
        query={"frame_number": params["frame_number"], "shot": params.get("shot", 1),
               "slot": params.get("slot", "")})
    return {"ok": bool(data.get("ok")), "frame_number": params["frame_number"],
            "path": data.get("path"), **info}


async def _upload_video(ctx, params):
    data, info = await _post_file(
        ctx, "scene_video", f"{_base(params['project_id'])}/upload-video", params,
        query={"frame_number": params["frame_number"], "shot": params.get("shot", 1)})
    return {"ok": bool(data.get("ok")), "frame_number": params["frame_number"],
            "path": data.get("path"), **info}


async def _upload_ref(ctx, params):
    data, info = await _post_file(
        ctx, "ref_image", f"{_base(params['project_id'])}/refs", params,
        query={"frame_number": params["frame_number"], "kind": params.get("kind", "other"),
               "name": params["name"]})
    return {"ok": bool(data.get("ok")), "frame_number": params["frame_number"],
            "refs": data.get("refs"), **info}


def _node_dir(project_id: int, node_key: str) -> str:
    return f"/projects/{project_id}/excel-gpt/{node_key}"


async def _node_key_safe(ctx, params):
    key = params["node_key"]
    if ".." in key or key.startswith((".", ":")) or "/" in key or "\\" in key:
        return "недопустимый node_key для папки загрузок"
    return None


_PRE_NODE_KEY_SAFE = Precondition("node_key_safe", "node_key безопасен как имя папки",
                                  _node_key_safe)


async def _upload_node_file(ctx, params):
    data, info = await _post_file(
        ctx, "node_file", f"{_node_dir(params['project_id'], params['node_key'])}/upload", params)
    return {"ok": bool(data.get("ok")), "file_name": data.get("fileName"),
            "replaced_xlsx": bool(data.get("replacedXlsx")),
            "used_as_check_agent": bool(data.get("usedAsCheckAgent")),
            "uploaded_names": data.get("uploadedFileNames"), **info}


async def _upload_check_agent(ctx, params):
    data, info = await _post_file(
        ctx, "check_agent",
        f"/projects/{params['project_id']}/gpt-operator/{params['node_key']}/check-agent",
        params)
    return {"ok": bool(data.get("ok")), "file_name": data.get("fileName"),
            "chars": data.get("chars"), **info}


async def _clear_check_agent(ctx, params):
    data = await ctx.call("DELETE", f"/projects/{params['project_id']}/gpt-operator/"
                                    f"{params['node_key']}/check-agent")
    return {"ok": bool((data or {}).get("ok"))}


# --- пауза, продолжение, перезагрузка xlsx ----------------------------------------

async def _project_paused(ctx, params):
    proj = await _get_project(ctx, params["project_id"])
    if proj.get("status") != "paused":
        return f"проект не на паузе (статус {proj.get('status')})"
    return None


async def _pause(ctx, params):
    data = await ctx.call("POST", f"/projects/{params['project_id']}/pause")
    return {"status": (data or {}).get("status")}


async def _resume(ctx, params):
    data = await ctx.call("POST", f"/projects/{params['project_id']}/resume")
    return {"status": (data or {}).get("status"), "resumed": True}


async def _continue(ctx, params):
    data = await ctx.call("POST", f"/projects/{params['project_id']}/continue")
    data = data if isinstance(data, dict) else {}
    return {"action": data.get("action"), "status": data.get("status"),
            "advanced": data.get("advanced"), "cleared": data.get("cleared")}


async def _poll_project_idle(ctx, params, started) -> JobState:
    from app.services.action_layer.builtin_group import _is_running

    proj = await _get_project(ctx, params["project_id"])
    status = proj.get("status")
    if _is_running(status):
        return JobState(done=False, note=f"статус проекта {status}")
    return JobState(done=True, ok=True, note=f"статус проекта {status}",
                    result={"final_status": status})


def _unknown_start_cost(_p: dict) -> CostEstimate:
    return CostEstimate.unknown(
        "после запуска воркер может начать любой шаг конвейера (картинки, видео, LLM): "
        "какой именно и сколько он стоит, из параметров не определить")


async def _reload_xlsx(ctx, params):
    data = await ctx.call("POST", f"/projects/{params['project_id']}/xlsx/reload")
    return {"status": (data or {}).get("status"), "reloaded": True}


async def _set_send_to_main_pc(ctx, params):
    pid = params["project_id"]
    detail = await _get_project(ctx, pid)
    nsp = {k: (dict(v) if isinstance(v, dict) else v)
           for k, v in ((detail.get("meta") or {}).get("node_step_params") or {}).items()}
    bucket = dict(nsp.get("assemble") or {})
    bucket["send_to_main_pc"] = bool(params["enabled"])
    nsp["assemble"] = bucket
    await ctx.call("PATCH", f"/projects/{pid}", json={"meta": {"node_step_params": nsp}})
    return {"send_to_main_pc": bool(params["enabled"])}


async def _fleet_enabled_for_flag(ctx, params):
    from app.settings import settings

    if params["enabled"] and not settings.fleet_enabled:
        return "FLEET_ENABLED выключен: флаг ничего не запустит"
    return None


# --- мастер-промты (общие файлы всех проектов) ---------------------------------------

def _prompt_path(params: dict) -> str:
    return f"/prompt-files/{params['step_code']}/{params['name']}"


def _name_ok(params: dict) -> str | None:
    from app.services.prompt_library import is_valid_prompt_name

    if not is_valid_prompt_name(params["name"]):
        return "недопустимое имя промта"
    return None


def _group_query(params: dict) -> dict | None:
    """Промты ноды группы пишутся в шаблон группы, а не в общий excel_gpt: читать оттуда же."""
    from app.services.prompt_library import is_excel_gpt_prompt_step, is_script_frames_qc_prompt

    if is_excel_gpt_prompt_step(params["step_code"]) and is_script_frames_qc_prompt(params["name"]):
        return {"group_id": "script_frames_qc"}
    return None


async def _read_prompt(ctx, params) -> dict | None:
    try:
        return await ctx.call("GET", f"{_prompt_path(params)}/content",
                              params=_group_query(params))
    except ActionCallError as exc:
        if exc.status == 404:
            return None
        raise


async def _pre_prompt_exists(ctx, params):
    reason = _name_ok(params)
    if reason:
        return reason
    if await _read_prompt(ctx, params) is None:
        return f"промта {params['step_code']}/{params['name']} нет (шаг или имя неверны)"
    return None


async def _pre_prompt_free(ctx, params):
    reason = _name_ok(params)
    if reason:
        return reason
    if await _read_prompt(ctx, params) is not None:
        return "промт с таким именем уже есть: перезапись только через prompt.save_master"
    return None


async def _pre_not_default(ctx, params):
    if params["name"] == "default":
        return "default удалять нельзя"
    return None


async def _pre_content_nonblank(ctx, params):
    if not params["content"].strip():
        return "текст промта пустой"
    return None


def _digest(text: str) -> dict:
    raw = text.encode("utf-8")
    return {"chars": len(text), "sha256": _sha(raw)}


async def _get_master(ctx, params):
    data = await _read_prompt(ctx, params)
    if data is None:
        raise RuntimeError("промт не найден")
    text = str(data.get("content") or "")
    return {**_digest(text), "content": text[:20000], "truncated": len(text) > 20000,
            "modified": data.get("modified")}


async def _save_master(ctx, params):
    old = await _read_prompt(ctx, params)
    if old is None:
        raise RuntimeError("промт не найден")
    prev = _digest(str(old.get("content") or ""))
    await ctx.call("PUT", _prompt_path(params), json={"content": params["content"]})
    new = _digest(params["content"])
    return {"previous_sha256": prev["sha256"], "previous_chars": prev["chars"],
            "new_sha256": new["sha256"], "new_chars": new["chars"],
            "unchanged": prev["sha256"] == new["sha256"], "scope": "global",
            "history_note": "прежний текст сохраняется в истории версий промта (Studio)"}


async def _create_master(ctx, params):
    await ctx.call("PUT", _prompt_path(params), json={"content": params["content"]})
    return {**{f"new_{k}": v for k, v in _digest(params["content"]).items()}, "scope": "global"}


async def _delete_master(ctx, params):
    old = await _read_prompt(ctx, params)
    if old is None:
        raise RuntimeError("промт не найден")
    prev = _digest(str(old.get("content") or ""))
    await ctx.call("DELETE", _prompt_path(params))
    return {"removed": True, "previous_sha256": prev["sha256"], "previous_chars": prev["chars"],
            "scope": "global"}


# --- стоимость прогона ноды ----------------------------------------------------------

_DECL_KEYS = ("min_llm_requests", "max_llm_requests", "max_input_tokens", "max_output_tokens")


async def _load_basis(ctx: ActionContext, params: dict):
    """RunBasis по реальным данным проекта или текст причины, почему оценить нельзя."""
    from app.services.prompt_library import (
        resolve_project_prompt_with_source, resolve_script_frames_qc_prompt_path)

    key = params["node_key"]
    expected = nrc.expected_prompt(key)
    if expected is None:
        return ("оценка есть только для нод Каркас, Площадка, Шоты, QC группы script_frames_qc")
    pid = params["project_id"]
    proj = await _get_project(ctx, pid)
    variant, _src = resolve_project_prompt_with_source(
        proj.get("prompt_overrides") or {}, "excel_gpt", meta=proj.get("meta") or {},
        node_key=key, slot_id="main")
    if str(variant or "").strip().removesuffix(".md").strip() != expected:
        return (f"выбран промт {variant!r}, оценка рассчитана только для {expected!r}: "
                "число вызовов других промтов из кода не определить")
    path = resolve_script_frames_qc_prompt_path(expected)
    if not path.is_file():
        return f"файл промта {expected} не найден"
    prompt_chars = len(path.read_text(encoding="utf-8"))
    try:
        text = await ctx.call("GET", f"/prompt-studio/projects/{pid}/gpt-text/excel_gpt")
    except ActionCallError as exc:
        return f"не удалось прочитать сопроводительный текст проекта (HTTP {exc.status})"
    user_chars = len(str((text or {}).get("text") or ""))
    graph = await ctx.call("GET", f"/db/projects/{pid}/graph")
    frames = (graph or {}).get("frames")
    if not isinstance(frames, list):
        return "граф проекта не вернул кадры"
    project_chars = sum(len(str(proj.get(k) or "")) for k in ("topic", "general_plan",
                                                                "script_text"))
    basis = nrc.compute_run_basis(
        node_key=key, frames=frames, entities=list((graph or {}).get("entities") or []),
        prompt_chars=prompt_chars, user_text_chars=user_chars,
        project_text_chars=project_chars)
    if basis is None:
        return "нет кадров с закадром: прогон ничего не запишет, запускать нечего"
    return basis


def _declared_vs_basis(params: dict, basis) -> str | None:
    if any(params.get(k) is None for k in _DECL_KEYS):
        return ("заявите стоимость: значения из node.get_run_cost_basis "
                f"({', '.join(_DECL_KEYS)})")
    if params["min_llm_requests"] > params["max_llm_requests"]:
        return "min_llm_requests больше max_llm_requests"
    for key, actual in (("max_llm_requests", basis.max_requests),
                        ("max_input_tokens", basis.max_input_tokens),
                        ("max_output_tokens", basis.max_output_tokens)):
        if params[key] < actual:
            return f"{key}: заявлено {params[key]}, по данным проекта нужно не меньше {actual}"
    return None


async def _pre_run_cost(ctx, params):
    key = params["node_key"]
    if nrc.is_report_node(key):
        return None
    basis = await _load_basis(ctx, params)
    if isinstance(basis, str):
        return basis
    return _declared_vs_basis(params, basis)


async def _get_run_cost_basis(ctx, params):
    key = params["node_key"]
    if nrc.is_report_node(key):
        return {"free": True, "note": "код-нода отчёта, LLM не вызывает"}
    basis = await _load_basis(ctx, params)
    if isinstance(basis, str):
        return {"estimable": False, "reason": basis}
    priced = nrc.price_request_credits(basis.max_input_tokens, basis.max_output_tokens)
    out = {"estimable": True, "node_kind": basis.node_kind, "packs": basis.packs,
           "units": basis.units, "rows": basis.rows, "declare": basis.declare(),
           "parts": basis.parts, "uncalibrated": True}
    if priced is not None:
        credits, why = priced
        out["upper_bound_credits"] = round(credits * basis.max_requests, 2)
        out["lower_bound_credits"] = round(credits * basis.min_requests, 2)
        out["price_basis"] = why
    else:
        out["price_basis"] = "цена модели в снимке Vibecode не найдена: оценка неизвестна"
    return out


def _estimate_node_run(params: dict) -> CostEstimate:
    key = str(params.get("node_key") or "")
    if nrc.is_report_node(key):
        return CostEstimate(0.0, 0.0, "код-нода отчёта, LLM не вызывает")
    if nrc.node_kind(key) is None:
        return CostEstimate.unknown(
            "оценка есть только для нод Каркас, Площадка, Шоты, QC группы script_frames_qc; "
            "число вызовов других нод из кода не определено")
    if any(params.get(k) is None for k in _DECL_KEYS):
        return CostEstimate.unknown(
            "стоимость не заявлена: получите значения у node.get_run_cost_basis и передайте "
            f"{', '.join(_DECL_KEYS)}")
    if params["min_llm_requests"] > params["max_llm_requests"]:
        return CostEstimate.unknown("min_llm_requests больше max_llm_requests")
    priced = nrc.price_request_credits(params["max_input_tokens"], params["max_output_tokens"])
    if priced is None:
        return CostEstimate.unknown("цена модели в снимке Vibecode не найдена")
    unit, why = priced
    return CostEstimate(
        round(unit * params["min_llm_requests"], 2), round(unit * params["max_llm_requests"], 2),
        f"{nrc.UNCALIBRATED}: до {params['max_llm_requests']} запросов ({why}), на запрос до "
        f"{params['max_input_tokens']} токенов входа и {params['max_output_tokens']} токенов "
        "выхода (выход допущен, max_tokens не задаётся; неуспешные попытки транспорта не "
        "тарифицируются - не проверено); нижняя граница - по одному запросу на пачку; "
        "фактический расход действие не возвращает")


async def _run_node_step(ctx, params):
    if not nrc.is_report_node(params["node_key"]):
        basis = await _load_basis(ctx, params)
        if isinstance(basis, str):
            raise RuntimeError(f"оценка стоимости не применима: {basis}")
        reason = _declared_vs_basis(params, basis)
        if reason:
            raise RuntimeError(f"оценка стоимости не совпадает с проектом: {reason}")
    await ctx.call("POST", f"/projects/{params['project_id']}/steps/excel_gpt/run",
                   params={"node_key": params["node_key"], "mode": "resume"})
    return {"started": True, "node_key": params["node_key"]}


# --- каталог ----------------------------------------------------------------------------

def _specs() -> list[ActionSpec]:
    S = ActionSpec
    frame_slot = {"frame_number": _FRAME_NO, "shot": _SHOT}
    node_props = {"node_key": _NODE_KEY}
    pid_only = _obj({}, [])
    img_effects = ("Картинка слота будет заменена загруженной; прежний файл уходит в old/, "
                   "программного отката нет; видео кадра помечается устаревшим.")
    return [
        # --- загрузки
        S(id="montage.upload_voice", title_ru="Загрузить озвучку",
          description_ru="Сохраняет файл как audio/voice_full.<расширение> и запоминает путь в "
                         "настройках проекта. Источник: source_path внутри DATA_DIR или "
                         "data_base64 (до 512 КБ). Прежний файл с тем же расширением "
                         "перезаписывается без архива.",
          params_schema=_obj({**_SRC_PROPS}, []), executor=_upload_voice,
          risk=RISK_DESTRUCTIVE, destructive=True, reversible=False, idempotent=True,
          destructive_effects=lambda p: ("Озвучка проекта (audio/voice_full.*) будет заменена "
                                         "загруженной файлом; прежний файл с тем же "
                                         "расширением перезаписывается, копии нет."),
          preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NO_MONTAGE, _PRE_NO_ALIGN,
                         _pre_source("voice")),
          tags=("montage", "upload", "audio")),
        S(id="montage.upload_music", title_ru="Загрузить фоновую музыку",
          description_ru="Сохраняет файл как music/bgm.<расширение> и запоминает путь в "
                         "настройках проекта. Прежний bgm с тем же расширением перезаписывается.",
          params_schema=_obj({**_SRC_PROPS}, []), executor=_upload_music,
          risk=RISK_DESTRUCTIVE, destructive=True, reversible=False, idempotent=True,
          destructive_effects=lambda p: ("Фоновая музыка проекта (music/bgm.*) будет заменена; "
                                         "прежний файл с тем же расширением перезаписывается, "
                                         "копии нет."),
          preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NO_MONTAGE, _pre_source("music")),
          tags=("montage", "upload", "audio")),
        S(id="montage.upload_image", title_ru="Загрузить картинку в слот кадра",
          description_ru="Кладёт PNG в слот кадра (shot 1/2, slot=parent для still родителя): "
                         "прежняя картинка слота архивируется в old/, видео кадра помечается "
                         "устаревшим. Только .png.",
          params_schema=_obj({**frame_slot, "slot": {"type": "string", "enum": ["", "parent"]},
                              **_SRC_PROPS}, ["frame_number"]),
          executor=_upload_image, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False, destructive_effects=lambda p: img_effects,
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("frame_number"),
                         _pre_source("scene_image")),
          tags=("montage", "upload", "media")),
        S(id="montage.upload_video", title_ru="Загрузить видео в слот кадра",
          description_ru="Кладёт MP4 в слот видео кадра: прежний клип архивируется в old/, "
                         "пометка «устарело» снимается. Только .mp4.",
          params_schema=_obj({**frame_slot, **_SRC_PROPS}, ["frame_number"]),
          executor=_upload_video, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False,
          destructive_effects=lambda p: ("Видео слота будет заменено загруженным; прежний клип "
                                         "уходит в old/, программного отката нет."),
          preconditions=(_PRE_PROJECT, _PRE_NO_APPLY, _pre_frame_numbers("frame_number"),
                         _pre_source("scene_video")),
          tags=("montage", "upload", "media")),
        S(id="montage.upload_ref", title_ru="Загрузить реф кадру",
          description_ru="Новый ручной референс кадра (картинка и имя): новый файл, прежние "
                         "рефы не затрагиваются. Повтор добавит ещё один реф.",
          params_schema=_obj({
              "frame_number": _FRAME_NO,
              "kind": {"type": "string", "enum": ["character", "item", "background", "other"]},
              "name": {"type": "string", "minLength": 1, "maxLength": 200}, **_SRC_PROPS},
              ["frame_number", "name"]),
          executor=_upload_ref, risk=RISK_LOCAL_SAFE, idempotent=False,
          preconditions=(_PRE_PROJECT, _pre_frame_numbers("frame_number"),
                         _pre_source("ref_image")),
          tags=("montage", "upload", "refs")),
        S(id="node.upload_file", title_ru="Загрузить файл в ноду «Работа с GPT»",
          description_ru="Подменяет вход ноды: .xlsx заменяет прежние xlsx ноды (они удаляются) "
                         "и выключает «брать со стрелок»; в режиме проверки .txt/.md становится "
                         "агентом проверки. Файл с тем же именем перезаписывается.",
          params_schema=_obj({**node_props, **_SRC_PROPS}, ["node_key"]),
          executor=_upload_node_file, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False,
          destructive_effects=lambda p: (f"Вход ноды {p['node_key']} будет подменён; старые xlsx "
                                         "ноды и одноимённый файл удаляются без копии."),
          preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE, _PRE_NODE_KEY_SAFE,
                         _pre_source("node_file")),
          tags=("node", "upload")),
        S(id="node.upload_check_agent", title_ru="Загрузить агента проверки в ноду",
          description_ru="Сохраняет .txt/.md агента проверки ноды, включает режим проверки и "
                         "источник «агент». Прежний агент ноды перезаписывается.",
          params_schema=_obj({**node_props, **_SRC_PROPS}, ["node_key"]),
          executor=_upload_check_agent, risk=RISK_DESTRUCTIVE, destructive=True,
          reversible=False, idempotent=False,
          destructive_effects=lambda p: (f"Агент проверки ноды {p['node_key']} будет заменён, "
                                         "режим проверки включён; прежний текст агента теряется."),
          preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE, _PRE_NODE_KEY_SAFE,
                         _pre_source("check_agent")),
          tags=("node", "upload", "check")),
        S(id="node.clear_check_agent", title_ru="Сбросить загруженного агента проверки",
          description_ru="Удаляет загруженные файлы агента проверки ноды: дальше берётся "
                         "встроенный агент.",
          params_schema=_obj({**node_props}, ["node_key"]), executor=_clear_check_agent,
          risk=RISK_DESTRUCTIVE, destructive=True, reversible=False, idempotent=True,
          destructive_effects=lambda p: (f"Загруженный агент проверки ноды {p['node_key']} "
                                         "будет удалён с диска."),
          preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE, _PRE_NODE_KEY_SAFE),
          tags=("node", "check")),
        # --- пауза и продолжение
        S(id="project.pause", title_ru="Поставить проект на паузу",
          description_ru="Останавливает выполняющуюся ноду и ставит статус paused (прежний "
                         "статус запоминается). Незавершённая работа ноды теряется. Повтор на "
                         "уже поставленной паузе ничего не меняет.",
          params_schema=pid_only, executor=_pause, risk=RISK_LOCAL_SAFE, idempotent=True,
          preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED), tags=("project", "pause")),
        S(id="project.resume", title_ru="Снять паузу проекта",
          description_ru="Возвращает статус, с которого проект ушёл в паузу. Если это был "
                         "выполняющийся шаг, воркер сам продолжит его и потратит деньги. "
                         "Стоимость запуска неизвестна, поэтому план нельзя подтвердить.",
          params_schema=pid_only, executor=_resume, poll=_poll_project_idle,
          stop=_stop_project, poll_interval_s=3.0, risk=RISK_DESTRUCTIVE, spends_money=True,
          destructive=True, reversible=False, idempotent=False, estimator=_unknown_start_cost,
          destructive_effects=lambda p: ("Проект вернётся в статус до паузы; если это был "
                                         "running-шаг, воркер перезапустит его и может "
                                         "перезаписать результаты."),
          preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, Precondition(
              "project_paused", "Проект на паузе", _project_paused), _PRE_NO_MONTAGE),
          tags=("project", "pause", "run")),
        S(id="project.continue_pipeline", title_ru="Продолжить конвейер проекта",
          description_ru="Снимает ручной стоп и паузу и продвигает проект на следующий шаг "
                         "(*_ready -> running), если шаг готов к запуску. Следующий шаг может "
                         "быть платным. Стоимость запуска неизвестна, поэтому план нельзя "
                         "подтвердить.",
          params_schema=pid_only, executor=_continue, poll=_poll_project_idle,
          stop=_stop_project, poll_interval_s=3.0, risk=RISK_DESTRUCTIVE, spends_money=True,
          destructive=True, reversible=False, idempotent=False, estimator=_unknown_start_cost,
          destructive_effects=lambda p: ("Ручной стоп проекта снимается, следующий шаг "
                                         "запускается воркером и может перезаписать результаты."),
          preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE, _PRE_NO_MONTAGE),
          tags=("project", "run")),
        # --- xlsx и отправка на основной ПК
        S(id="project.reload_xlsx", title_ru="Перечитать project.xlsx в базу",
          description_ru="Импортирует project.xlsx в БД: поля проекта, закадр, промты и атрибуты "
                         "кадров перезаписываются значениями из файла, недостающие кадры "
                         "создаются. Правки, сделанные в БД после выгрузки xlsx, теряются.",
          params_schema=pid_only, executor=_reload_xlsx, risk=RISK_DESTRUCTIVE, destructive=True,
          reversible=False, idempotent=True,
          destructive_effects=lambda p: ("Значения кадров и проекта в БД будут перезаписаны из "
                                         "project.xlsx, правки только в БД теряются."),
          preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NO_MONTAGE, _PRE_NO_APPLY),
          tags=("project", "xlsx")),
        S(id="montage.set_send_to_main_pc", title_ru="Отправлять сборку на основной ПК",
          description_ru="Включает или выключает флаг assemble.send_to_main_pc. Включённый флаг "
                         "после music_ready помечает проект для hub-очереди монтажа: данные "
                         "проекта уходят на основной ПК (FLEET_ENABLED), там идут ASR и сборка. "
                         "Деньги не тратятся; сам флаг откатывается повторным вызовом, уже "
                         "поставленный в очередь проект остаётся.",
          params_schema=_obj({"enabled": {"type": "boolean"}}, ["enabled"]),
          executor=_set_send_to_main_pc, risk=RISK_LOCAL_SAFE, idempotent=True,
          preconditions=(_PRE_PROJECT, _PRE_IDLE, Precondition(
              "fleet_enabled", "Fleet включён (иначе флаг не действует)",
              _fleet_enabled_for_flag)),
          tags=("montage", "assemble", "fleet")),
        # --- мастер-промты
        S(id="prompt.get_master", title_ru="Прочитать мастер-промт",
          description_ru="Текст общего промта шага (первые 20000 символов), длина и sha256.",
          params_schema=_obj({"step_code": _STEP_CODE, "name": _PROMPT_NAME},
                             ["step_code", "name"], project=False),
          executor=_get_master, risk=RISK_READ, idempotent=True, scope="global",
          preconditions=(Precondition("prompt_exists", "Промт существует", _pre_prompt_exists),),
          tags=("prompt",)),
        S(id="prompt.save_master", title_ru="Перезаписать мастер-промт (для всех проектов)",
          description_ru="Пишет общий файл промта шага: правка действует на ВСЕ проекты. Результат "
                         "содержит sha256 прежнего и нового текста. Прежняя версия остаётся в "
                         "истории промта, но восстановить её из каталога нельзя.",
          params_schema=_obj({"step_code": _STEP_CODE, "name": _PROMPT_NAME,
                              "content": {"type": "string", "minLength": 1, "maxLength": 200000}},
                             ["step_code", "name", "content"], project=False),
          executor=_save_master, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=True, scope="global",
          destructive_effects=lambda p: (
              f"ГЛОБАЛЬНО: общий промт {p['step_code']}/{p['name']} будет перезаписан для всех "
              "проектов" + ("; это файл шаблона группы script_frames_qc в репозитории"
                            if _group_query(p) else "") +
              "; прежняя версия остаётся только в истории версий Studio."),
          preconditions=(Precondition("prompt_exists", "Промт существует", _pre_prompt_exists),
                         Precondition("content_nonblank", "Текст не пустой",
                                      _pre_content_nonblank)),
          tags=("prompt", "global")),
        S(id="prompt.create_master", title_ru="Создать вариант мастер-промта",
          description_ru="Новый файл варианта промта шага (общий для всех проектов). Существующее "
                         "имя не перезаписывается.",
          params_schema=_obj({"step_code": _STEP_CODE, "name": _PROMPT_NAME,
                              "content": {"type": "string", "minLength": 1, "maxLength": 200000}},
                             ["step_code", "name", "content"], project=False),
          executor=_create_master, risk=RISK_LOCAL_SAFE, idempotent=False, scope="global",
          preconditions=(Precondition("prompt_free", "Имя свободно", _pre_prompt_free),
                         Precondition("content_nonblank", "Текст не пустой",
                                      _pre_content_nonblank)),
          tags=("prompt", "global")),
        S(id="prompt.delete_master", title_ru="Удалить вариант мастер-промта",
          description_ru="Удаляет файл варианта промта шага (общий для всех проектов, default "
                         "удалять нельзя). Проекты, выбравшие этот вариант, перейдут на запасной.",
          params_schema=_obj({"step_code": _STEP_CODE, "name": _PROMPT_NAME},
                             ["step_code", "name"], project=False),
          executor=_delete_master, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
          idempotent=False, scope="global",
          destructive_effects=lambda p: (f"ГЛОБАЛЬНО: файл промта {p['step_code']}/{p['name']} "
                                         "будет удалён для всех проектов."),
          preconditions=(Precondition("not_default", "Это не default", _pre_not_default),
                         Precondition("prompt_exists", "Промт существует", _pre_prompt_exists)),
          tags=("prompt", "global")),
        # --- стоимость и запуск ноды
        S(id="node.get_run_cost_basis", title_ru="Верхняя оценка прогона текстовой ноды",
          description_ru="Для нод Каркас, Площадка, Шоты, QC: пачки, худшее число запросов к "
                         "модели и токены на запрос по реальным данным проекта и файлам промтов. "
                         "Значения из поля declare передаются в node.run_step. Оценка "
                         "верхняя и не откалибрована.",
          params_schema=_obj({**node_props}, ["node_key"]), executor=_get_run_cost_basis,
          risk=RISK_READ, idempotent=True, preconditions=(_PRE_PROJECT, _PRE_NODE),
          tags=("node", "cost")),
        S(id="node.run_step", title_ru="Запустить шаг на ноде «Работа с GPT»",
          description_ru="Явный старт шага ноды (режим resume): убивает активную генерацию проекта "
                         "и перезаписывает в БД выход ноды по всем ячейкам. LLM идёт через "
                         "Vibecode. Для Каркас/Площадка/Шоты/QC заявите стоимость "
                         "(node.get_run_cost_basis), без заявки она неизвестна и план нельзя "
                         "подтвердить. Заблокирован, если в проекте что-то выполняется или проект "
                         "под Actions-v1. Останавливается глобальным стопом проекта.",
          params_schema=_obj({**node_props, **_BASIS_INTS}, ["node_key"]),
          executor=_run_node_step, poll=_node_run_poll, stop=_stop_project, poll_interval_s=3.0,
          risk=RISK_DESTRUCTIVE, spends_money=True, destructive=True, reversible=False,
          idempotent=False, estimator=_estimate_node_run,
          destructive_effects=_node_run_effects,
          preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE, _PRE_NO_MONTAGE,
                         _PRE_NODE_GPT, Precondition(
                             "run_cost_declared",
                             "Заявленная стоимость не меньше расчёта по данным проекта",
                             _pre_run_cost)),
          tags=("node", "run")),
    ]


def register_wave3(registry: ActionRegistry) -> None:
    # node.run_step из builtin_group заменяется версией с оценкой.
    registry.unregister("node.run_step")
    registry.register_many(_specs())
