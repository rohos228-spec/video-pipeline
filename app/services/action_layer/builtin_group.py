"""Каталог действий вне панели монтажа: меню съёмки, группы нод, ноды, стоп проекта.

Префиксы: ``shot.*``, ``group.*``, ``node.*`` и ``project.stop``. Исполнители зовут
существующие эндпоинты через ``ctx.call``.

Не заводятся (нет JSON-эндпоинта или это глобальная правка): загрузка файлов в ноду,
правка мастер-промтов (общие файлы для всех проектов), открепление и удаление ноды
(локальные события канваса без бэкенда), смена модели ноды (см. ``node.get_llm_route``).
"""
from __future__ import annotations

import re

from app.services.action_layer.context import ActionCallError, ActionContext
from app.services.action_layer.spec import (
    RISK_DESTRUCTIVE, RISK_LOCAL_SAFE, RISK_READ, ActionRegistry, ActionSpec,
    CostEstimate, JobState, Precondition)

_PID = {"type": "integer", "minimum": 1, "description": "ID проекта"}
_NODE_KEY = {"type": "string", "minLength": 1, "maxLength": 80,
             "pattern": r"^[A-Za-z0-9_.:\-]+$", "description": "id ноды на канвасе"}
_GROUP_ID = {"type": "string", "minLength": 1, "maxLength": 80,
             "pattern": r"^[A-Za-z0-9_\-]+$"}
_MAX_CELLS = 200

_ROLES = ["assist", "review", "transform", "extract", "compare", "gate"]
_OUTPUT_MODES = ["text", "project_file", "sidecar"]
_EMIT_KINDS = ["result", "reply_txt", "analysis", "inputs"]
_SHOT_FIELDS = ["img_prompt", "video_prompt", "vo", "action", "characters", "stitch",
                "scene", "set", "size", "move"]

_NODE_RUN_STEP = "excel_gpt"


def _schema(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


# --- общие проверки ---------------------------------------------------------

async def _get_project(ctx: ActionContext, pid: int) -> dict:
    return await ctx.call("GET", f"/projects/{pid}")


async def _project_exists(ctx: ActionContext, params: dict) -> str | None:
    await _get_project(ctx, params["project_id"])
    return None


def _is_running(status: str | None) -> bool:
    from app.models import ProjectStatus
    from app.services.project_state import is_running_status
    try:
        return is_running_status(ProjectStatus(status))
    except ValueError:
        return False


async def _project_idle(ctx: ActionContext, params: dict) -> str | None:
    proj = await _get_project(ctx, params["project_id"])
    if _is_running(proj.get("status")):
        return f"в проекте выполняется шаг (статус {proj.get('status')})"
    return None


async def _no_montage_jobs(ctx: ActionContext, params: dict) -> str | None:
    base = f"/projects/{params['project_id']}/montage-board"
    for suffix, title in (("apply-status", "применение правок"),
                          ("montage-status", "монтаж видео"),
                          ("recover-outsee-status", "забор из галереи")):
        data = await ctx.call("GET", f"{base}/{suffix}")
        if ((data or {}).get("job") or {}).get("status") == "running":
            return f"идёт задача монтажа: {title}"
    return None


async def _not_managed(ctx: ActionContext, params: dict) -> str | None:
    """Ручной запуск шага при managed-режиме Actions-v1 даёт 409: отказ заранее."""
    try:
        from app.services.actions_v1 import managed_project_enabled
        managed = await managed_project_enabled(params["project_id"])
    except Exception as exc:  # noqa: BLE001 - не смогли проверить: отказ (fail-closed)
        return f"не удалось проверить managed-режим ({type(exc).__name__})"
    if managed:
        return ("проект под управляемым исполнением Actions-v1: ручной запуск шага "
                "заблокирован (409), запуск идёт только через панель Actions-v1")
    return None


def _canvas_node(meta: dict | None, node_key: str) -> dict | None:
    from app.services.vibecode_catalog import find_canvas_node
    return find_canvas_node(meta or {}, node_key=node_key)


async def _node_exists(ctx: ActionContext, params: dict) -> str | None:
    proj = await _get_project(ctx, params["project_id"])
    if _canvas_node(proj.get("meta"), params["node_key"]) is None:
        return f"нода {params['node_key']!r} не найдена на канвасе проекта"
    return None


async def _node_is_gpt(ctx: ActionContext, params: dict) -> str | None:
    from app.services.excel_gpt_node import effective_node_type
    proj = await _get_project(ctx, params["project_id"])
    node = _canvas_node(proj.get("meta"), params["node_key"])
    if node is None:
        return f"нода {params['node_key']!r} не найдена на канвасе проекта"
    if effective_node_type(node) != _NODE_RUN_STEP:
        return "запуск доступен только для нод «Работа с GPT» (excel_gpt)"
    if params["node_key"] in [str(x) for x in ((proj.get("meta") or {}).get("disabled_nodes") or [])]:
        return "нода отключена"
    return None


def _forced_vibecode(node_key: str) -> bool:
    from app.services.node_groups import is_script_frames_qc_gpt_node
    return is_script_frames_qc_gpt_node(None, node_key=node_key)


_PRE_PROJECT = Precondition("project_exists", "Проект существует", _project_exists)
_PRE_IDLE = Precondition("project_idle", "В проекте не выполняется шаг", _project_idle)
_PRE_NO_MONTAGE = Precondition("no_montage_jobs", "Не идут задачи панели монтажа",
                               _no_montage_jobs)
_PRE_NOT_MANAGED = Precondition("not_managed", "Проект не под управляемым исполнением Actions-v1",
                                _not_managed)
_PRE_NODE = Precondition("node_exists", "Нода есть на канвасе проекта", _node_exists)
_PRE_NODE_GPT = Precondition("node_runnable", "Нода «Работа с GPT» существует и не отключена",
                             _node_is_gpt)


# --- project.stop -----------------------------------------------------------

async def _stop_project(ctx: ActionContext, params: dict) -> dict:
    """Глобальный стоп проекта; «нечего останавливать» (400) не ошибка."""
    try:
        data = await ctx.call("POST", f"/projects/{params['project_id']}/stop")
    except ActionCallError as exc:
        if exc.status == 400:
            return {"stopped": False, "message": str(exc.detail)}
        raise
    return {"stopped": True, "message": data.get("message"),
            "generation_still_active": data.get("generation_still_active"),
            "xlsx_stopped": data.get("xlsx_stopped")}


# --- shot.* (меню съёмки) ---------------------------------------------------

def _shot_base(pid: int) -> str:
    return f"/db/projects/{pid}/shot-menu"


async def _shot_get_menu(ctx: ActionContext, params: dict) -> dict:
    menu = await ctx.call("GET", _shot_base(params["project_id"]))
    cells = (menu or {}).get("cells") or []
    return {"cell_count": len(cells), "cells": cells[:_MAX_CELLS],
            "cells_truncated": len(cells) > _MAX_CELLS}


async def _shot_edit_cell(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("PATCH", f"{_shot_base(params['project_id'])}/cell",
                          json={"parent_uuid": params["parent_uuid"],
                                "voiceover": params["voiceover"]})
    return data if isinstance(data, dict) else {"ok": True}


async def _shot_add_cell(ctx: ActionContext, params: dict) -> dict:
    body: dict = {"voiceover": params.get("voiceover", "")}
    if "before_index" in params:
        body["before_index"] = params["before_index"]
    data = await ctx.call("POST", f"{_shot_base(params['project_id'])}/cell", json=body)
    return data if isinstance(data, dict) else {"ok": True}


async def _shot_edit_field(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("PATCH", f"{_shot_base(params['project_id'])}/shot-field",
                          json={"frame_uuid": params["frame_uuid"], "field": params["field"],
                                "value": params["value"]})
    return data if isinstance(data, dict) else {"ok": True}


# --- group.* ----------------------------------------------------------------

async def _group_list(ctx: ActionContext, params: dict) -> dict:
    groups = await ctx.call("GET", "/node-groups")
    return {"count": len(groups), "groups": [
        {k: g.get(k) for k in ("id", "title", "description", "category", "node_count",
                               "builtin")} for g in groups]}


async def _group_get(ctx: ActionContext, params: dict) -> dict:
    return await ctx.call("GET", f"/node-groups/{params['group_id']}")


async def _find_group(ctx: ActionContext, group_id: str) -> dict | None:
    for g in await ctx.call("GET", "/node-groups"):
        if g.get("id") == group_id:
            return g
    return None


async def _group_exists(ctx: ActionContext, params: dict) -> str | None:
    if await _find_group(ctx, params["group_id"]) is None:
        return f"группа {params['group_id']!r} не найдена"
    return None


async def _group_is_custom(ctx: ActionContext, params: dict) -> str | None:
    group = await _find_group(ctx, params["group_id"])
    if group is None:
        return f"группа {params['group_id']!r} не найдена"
    if group.get("builtin"):
        return "группа встроенная: менять и удалять можно только пользовательские"
    return None


async def _group_id_free(ctx: ActionContext, params: dict) -> str | None:
    gid = params.get("group_id")
    if gid and await _find_group(ctx, gid) is not None:
        return f"группа {gid!r} уже есть: перезапись запрещена, укажите другой id"
    return None


async def _group_insert(ctx: ActionContext, params: dict) -> dict:
    body = {"after": params["after"]} if params.get("after") else None
    data = await ctx.call("POST", f"/projects/{params['project_id']}/canvas/groups/"
                                  f"{params['group_id']}", json=body)
    return {"group_id": params["group_id"], "result": data}


async def _group_from_selection(ctx: ActionContext, params: dict) -> dict:
    body = {k: params[k] for k in ("node_ids", "title", "description", "category", "group_id")
            if k in params}
    return await ctx.call("POST", f"/projects/{params['project_id']}/node-groups/from-selection",
                          json=body)


async def _group_update(ctx: ActionContext, params: dict) -> dict:
    body = {k: params[k] for k in ("title", "description", "category") if k in params}
    return await ctx.call("PUT", f"/node-groups/{params['group_id']}", json=body)


async def _has_group_patch(ctx: ActionContext, params: dict) -> str | None:
    if not any(k in params for k in ("title", "description", "category")):
        return "не указано ни одного поля для изменения"
    return None


async def _group_delete(ctx: ActionContext, params: dict) -> dict:
    return await ctx.call("DELETE", f"/node-groups/{params['group_id']}")


async def _group_shots_report(ctx: ActionContext, params: dict) -> dict:
    html = await ctx.call("GET", f"/projects/{params['project_id']}/shots-report")
    text = html if isinstance(html, str) else str(html)
    return {"chars": len(text), "html_head": text[:3000], "truncated": len(text) > 3000}


# --- node.* -----------------------------------------------------------------

async def _node_get_config(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("GET", f"/projects/{params['project_id']}/gpt-operator/"
                                 f"{params['node_key']}/resolve")
    return {"resolve": data, "forced_vibecode": _forced_vibecode(params["node_key"])}


async def _node_get_llm_route(ctx: ActionContext, params: dict) -> dict:
    forced = _forced_vibecode(params["node_key"])
    return {"node_key": params["node_key"], "forced_vibecode": forced,
            "note": ("ноды группы script_frames_qc всегда идут через Vibecode «GPT 5.6 Sol»; "
                     "выбор модели на ноде игнорируется" if forced else
                     "модель берётся из выбора на ноде")}


_CONFIG_FIELDS = ("role", "outputMode", "emitKinds", "takeFromEdges", "useSnapshot",
                  "checkMode", "checkFix", "checkPromptSource")


async def _config_has_changes(ctx: ActionContext, params: dict) -> str | None:
    if not any(k in params for k in _CONFIG_FIELDS):
        return "не указано ни одного поля настройки"
    return None


async def _config_output_mode_allowed(ctx: ActionContext, params: dict) -> str | None:
    if "outputMode" in params and _forced_vibecode(params["node_key"]):
        return ("режим выхода нод script_frames_qc не меняется: он нужен для записи "
                "результата в БД")
    return None


async def _node_set_config(ctx: ActionContext, params: dict) -> dict:
    body = {k: params[k] for k in _CONFIG_FIELDS if k in params}
    data = await ctx.call("PATCH", f"/projects/{params['project_id']}/gpt-operator/"
                                   f"{params['node_key']}", json=body)
    return {"ok": bool(data.get("ok")),
            "config": ((data.get("resolve") or {}).get("config")) or {}}


async def _node_set_prompt_variant(ctx: ActionContext, params: dict) -> dict:
    proj = await _get_project(ctx, params["project_id"])
    allv = {k: dict(v) if isinstance(v, dict) else {}
            for k, v in ((proj.get("meta") or {}).get("prompt_slot_variants") or {}).items()}
    node = allv.setdefault(params["node_key"], {})
    node[params.get("slot_id", "main")] = params["variant"]
    await ctx.call("PATCH", f"/projects/{params['project_id']}",
                   json={"meta": {"prompt_slot_variants": allv}})
    return {"node_key": params["node_key"], "slot_id": params.get("slot_id", "main"),
            "variant": params["variant"]}


async def _node_set_disabled(ctx: ActionContext, params: dict) -> dict:
    proj = await _get_project(ctx, params["project_id"])
    cur = [str(x) for x in ((proj.get("meta") or {}).get("disabled_nodes") or [])]
    key = params["node_key"]
    new = [k for k in cur if k != key] + ([key] if params["disabled"] else [])
    await ctx.call("PATCH", f"/projects/{params['project_id']}",
                   json={"meta": {"disabled_nodes": new}})
    return {"node_key": key, "disabled": params["disabled"], "disabled_nodes": new}


async def _node_run_step(ctx: ActionContext, params: dict) -> dict:
    await ctx.call("POST", f"/projects/{params['project_id']}/steps/{_NODE_RUN_STEP}/run",
                   params={"node_key": params["node_key"], "mode": "resume"})
    return {"started": True, "node_key": params["node_key"]}


async def _node_run_poll(ctx: ActionContext, params: dict, started: dict) -> JobState:
    pid = params["project_id"]
    proj = await _get_project(ctx, pid)
    status = proj.get("status")
    if _is_running(status):
        return JobState(done=False, note=f"статус проекта {status}")
    error = None
    try:
        run = await ctx.call("GET", f"/projects/{pid}/run")
        for nr in (run or {}).get("node_runs") or []:
            if nr.get("node_key") == params["node_key"] and nr.get("status") == "failed":
                error = nr.get("error") or "нода завершилась с ошибкой"
    except ActionCallError:
        pass
    if error:
        return JobState(done=True, ok=False, error=error, note=f"статус проекта {status}")
    return JobState(done=True, ok=True, note=f"статус проекта {status}",
                    result={"final_status": status})


def _node_run_estimate(params: dict) -> CostEstimate:
    key = str(params.get("node_key") or "")
    if key.endswith("_fw_report") or "_fw_report_" in key:
        return CostEstimate(0.0, 0.0, "код-нода отчёта, LLM не вызывает")
    return CostEstimate.unknown(
        "LLM-прогон ноды идёт через Vibecode; цена не откалибрована и расход не "
        "возвращается (число вызовов зависит от числа ячеек)")


def _node_run_effects(params: dict) -> str:
    return ("Запуск шага убивает активную генерацию проекта, снимает стоп и паузу очереди. "
            f"Нода {params['node_key']} перезапишет в БД поля сцен и кадров всех ячеек; "
            "прежние значения не сохраняются.")


async def _node_reset_precondition(ctx: ActionContext, params: dict) -> str | None:
    steps = await ctx.call("GET", "/projects/steps/catalog")
    if params["step_code"] not in {s.get("code") for s in steps}:
        return f"неизвестный шаг {params['step_code']!r}"
    return None


async def _node_reset(ctx: ActionContext, params: dict) -> dict:
    data = await ctx.call("POST", f"/projects/{params['project_id']}/steps/"
                                  f"{params['step_code']}/reset")
    return {"step_code": params["step_code"], "status": data.get("status")}


# --- каталог ----------------------------------------------------------------

def _specs() -> list[ActionSpec]:
    pid_only = _schema({"project_id": _PID}, ["project_id"])
    node_only = _schema({"project_id": _PID, "node_key": _NODE_KEY}, ["project_id", "node_key"])
    group_only = _schema({"group_id": _GROUP_ID}, ["group_id"])
    return [
        ActionSpec(
            id="project.stop", title_ru="Остановить проект",
            description_ru="Глобальный стоп проекта: прерывает активный шаг, генерацию и задачи "
                           "монтажа. Отдельного стопа только монтажа нет. Частичные результаты "
                           "могут остаться. Если останавливать нечего, ответ stopped=false.",
            params_schema=pid_only, executor=_stop_project, risk=RISK_LOCAL_SAFE,
            idempotent=True, preconditions=(_PRE_PROJECT,), tags=("project", "stop")),

        # shot.*
        ActionSpec(
            id="shot.get_menu", title_ru="Прочитать меню съёмки",
            description_ru="Ячейки закадра и шоты. На пустой БД старого проекта один раз "
                           "подтягивает кадры из project.xlsx.",
            params_schema=pid_only, executor=_shot_get_menu, risk=RISK_READ, idempotent=True,
            preconditions=(_PRE_PROJECT,), tags=("shot",)),
        ActionSpec(
            id="shot.edit_cell_voiceover", title_ru="Изменить закадр ячейки (меню съёмки)",
            description_ru="Записывает текст закадра ячейки в БД. Прежний текст не сохраняется.",
            params_schema=_schema({"project_id": _PID,
                                   "parent_uuid": {"type": "string", "minLength": 1, "maxLength": 80},
                                   "voiceover": {"type": "string", "maxLength": 20000}},
                                  ["project_id", "parent_uuid", "voiceover"]),
            executor=_shot_edit_cell, risk=RISK_LOCAL_SAFE, idempotent=True,
            preconditions=(_PRE_PROJECT,), tags=("shot", "text")),
        ActionSpec(
            id="shot.add_cell", title_ru="Добавить ячейку в меню съёмки",
            description_ru="Новая пустая ячейка перед ячейкой с индексом before_index (без индекса "
                           "в конец), дробный sort_key. Повтор создаст ещё одну ячейку.",
            params_schema=_schema({"project_id": _PID,
                                   "before_index": {"type": "integer", "minimum": 0},
                                   "voiceover": {"type": "string", "maxLength": 20000}},
                                  ["project_id"]),
            executor=_shot_add_cell, risk=RISK_LOCAL_SAFE, idempotent=False,
            preconditions=(_PRE_PROJECT,), tags=("shot", "structure")),
        ActionSpec(
            id="shot.edit_field", title_ru="Изменить поле шота (меню съёмки)",
            description_ru="Действие, персонажи, стык, сцена, площадка, крупность, движение или "
                           "промты шота: запись в БД без запуска генерации.",
            params_schema=_schema({"project_id": _PID,
                                   "frame_uuid": {"type": "string", "minLength": 1, "maxLength": 80},
                                   "field": {"type": "string", "enum": _SHOT_FIELDS},
                                   "value": {"type": "string", "maxLength": 8000}},
                                  ["project_id", "frame_uuid", "field", "value"]),
            executor=_shot_edit_field, risk=RISK_LOCAL_SAFE, idempotent=True,
            preconditions=(_PRE_PROJECT,), tags=("shot", "text")),

        # group.*
        ActionSpec(
            id="group.list", title_ru="Каталог групп нод",
            description_ru="Встроенные и пользовательские группы нод.",
            params_schema=_schema({}, []), executor=_group_list, risk=RISK_READ, idempotent=True,
            scope="global", tags=("group",)),
        ActionSpec(
            id="group.get", title_ru="Состав группы нод",
            description_ru="Полный spec группы: ноды, связи, промты.",
            params_schema=group_only, executor=_group_get, risk=RISK_READ, idempotent=True,
            scope="global", preconditions=(Precondition(
                "group_exists", "Группа существует", _group_exists),), tags=("group",)),
        ActionSpec(
            id="group.shots_report", title_ru="Отчёт «Кадры» (script_frames_qc)",
            description_ru="Читает готовый HTML-отчёт ноды fw_report (первые 3000 символов). "
                           "Отчёт не пересчитывается.",
            params_schema=pid_only, executor=_group_shots_report, risk=RISK_READ,
            idempotent=True, preconditions=(_PRE_PROJECT,), tags=("group", "report")),
        ActionSpec(
            id="group.insert", title_ru="Вставить группу нод в канвас",
            description_ru="Добавляет ноды группы, связи, промт-варианты и флаги в канвас проекта. "
                           "Запуска нод нет. Повторная вставка добавит вторую копию.",
            params_schema=_schema({"project_id": _PID, "group_id": _GROUP_ID,
                                   "after": {"type": "string", "maxLength": 80}},
                                  ["project_id", "group_id"]),
            executor=_group_insert, risk=RISK_LOCAL_SAFE, idempotent=False,
            preconditions=(_PRE_PROJECT, _PRE_IDLE, Precondition(
                "group_exists", "Группа существует", _group_exists)),
            tags=("group", "canvas")),
        ActionSpec(
            id="group.save_from_selection", title_ru="Сохранить выделение как группу",
            description_ru="Записывает выбранные ноды канваса как новую пользовательскую группу "
                           "(файл в node_groups/). Существующую группу не перезаписывает.",
            params_schema=_schema({
                "project_id": _PID,
                "node_ids": {"type": "array", "minItems": 1, "maxItems": 60,
                             "items": {"type": "string", "minLength": 1, "maxLength": 80}},
                "title": {"type": "string", "minLength": 1, "maxLength": 120},
                "description": {"type": "string", "maxLength": 1000},
                "category": {"type": "string", "maxLength": 40},
                "group_id": _GROUP_ID}, ["project_id", "node_ids", "title"]),
            executor=_group_from_selection, risk=RISK_LOCAL_SAFE, idempotent=False,
            preconditions=(_PRE_PROJECT, Precondition(
                "group_id_free", "Группы с таким id ещё нет", _group_id_free)),
            tags=("group",)),
        ActionSpec(
            id="group.update", title_ru="Переименовать пользовательскую группу",
            description_ru="Название, описание или категория пользовательской группы.",
            params_schema=_schema({"group_id": _GROUP_ID,
                                   "title": {"type": "string", "minLength": 1, "maxLength": 120},
                                   "description": {"type": "string", "maxLength": 1000},
                                   "category": {"type": "string", "maxLength": 40}},
                                  ["group_id"]),
            executor=_group_update, risk=RISK_LOCAL_SAFE, idempotent=True, scope="global",
            preconditions=(Precondition("group_custom", "Группа пользовательская",
                                        _group_is_custom),
                           Precondition("has_patch", "Есть что менять", _has_group_patch)),
            tags=("group",)),
        ActionSpec(
            id="group.delete", title_ru="Удалить пользовательскую группу",
            description_ru="Удаляет файл пользовательской группы. Встроенные не удаляются. "
                           "Ноды, уже вставленные в проекты, остаются.",
            params_schema=group_only, executor=_group_delete, risk=RISK_DESTRUCTIVE,
            destructive=True, reversible=False, idempotent=False, scope="global",
            destructive_effects=lambda p: (f"Файл группы {p['group_id']} будет удалён "
                                           "без возможности восстановления."),
            preconditions=(Precondition("group_custom", "Группа пользовательская",
                                        _group_is_custom),),
            tags=("group",)),

        # node.*
        ActionSpec(
            id="node.get_config", title_ru="Настройки ноды «Работа с GPT»",
            description_ru="Роль, режим выхода, виды выхода, режим проверки и признак принудительного "
                           "Vibecode.",
            params_schema=node_only, executor=_node_get_config, risk=RISK_READ, idempotent=True,
            preconditions=(_PRE_PROJECT, _PRE_NODE), tags=("node",)),
        ActionSpec(
            id="node.get_llm_route", title_ru="Через какую модель идёт нода",
            description_ru="Для нод script_frames_qc выбор модели игнорируется: всегда Vibecode "
                           "«GPT 5.6 Sol». Менять модель такой ноды нельзя, действия смены нет.",
            params_schema=node_only, executor=_node_get_llm_route, risk=RISK_READ,
            idempotent=True, preconditions=(_PRE_PROJECT,), tags=("node", "model")),
        ActionSpec(
            id="node.set_config", title_ru="Изменить пульт оператора ноды",
            description_ru="Роль, режим и виды выхода, «брать со стрелок», снимок и режим проверки. "
                           "Только конфиг ноды, запуска нет. Для нод script_frames_qc режим выхода "
                           "менять нельзя.",
            params_schema=_schema({
                "project_id": _PID, "node_key": _NODE_KEY,
                "role": {"type": "string", "enum": _ROLES},
                "outputMode": {"type": "string", "enum": _OUTPUT_MODES},
                "emitKinds": {"type": "array", "maxItems": 4,
                              "items": {"type": "string", "enum": _EMIT_KINDS}},
                "takeFromEdges": {"type": "boolean"}, "useSnapshot": {"type": "boolean"},
                "checkMode": {"type": "boolean"}, "checkFix": {"type": "boolean"},
                "checkPromptSource": {"type": "string", "enum": ["upstream", "agent"]}},
                ["project_id", "node_key"]),
            executor=_node_set_config, risk=RISK_LOCAL_SAFE, idempotent=True,
            future_spend_note="Режим проверки (checkMode/checkFix) и выход ноды меняют число платных "
                              "вызовов при следующем запуске.",
            preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE,
                           Precondition("has_changes", "Указано хотя бы одно поле",
                                        _config_has_changes),
                           Precondition("output_mode_allowed",
                                        "Режим выхода не меняется у нод script_frames_qc",
                                        _config_output_mode_allowed)),
            tags=("node", "config")),
        ActionSpec(
            id="node.set_prompt_variant", title_ru="Выбрать вариант промта слота ноды",
            description_ru="Привязывает файл промта к слоту ноды в этом проекте (meta). Сам файл "
                           "промта не меняется. Существование файла на сервере не проверяется.",
            params_schema=_schema({
                "project_id": _PID, "node_key": _NODE_KEY,
                "slot_id": {"type": "string", "minLength": 1, "maxLength": 60,
                            "pattern": r"^[A-Za-z0-9_\-]+$"},
                "variant": {"type": "string", "minLength": 1, "maxLength": 120,
                            "pattern": r"^[A-Za-z0-9_.\-]+$"}},
                ["project_id", "node_key", "variant"]),
            executor=_node_set_prompt_variant, risk=RISK_LOCAL_SAFE, idempotent=True,
            preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE), tags=("node", "prompt")),
        ActionSpec(
            id="node.set_disabled", title_ru="Отключить или включить ноду",
            description_ru="Пишет ноду в meta.disabled_nodes проекта или убирает оттуда. "
                           "Отключённую ноду запуск шага не выполняет.",
            params_schema=_schema({"project_id": _PID, "node_key": _NODE_KEY,
                                   "disabled": {"type": "boolean"}},
                                  ["project_id", "node_key", "disabled"]),
            executor=_node_set_disabled, risk=RISK_LOCAL_SAFE, idempotent=True,
            preconditions=(_PRE_PROJECT, _PRE_IDLE, _PRE_NODE), tags=("node", "config")),
        ActionSpec(
            id="node.run_step", title_ru="Запустить шаг на ноде «Работа с GPT»",
            description_ru="Явный старт шага ноды (режим resume): убивает активную генерацию проекта "
                           "и перезаписывает в БД выход ноды по всем ячейкам. LLM идёт через Vibecode. "
                           "Заблокирован, если в проекте что-то выполняется или проект под "
                           "Actions-v1. Останавливается глобальным стопом проекта.",
            params_schema=node_only, executor=_node_run_step, poll=_node_run_poll,
            stop=_stop_project, poll_interval_s=3.0,
            risk=RISK_DESTRUCTIVE, spends_money=True, destructive=True, reversible=False,
            idempotent=False, estimator=_node_run_estimate,
            destructive_effects=_node_run_effects,
            preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE, _PRE_NO_MONTAGE,
                           _PRE_NODE_GPT),
            tags=("node", "run")),
        ActionSpec(
            id="node.reset_from_step", title_ru="Сбросить шаг и всё после него",
            description_ru="Стирает результаты шага и всех шагов ниже по конвейеру и пересчитывает "
                           "статус проекта. Запуска нет.",
            params_schema=_schema({"project_id": _PID,
                                   "step_code": {"type": "string", "minLength": 1, "maxLength": 40,
                                                 "pattern": r"^[a-z0-9_]+$"}},
                                  ["project_id", "step_code"]),
            executor=_node_reset, risk=RISK_DESTRUCTIVE, destructive=True, reversible=False,
            idempotent=False,
            destructive_effects=lambda p: (f"Данные шага {p['step_code']} и всех последующих "
                                           "шагов конвейера будут удалены."),
            preconditions=(_PRE_PROJECT, _PRE_NOT_MANAGED, _PRE_IDLE, _PRE_NO_MONTAGE,
                           Precondition("step_known", "Шаг есть в каталоге шагов",
                                        _node_reset_precondition)),
            tags=("node", "reset")),
    ]


def register_group_actions(registry: ActionRegistry) -> None:
    registry.register_many(_specs())
