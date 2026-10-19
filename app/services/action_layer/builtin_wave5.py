from __future__ import annotations

from dataclasses import replace
from typing import Any

from app.services.action_layer import node_run_cost as nrc
from app.services.action_layer.builtin_group import (
    _NODE_KEY, _PRE_IDLE, _PRE_NODE, _PRE_NOT_MANAGED, _PRE_NO_MONTAGE, _get_project,
    _stop_project)
from app.services.action_layer.builtin_montage import _PID, _PRE_PROJECT, _base
from app.services.action_layer.builtin_montage_more import (
    _GEN, _IMG_RES, _VID_RES, _effective_settings)
from app.services.action_layer.builtin_wave3 import (
    _PRE_NODE_KEY_SAFE, _SRC_PROPS, _obj, _poll_project_idle, _post_file, _pre_source,
    _unknown_start_cost)
from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE, RISK_LOCAL_SAFE, RISK_READ, ActionRegistry, ActionSpec, CostEstimate,
    Precondition)

WAVE5_IDS: tuple[str, ...] = (
    "step.get_run_cost_basis", "step.run_step",
    "hitl.list_pending", "hitl.approve", "hitl.reject", "hitl.regenerate",
    "project.create", "project.create_child", "project.patch_settings",
    "massfactory.start", "kill_switch.engage",
    "project.upload_xlsx", "hero.replace_image", "storage.upload_file", "storage.clear_files",
    "history.list_snapshots", "history.rollback",
)
WAVE5_CHANGED_IDS: tuple[str, ...] = (
    "project.resume", "project.continue_pipeline",
    "montage.set_voiceover", "montage.set_coverage", "montage.set_anchors",
    "montage.set_scene_chain", "shot.edit_cell_voiceover", "shot.edit_field",
    "montage.upload_voice", "montage.upload_music", "prompt.save_master",
)
EXCLUDED_CONTROLS: dict[str, str] = {
    "Публикация на площадки": "остаётся действием человека (node_capabilities: publish human_only, "
                              "управляемый агентский режим запрещён); в каталог не входит",
    "Снятие аварийного стопа": "включённый стоп блокирует любые шаги слоя (executor), снять его "
                               "можно только человеческой кнопкой; слой умеет лишь включать",
    "Удаление проекта": "необратимо удаляет папку проекта; планировщик намеренно держит его вне каталога",
    "Воркфлоу и холст": "переписывание графа целиком, гонка с открытым "
                                               "холстом (docs/ACTION_LAYER.md, «Не вошло в каталог»)",
    "GPT-workspace, meta-agent, gpt-verdict": "платный LLM вне проекта: стоимость запроса не "
                                              "определяется кодом, отдельная волна",
    "Baza (правка кадров /db/*)": "частично покрыто shot.* и montage.*, полный набор правок - "
                                  "отдельная волна",
    "Outsee/Kie create-workspace, gen-assistant": "автономная генерация вне проекта, тарифы Outsee "
                                                  "в репозитории не заданы",
    "Fleet pull/push/sync": "запуск на основном ПК, не локальный контроль; есть только флаг "
                            "montage.set_send_to_main_pc",
    "Дублирование проекта": "эндпоинта дублирования нет; есть только дочерний проект "
                            "(project.create_child)",
}

_CEILING = {"type": "number", "minimum": 0.01, "maximum": 100000,
            "description": "Потолок расхода в кредитах, который назвал оператор"}
_STEP_CODES = list(nrc.MEDIA_STEPS)
_HERO_MODES = ["hero", "no_hero", "auto"]
_TOPIC = {"type": "string", "maxLength": 2000}
_TITLE = {"type": "string", "minLength": 1, "maxLength": 200}



async def _no_auto_chain(ctx, params):
    proj = await _get_project(ctx, params["project_id"])
    if proj.get("auto_mode"):
        return ("включено автопродвижение: после этого шага воркер сам запустит следующие, "
                "платные тоже, и потолок их не покрывает; выключите его (project.set_auto_mode)")
    return None


_PRE_NO_AUTO_CHAIN = Precondition("no_auto_chain", "Автопродвижение выключено (шаг один, цену "
                                  "покрывает потолок)", _no_auto_chain)


def _ceiling_estimate(base: CostEstimate, params: dict) -> CostEstimate:
    return nrc.apply_ceiling(base, params.get("ceiling_credits"))



async def _step_basis(ctx: ActionContext, params: dict) -> dict:
    code = params["step_code"]
    kind = nrc.media_step_kind(code)
    detail = await _get_project(ctx, params["project_id"])
    eff = _effective_settings(detail)
    board = await ctx.call("GET", _base(params["project_id"]))
    units = nrc.media_units((board or {}).get("frames") or [])
    declare: dict[str, Any] = {}
    count = None
    if kind == "image":
        count = units["images"]
        declare = {"count": count, "image_generator": eff["image_generator"],
                   "image_resolution": eff["image_resolution"]}
    elif kind == "video":
        count = units["videos"]
        declare = {"count": count, "video_generator": eff["video_generator"],
                   "video_resolution": eff["video_resolution"]}
    est = nrc.media_code_estimate(
        code, units=count, image_generator=eff["image_generator"],
        image_resolution=eff["image_resolution"], video_generator=eff["video_generator"],
        video_resolution=eff["video_resolution"])
    return {"kind": kind, "units": units, "declare": declare, "estimate": est, "effective": eff}


async def _get_step_cost_basis(ctx, params):
    basis = await _step_basis(ctx, params)
    est: CostEstimate = basis["estimate"]
    out = {"step_code": params["step_code"], "kind": basis["kind"], "units": basis["units"],
           "declare": basis["declare"], "estimable": est.known, "uncalibrated": True,
           "basis": est.basis, "needs_ceiling": (not est.known) and basis["kind"] != "local"}
    if est.known:
        out["upper_bound_credits"] = est.high
    else:
        out["hint"] = ("цена по коду неизвестна: для запуска укажите ceiling_credits - потолок, "
                       "который вы готовы потратить на этот шаг")
    return out


def _estimate_step(params: dict) -> CostEstimate:
    base = nrc.media_code_estimate(
        params.get("step_code", ""), units=params.get("count"),
        image_generator=params.get("image_generator"),
        image_resolution=params.get("image_resolution"),
        video_generator=params.get("video_generator"),
        video_resolution=params.get("video_resolution"))
    return _ceiling_estimate(base, params)


async def _verify_step_cost(ctx: ActionContext, params: dict) -> str | None:
    code = params["step_code"]
    basis = await _step_basis(ctx, params)
    kind = basis["kind"]
    if kind in ("image", "video"):
        need = basis["declare"]
        if params.get("count") is None:
            return f"заявите count: число генераций по данным проекта {need['count']}"
        if params["count"] < need["count"]:
            return f"count: заявлено {params['count']}, по доске нужно не меньше {need['count']}"
        for name in ("image_generator", "image_resolution") if kind == "image" \
                else ("video_generator", "video_resolution"):
            if params.get(name) is None:
                return f"укажите {name} (в проекте: {need[name]})"
            if params[name] != need[name]:
                return f"{name}: заявлено {params[name]}, в проекте {need[name]}"
    actual = basis["estimate"]
    ceiling = params.get("ceiling_credits")
    if actual.known and ceiling is not None and ceiling < actual.high:
        return f"ceiling_credits {ceiling:g} ниже расчёта {actual.high:g} кр по данным проекта"
    final = _estimate_step(params)
    if kind != "local" and not final.known:
        return ("стоимость шага неизвестна: " + actual.basis +
                ". Укажите ceiling_credits - потолок расхода на этот шаг")
    return None


async def _pre_step_cost(ctx, params):
    return await _verify_step_cost(ctx, params)


async def _pre_step_runnable(ctx, params):
    from app.telegram.menu import is_step_runnable, step_by_code
    from app.models import ProjectStatus

    step = step_by_code(params["step_code"])
    if step is None:
        return f"неизвестный шаг {params['step_code']!r}"
    proj = await _get_project(ctx, params["project_id"])
    try:
        status = ProjectStatus(proj.get("status"))
    except ValueError:
        return f"неизвестный статус проекта {proj.get('status')!r}"
    if not is_step_runnable(step, status):
        return (f"шаг «{step.title}» сейчас недоступен при статусе проекта {status.value}: "
                "предыдущие шаги не выполнены или проект на паузе")
    return None


async def _run_media_step(ctx, params):
    reason = await _verify_step_cost(ctx, params)
    if reason:
        raise RuntimeError(f"оценка стоимости не совпадает с проектом: {reason}")
    await ctx.call("POST", f"/projects/{params['project_id']}/steps/{params['step_code']}/run",
                   params={"mode": "resume"})
    return {"started": True, "step_code": params["step_code"]}


async def _poll_step(ctx, params, started):
    state = await _poll_project_idle(ctx, params, started)
    if not state.done:
        return state
    final = str((state.result or {}).get("final_status"))
    if final == "failed":
        return replace(state, ok=False, error="шаг завершился с ошибкой (статус проекта failed)")
    if final == "paused":
        return replace(state, ok=False, error="шаг не завершён: проект поставлен на паузу (статус paused)")
    return state


def _step_effects(params: dict) -> str:
    title = nrc.MEDIA_STEPS.get(params["step_code"], ("", params["step_code"]))[1]
    return (f"Запуск шага «{title}» убивает активную генерацию проекта, снимает стоп и паузу "
            "очереди и перезаписывает результат шага; платные вызовы провайдеров начнутся сразу. "
            "Остановить: project.stop или аварийный стоп.")



async def _hitl_rows(ctx, project_id: int) -> list[dict]:
    rows = await ctx.call("GET", f"/hitl/project/{project_id}")
    return [r for r in rows or [] if isinstance(r, dict)]


async def _list_pending(ctx, params):
    rows = [r for r in await _hitl_rows(ctx, params["project_id"])
            if str(r.get("decision")) == "pending"]
    return {"count": len(rows), "requests": [
        {"hitl_id": r.get("id"), "kind": r.get("kind"), "frame_id": r.get("frame_id"),
         "created_at": r.get("created_at"), "payload_keys": sorted((r.get("payload") or {}))[:20]}
        for r in rows[:50]]}


async def _hitl_pending(ctx, params):
    for row in await _hitl_rows(ctx, params["project_id"]):
        if row.get("id") == params["hitl_id"]:
            if str(row.get("decision")) != "pending":
                return f"решение по запросу {params['hitl_id']} уже принято ({row.get('decision')})"
            return None
    return f"запроса подтверждения {params['hitl_id']} в проекте {params['project_id']} нет"


_PRE_HITL = Precondition("hitl_pending", "Запрос подтверждения существует и ждёт решения",
                         _hitl_pending)


def _decide(decision: str):
    async def executor(ctx, params):
        body = {"decision": decision, "project_id": params.get("project_id")}
        if decision == "approve":
            body["advance"] = False
        data = await ctx.call("POST", f"/hitl/{params['hitl_id']}/decision", json=body)
        return {"hitl_id": params["hitl_id"], "decision": (data or {}).get("decision"),
                "kind": (data or {}).get("kind"), "frame_id": (data or {}).get("frame_id")}
    return executor


_HITL_PARAMS = {"hitl_id": {"type": "integer", "minimum": 1,
                            "description": "id запроса из hitl.list_pending"}}



async def _create_project(ctx, params):
    body = {"title": params["title"].strip()}
    if params.get("topic"):
        body["topic"] = params["topic"].strip()
    if params.get("hero_mode"):
        body["hero_mode"] = params["hero_mode"]
    data = await ctx.call("POST", "/projects", json=body)
    return {"project_id": (data or {}).get("id"), "slug": (data or {}).get("slug"),
            "title": (data or {}).get("title"), "status": (data or {}).get("status")}


async def _title_nonblank(ctx, params):
    return None if params["title"].strip() else "название проекта пустое"


async def _create_child(ctx, params):
    data = await ctx.call("POST", f"/projects/{params['project_id']}/child")
    return {"child_id": (data or {}).get("id"), "slug": (data or {}).get("slug"),
            "parent_id": params["project_id"]}


_SETTINGS_KEYS = ("title", "topic", "hero_mode", "image_generator", "image_resolution",
                  "video_generator", "video_resolution", "aspect_ratio")


async def _settings_given(ctx, params):
    if not any(k in params for k in _SETTINGS_KEYS):
        return f"укажите хотя бы одну настройку: {', '.join(_SETTINGS_KEYS)}"
    return None


async def _patch_settings(ctx, params):
    body = {k: params[k] for k in _SETTINGS_KEYS if k in params}
    data = await ctx.call("PATCH", f"/projects/{params['project_id']}", json=body)
    data = data if isinstance(data, dict) else {}
    changed = {k: data.get(k) for k, v in body.items()
               if str(data.get(k) if data.get(k) is not None else "") != str(v).strip()}
    if changed:
        raise ActionCallError(409, f"сервер сохранил другие значения: {changed}")
    return {"updated": sorted(body)}



async def _mass_start(ctx, params):
    data = await ctx.call("POST", f"/projects/{params['project_id']}/mass-lanes/start",
                          json={"topics": list(params["topics"])})
    return {"started_id": (data or {}).get("started_id"),
            "queue_size": (data or {}).get("queue_size"),
            "remaining": (data or {}).get("remaining")}


def _mass_estimate(params: dict) -> CostEstimate:
    n = len(params.get("topics") or [])
    return _ceiling_estimate(CostEstimate.unknown(
        f"{n} видео по полному конвейеру в дочерних проектах: цена по коду не определяется"), params)



async def _kill_engage(ctx, params):
    data = await ctx.call("POST", f"/projects/{params['project_id']}/observations/kill-switch",
                          json={"active": True, "rationale": params["rationale"]})
    data = data if isinstance(data, dict) else {}
    if data.get("active") is not True:
        raise ActionCallError(409, f"аварийный стоп не включился: {str(data)[:200]}")
    return {"active": True}



async def _upload_xlsx(ctx, params):
    data, info = await _post_file(
        ctx, "xlsx", f"/projects/{params['project_id']}/xlsx/upload", params)
    return {"status": data.get("status"), **info}


async def _replace_hero(ctx, params):
    data, info = await _post_file(
        ctx, "ref_image", f"/projects/{params['project_id']}/assets/hero/replace", params)
    return {"path": data.get("path"), "id": data.get("id"), **info}


async def _storage_upload(ctx, params):
    data, info = await _post_file(
        ctx, "node_file",
        f"/projects/{params['project_id']}/storage/{params['node_key']}/upload", params)
    return {"ok": bool(data.get("ok")), "file_name": data.get("fileName"), **info}


async def _storage_clear(ctx, params):
    data = await ctx.call(
        "DELETE", f"/projects/{params['project_id']}/storage/{params['node_key']}/files")
    return {"ok": bool((data or {}).get("ok")), "removed": (data or {}).get("removed")}



def _specs() -> list[ActionSpec]:
    S = ActionSpec
    step_props = {
        "step_code": {"type": "string", "enum": _STEP_CODES},
        "count": {"type": "integer", "minimum": 1, "maximum": 100000,
                  "description": "Число генераций из step.get_run_cost_basis (для img и video)"},
        "image_generator": _GEN, "image_resolution": _IMG_RES,
        "video_generator": _GEN, "video_resolution": _VID_RES,
        "ceiling_credits": _CEILING,
    }
    return [
        S(id="step.get_run_cost_basis", title_ru="Оценка стоимости запуска шага конвейера",
          description_ru="Для шагов картинок, видео, озвучки, музыки, сборки и LLM-промтов: сколько "
                         "генераций по доске, какие значения заявить в step.run_step и верхняя оценка "
                         "в кредитах. Картинки и видео оцениваются по каталогу Kie (Outsee и прочие "
                         "модели - нет тарифа), озвучка, музыка и промты - оценки по коду нет, тогда "
                         "нужен потолок ceiling_credits. Оценка не откалибрована по балансу.",
          params_schema=_obj({"step_code": step_props["step_code"]}, ["step_code"]),
          executor=_get_step_cost_basis, risk=RISK_READ, idempotent=True,
          preconditions=(_PRE_PROJECT,), tags=("step", "cost")),
        S(id="step.run_step", title_ru="Запустить шаг конвейера проекта",
          description_ru="Ручной запуск шага (режим resume): картинки, видео, озвучка, музыка, "
                         "финальная сборка, промты картинок и анимации. Убивает активную генерацию "
                         "проекта и перезаписывает результат шага. Картинки и видео оцениваются по "
                         "каталогу Kie, для остальных платных шагов нужен потолок ceiling_credits; "
                         "потолок и заявленные значения входят в хэш подтверждения. Автопродвижение "
                         "должно быть выключено. Публикация сюда не входит.",
          params_schema=_obj(step_props, ["step_code"]),
          executor=_run_media_step, poll=_poll_step, stop=_stop_project, poll_interval_s=3.0,
          risk=RISK_DESTRUCTIVE, spends_money=True, destructive=True, reversible=False,
          idempotent=False, estimator=_estimate_step, destructive_effects=_step_effects,
          preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE, _PRE_NO_MONTAGE,
                         _PRE_NO_AUTO_CHAIN,
                         Precondition("step_runnable", "Шаг доступен при статусе проекта",
                                      _pre_step_runnable),
                         Precondition("step_cost_declared",
                                      "Заявленные число, модель и потолок покрывают расчёт по проекту",
                                      _pre_step_cost)),
          tags=("step", "run")),
        S(id="hitl.list_pending", title_ru="Показать ожидающие подтверждения",
          description_ru="Запросы подтверждения проекта (HITL), по которым решение ещё не принято: "
                         "id, вид и кадр. Ничего не меняет.",
          params_schema=_obj({}, []), executor=_list_pending, risk=RISK_READ, idempotent=True,
          preconditions=(_PRE_PROJECT,), tags=("hitl",)),
        S(id="hitl.approve", title_ru="Одобрить запрос подтверждения (HITL)",
          description_ru="Принимает результат: кадр получает статус одобренного. Следующий шаг "
                         "НЕ запускается (платный запуск идёт отдельным действием step.run_step с оценкой "
                         "цены и потолком). Решение необратимо. Выполняется с токеном оператора. "
                         "Автопродвижение должно быть выключено.",
          params_schema=_obj(_HITL_PARAMS, ["hitl_id"]), executor=_decide("approve"),
          risk=RISK_DESTRUCTIVE, destructive=True, reversible=False, idempotent=False,
          destructive_effects=lambda p: (f"Решение по запросу {p['hitl_id']} будет принято "
                                         "«одобрено» и повторно не меняется; следующий шаг "
                                         "не запускается."),
          preconditions=(_PRE_PROJECT, _PRE_HITL, _PRE_NO_AUTO_CHAIN), tags=("hitl",)),
        S(id="hitl.reject", title_ru="Отклонить запрос подтверждения (HITL)",
          description_ru="Отклоняет результат: кадр получает статус failed, проект может уйти на "
                         "паузу. Решение необратимо. Выполняется с токеном оператора.",
          params_schema=_obj(_HITL_PARAMS, ["hitl_id"]), executor=_decide("reject"),
          risk=RISK_DESTRUCTIVE, destructive=True, reversible=False, idempotent=False,
          destructive_effects=lambda p: (f"Решение по запросу {p['hitl_id']} будет принято "
                                         "«отклонено» и повторно не меняется; кадр помечается "
                                         "неудачным."),
          preconditions=(_PRE_PROJECT, _PRE_HITL), tags=("hitl",)),
        S(id="hitl.regenerate", title_ru="Запросить перегенерацию по HITL",
          description_ru="Возвращает кадр на перегенерацию: воркер заново сгенерирует картинку или "
                         "клип и потратит деньги. Стоимость по коду не определяется, поэтому нужен "
                         "потолок ceiling_credits; он входит в хэш подтверждения. Автопродвижение "
                         "должно быть выключено. Решение необратимо.",
          params_schema=_obj({**_HITL_PARAMS, "ceiling_credits": _CEILING},
                             ["hitl_id", "ceiling_credits"]),
          executor=_decide("regenerate"), risk=RISK_DESTRUCTIVE, spends_money=True, deferred_spend=True,
          destructive=True, reversible=False, idempotent=False,
          estimator=lambda p: _ceiling_estimate(CostEstimate.unknown(
              "перегенерация: число попыток и цена модели из параметров не определяются"), p),
          destructive_effects=lambda p: (f"Решение по запросу {p['hitl_id']} будет принято "
                                         "«перегенерировать»; прежний результат кадра заменится."),
          preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_HITL, _PRE_NO_AUTO_CHAIN),
          tags=("hitl", "run")),
        S(id="project.create", title_ru="Создать проект",
          description_ru="Новый проект с названием, темой и режимом героя. Автопродвижение не "
                         "включается (если стоит серверная настройка «автопродвижение новых "
                         "проектов», она применится). Ничего не запускает и денег не тратит.",
          params_schema=_obj({"title": _TITLE, "topic": _TOPIC,
                              "hero_mode": {"type": "string", "enum": _HERO_MODES}},
                             ["title"], project=False),
          executor=_create_project, risk=RISK_LOCAL_SAFE, idempotent=False, scope="global",
          future_spend_note="Если на сервере включено «автопродвижение новых проектов», проект получит его "
                            "сразу: дальнейшие шаги могут быть платными.",
          preconditions=(Precondition("title_nonblank", "Название не пустое", _title_nonblank),),
          tags=("project", "create")),
        S(id="project.create_child", title_ru="Создать дочерний проект",
          description_ru="Дочерний проект: настройки, промты и тексты родителя, без озвучки, Excel "
                         "и результатов. Ничего не запускает.",
          params_schema=_obj({}, []), executor=_create_child, risk=RISK_LOCAL_SAFE,
          idempotent=False, preconditions=(_PRE_PROJECT,), tags=("project", "create"),
          future_spend_note="Копирует настройки родителя (модель, разрешение), от них зависит цена "
                            "будущих генераций дочернего проекта."),
        S(id="project.patch_settings", title_ru="Изменить настройки проекта",
          description_ru="Название, тема, режим героя, модель и разрешение картинок и видео, формат "
                         "кадра. Меняет цену будущих генераций, но сам ничего не запускает. Проверяет, "
                         "что сервер сохранил именно эти значения. Автопродвижение, мета и промты "
                         "здесь не меняются.",
          params_schema=_obj({
              "title": _TITLE, "topic": _TOPIC,
              "hero_mode": {"type": "string", "enum": _HERO_MODES},
              "image_generator": _GEN, "image_resolution": _IMG_RES,
              "video_generator": _GEN, "video_resolution": _VID_RES,
              "aspect_ratio": {"type": "string", "pattern": r"^\d{1,2}:\d{1,2}$"}}, []),
          executor=_patch_settings, risk=RISK_LOCAL_SAFE, idempotent=True,
          future_spend_note="Меняет модель и разрешение, то есть цену будущих генераций проекта.",
          preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE,
                         Precondition("settings_given", "Указана хотя бы одна настройка",
                                      _settings_given)),
          tags=("project", "settings")),
        S(id="massfactory.start", title_ru="Запустить массовую фабрику видео",
          description_ru="Очередь тем: первый дочерний проект стартует с плана, остальные идут по "
                         "очереди сами. Удаляет новые (нетронутые) дочерние проекты прежней "
                         "очереди. Цена по коду не определяется (полный конвейер на каждую тему), "
                         "поэтому нужен потолок ceiling_credits на всю очередь; воркер потолок не "
                         "знает - остановить можно project.stop или аварийным стопом.",
          params_schema=_obj({
              "topics": {"type": "array", "minItems": 1, "maxItems": 20,
                         "items": {"type": "string", "minLength": 1, "maxLength": 300}},
              "ceiling_credits": _CEILING}, ["topics", "ceiling_credits"]),
          executor=_mass_start, risk=RISK_DESTRUCTIVE, spends_money=True, deferred_spend=True, destructive=True,
          reversible=False, idempotent=False, estimator=_mass_estimate,
          destructive_effects=lambda p: (
              f"Очередь из {len(p['topics'])} тем стартует; прежние нетронутые дочерние проекты "
              "очереди удаляются; воркер пойдёт по полному конвейеру и потратит деньги."),
          preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE),
          tags=("project", "factory", "run")),
        S(id="kill_switch.engage", title_ru="Включить аварийный стоп",
          description_ru="Включает глобальный аварийный стоп: платные и разрушающие шаги слоя "
                         "перестают выполняться. Причина обязательна и пишется в журнал. Снять стоп "
                         "может только человек кнопкой в панели.",
          params_schema=_obj({"rationale": {"type": "string", "minLength": 3, "maxLength": 1000}},
                             ["rationale"]),
          executor=_kill_engage, risk=RISK_LOCAL_SAFE, idempotent=True,
          preconditions=(_PRE_PROJECT,), tags=("safety", "kill_switch")),
        S(id="project.upload_xlsx", title_ru="Загрузить Excel проекта",
          description_ru="Заменяет project.xlsx файлом .xlsx (проверяется сервером) и перечитывает "
                         "его в базу: поля проекта, закадр и кадры перезаписываются значениями из "
                         "файла. Прежний файл не архивируется.",
          params_schema=_obj({**_SRC_PROPS}, []), executor=_upload_xlsx,
          risk=RISK_DESTRUCTIVE, destructive=True, reversible=False, idempotent=True,
          destructive_effects=lambda p: ("project.xlsx проекта будет заменён загруженным файлом, "
                                         "кадры и поля проекта в базе перезапишутся его данными; "
                                         "прежний файл не сохраняется."),
          preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE, _PRE_NO_MONTAGE,
                         _pre_source("xlsx")),
          tags=("project", "upload", "xlsx")),
        S(id="hero.replace_image", title_ru="Загрузить картинку героя",
          description_ru="Кладёт картинку (png, jpg, webp) в папку characters проекта под её "
                         "именем; файл с таким же именем перезаписывается без архива.",
          params_schema=_obj({**_SRC_PROPS}, []), executor=_replace_hero,
          risk=RISK_DESTRUCTIVE, destructive=True, reversible=False, idempotent=True,
          destructive_effects=lambda p: ("Картинка героя в characters/ с тем же именем файла будет "
                                         "перезаписана без копии."),
          preconditions=(_PRE_PROJECT, _PRE_IDLE, _pre_source("ref_image")),
          tags=("hero", "upload")),
        S(id="storage.upload_file", title_ru="Загрузить файл в хранилище ноды",
          description_ru="Добавляет файл в хранилище ноды проекта под новым именем с отметкой "
                         "времени; существующие файлы не затрагиваются.",
          params_schema=_obj({"node_key": _NODE_KEY, **_SRC_PROPS}, ["node_key"]),
          executor=_storage_upload, risk=RISK_LOCAL_SAFE, idempotent=False,
          preconditions=(_PRE_PROJECT, _PRE_NODE, _PRE_NODE_KEY_SAFE, _pre_source("node_file")),
          tags=("storage", "upload")),
        S(id="storage.clear_files", title_ru="Очистить хранилище ноды",
          description_ru="Удаляет все файлы из хранилища ноды проекта.",
          params_schema=_obj({"node_key": _NODE_KEY}, ["node_key"]), executor=_storage_clear,
          risk=RISK_DESTRUCTIVE, destructive=True, reversible=False, idempotent=True,
          destructive_effects=lambda p: (f"Все файлы хранилища ноды {p['node_key']} будут удалены "
                                         "без копии."),
          preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE, _PRE_NODE_KEY_SAFE),
          tags=("storage",)),
    ]



def _with_ceiling(spec: ActionSpec) -> ActionSpec:
    schema = {**spec.params_schema,
              "properties": {**spec.params_schema["properties"], "ceiling_credits": _CEILING}}
    return replace(
        spec, params_schema=schema,
        estimator=lambda p: _ceiling_estimate(_unknown_start_cost(p), p),
        description_ru=spec.description_ru.replace(
            "Стоимость запуска неизвестна, поэтому план нельзя подтвердить.",
            "Стоимость запуска по коду неизвестна: без потолка ceiling_credits план нельзя "
            "подтвердить; с потолком (и выключенным автопродвижением) подтверждение привязано к "
            "хэшу плана вместе с ним. Воркер потолок не знает - предел расхода держат лимиты "
            "spend_guard и аварийный стоп."),
        preconditions=(*spec.preconditions, Precondition(
            "ceiling_chain", "С потолком: автопродвижение выключено",
            _chain_if_ceiling)))


async def _chain_if_ceiling(ctx, params):
    if params.get("ceiling_credits") is None:
        return None
    return await _no_auto_chain(ctx, params)


def register_wave5(registry: ActionRegistry) -> None:
    from app.services.action_layer import builtin_wave5_history as hist

    for action_id in ("project.resume", "project.continue_pipeline"):
        spec = registry.find(action_id)
        if spec is not None:
            registry.unregister(action_id)
            registry.register(_with_ceiling(spec))
    hist.wrap_catalog(registry)
    registry.register_many([*_specs(), *hist.history_specs()])
