"""Макросы («умные кнопки»): именованные готовые планы с параметрами.

Макрос не исполняется сам: он разворачивается в обычные шаги и проходит тот же
preview, подтверждение, бюджет и журнал, что и план планировщика. Ссылаться
макрос может только на зарегистрированные действия; встроенные макросы могут
ссылаться на id, которые пока не зарегистрированы (тогда макрос «недоступен»).
"""
from __future__ import annotations

import copy
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.services.action_layer import schema as schema_mod
from app.services.action_layer import store
from app.services.action_layer.spec import ActionRegistry, ensure_builtin_actions
from app.services.action_layer.spec import registry as default_registry

_PLACEHOLDER = re.compile(r"\{\{\s*([a-z_][a-z0-9_]*)\s*\}\}")
_ACTION_ID = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
_USER_ID = re.compile(r"^user\.[a-z0-9_]{2,40}$")
MAX_USER_MACROS = 50


class MacroError(ValueError):
    def __init__(self, errors: list[str] | str):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        super().__init__("; ".join(self.errors))


class MacroUnavailable(Exception):
    def __init__(self, macro_id: str, missing: list[str]):
        self.macro_id, self.missing = macro_id, missing
        super().__init__(f"macro {macro_id} references unregistered actions: {missing}")


class MacroForbidden(Exception):
    pass


@dataclass(frozen=True)
class MacroSpec:
    id: str
    title_ru: str
    description_ru: str
    steps: tuple[dict, ...]
    params_schema: dict
    builtin: bool = False
    owner: str = ""
    shared: bool = False

    def action_ids(self) -> list[str]:
        return [s["action_id"] for s in self.steps]

    def missing_actions(self, registry: ActionRegistry) -> list[str]:
        return sorted({a for a in self.action_ids() if registry.find(a) is None})

    def to_json(self, registry: ActionRegistry, operator: str = "") -> dict:
        missing = self.missing_actions(registry)
        return {"id": self.id, "title_ru": self.title_ru, "description_ru": self.description_ru,
                "steps": [copy.deepcopy(s) for s in self.steps],
                "params_schema": self.params_schema, "builtin": self.builtin,
                "owner": self.owner, "shared": self.shared, "mine": bool(
                    self.owner and self.owner == operator),
                "available": not missing, "missing_action_ids": missing}


# --- шаблоны ----------------------------------------------------------------

def _placeholders(node: Any) -> set[str]:
    if isinstance(node, str):
        return set(_PLACEHOLDER.findall(node))
    if isinstance(node, dict):
        out: set[str] = set()
        for key, value in node.items():
            if isinstance(key, str) and _PLACEHOLDER.search(key):
                out.add("<key>")
            out |= _placeholders(value)
        return out
    if isinstance(node, list):
        out = set()
        for item in node:
            out |= _placeholders(item)
        return out
    return set()


_OMIT = object()


def _substitute(node: Any, values: dict) -> Any:
    if isinstance(node, str):
        whole = _PLACEHOLDER.fullmatch(node.strip())
        if whole:
            return values.get(whole.group(1), _OMIT)
        return _PLACEHOLDER.sub(lambda m: str(values.get(m.group(1), "")), node)
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            res = _substitute(value, values)
            if res is not _OMIT:  # необязательный параметр не задан - поле опускается
                out[key] = res
        return out
    if isinstance(node, list):
        res_list = [_substitute(i, values) for i in node]
        if any(r is _OMIT for r in res_list):
            raise MacroError("Необязательный параметр нельзя использовать как элемент списка.")
        return res_list
    return copy.deepcopy(node)


def _max_steps() -> int:
    from app.settings import settings
    return int(settings.action_layer_max_steps)


def validate_macro(title_ru: str, steps: Any, params_schema: Any, registry: ActionRegistry, *,
                   require_registered: bool, description_ru: str = "") -> list[str]:
    """Список ошибок (пустой = макрос корректен). Ничего не бросает."""
    errors: list[str] = []
    if not isinstance(title_ru, str) or not title_ru.strip() or len(title_ru) > 200:
        errors.append("title_ru: 1..200 characters")
    if not isinstance(description_ru, str) or len(description_ru) > 2000:
        errors.append("description_ru: up to 2000 characters")
    declared: dict = {}
    if not isinstance(params_schema, dict):
        errors.append("params_schema must be an object")
    else:
        errors += [f"params_schema: {e}" for e in schema_mod.check_schema(params_schema)]
        if (params_schema.get("type") != "object"
                or params_schema.get("additionalProperties") is not False):
            errors.append("params_schema must be type=object with additionalProperties=false")
        declared = params_schema.get("properties") or {}
    if not isinstance(steps, list) or not steps:
        return errors + ["steps must be a non-empty list"]
    if len(steps) > _max_steps():
        errors.append(f"too many steps (max {_max_steps()})")
    for i, step in enumerate(steps):
        where = f"step {i}"
        if not isinstance(step, dict):
            errors.append(f"{where}: must be an object")
            continue
        extra = set(step) - {"action_id", "params", "depends_on"}
        if extra:
            errors.append(f"{where}: unknown fields {sorted(extra)}")
        action_id, params = step.get("action_id"), step.get("params", {})
        deps = step.get("depends_on", [])
        if not isinstance(action_id, str) or not _ACTION_ID.match(action_id):
            errors.append(f"{where}: invalid action_id")
            continue
        if not isinstance(params, dict):
            errors.append(f"{where}: params must be an object")
            continue
        if (not isinstance(deps, list) or any(
                not isinstance(d, int) or isinstance(d, bool) or not 0 <= d < i for d in deps)):
            errors.append(f"{where}: depends_on must point to earlier steps")
        used = _placeholders(params)
        if "<key>" in used:
            errors.append(f"{where}: placeholders are not allowed in keys")
        for name in sorted(used - {"<key>"}):
            if name not in declared:
                errors.append(f"{where}: placeholder {name!r} is not in params_schema")
        spec = registry.find(action_id)
        if spec is None:
            if require_registered:
                errors.append(f"{where}: unknown action {action_id!r}")
            continue
        props = spec.params_schema.get("properties") or {}
        for key, value in params.items():
            if key not in props:
                errors.append(f"{where}: {action_id} has no parameter {key!r}")
                continue
            names = _placeholders(value)
            if not names:
                errors += [f"{where}.{key}: {e}" for e in schema_mod.validate(props[key], value, "$")]
            elif isinstance(value, str) and _PLACEHOLDER.fullmatch(value.strip()):
                want, have = props[key].get("type"), (declared.get(next(iter(names))) or {}).get("type")
                if isinstance(want, str) and isinstance(have, str) and want != have:
                    errors.append(f"{where}.{key}: expects {want}, macro parameter is {have}")
        for req in spec.params_schema.get("required") or []:
            if req not in params:
                errors.append(f"{where}: {action_id} requires parameter {req!r}")
    return errors


def expand(macro: MacroSpec, params: Any, registry: ActionRegistry) -> list[dict]:
    """Подставляет параметры. Недоступный макрос = MacroUnavailable."""
    missing = macro.missing_actions(registry)
    if missing:
        raise MacroUnavailable(macro.id, missing)
    if not isinstance(params, dict):
        raise MacroError("Параметры умной кнопки должны быть объектом.")
    errors = schema_mod.validate(macro.params_schema, params)
    if errors:
        raise MacroError(errors)
    out = []
    for step in macro.steps:
        out.append({"action_id": step["action_id"],
                    "params": _substitute(step.get("params", {}), params),
                    "depends_on": list(step.get("depends_on", []))})
    return out


# --- встроенные макросы -----------------------------------------------------

_PID = {"type": "integer", "minimum": 1}


def _params(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": False}


def _chain(*steps: tuple[str, dict]) -> tuple[dict, ...]:
    return tuple({"action_id": a, "params": p, "depends_on": [i - 1] if i else []}
                 for i, (a, p) in enumerate(steps))


_P = {"project_id": "{{project_id}}"}

_IMG_RES = {"type": "string", "enum": ["1k", "2k", "3k", "4k"]}
_VID_RES = {"type": "string", "enum": ["720p", "1080p"]}
_STR = {"type": "string", "minLength": 1}
_GEN = {"type": "string", "pattern": "^[a-z0-9_]{1,40}$"}
_COST_NOTE = (" Платные шаги требуют явных лимитов и настроек генерации: они вынесены в "
              "параметры макроса (текущие значения даёт montage.get_cost_basis). "
              "План нужно подтвердить оператором.")

# Только реально зарегистрированные действия; проверяется тестом на реестре.
BUILTIN_MACROS: tuple[MacroSpec, ...] = (
    MacroSpec(
        "smart.check_board", "Проверить доску монтажа",
        "Читает доску и статусы фоновых задач. Ничего не меняет и не тратит.",
        _chain(("montage.get_board", dict(_P)), ("montage.apply_status", dict(_P)),
               ("montage.montage_status", dict(_P)), ("montage.recover_status", dict(_P))),
        _params({"project_id": _PID}, ["project_id"]), builtin=True),
    MacroSpec(
        "smart.cost_basis", "Текущие настройки генерации и база стоимости",
        "Читает генераторы, разрешения и данные для оценки стоимости. Ничего не меняет.",
        _chain(("montage.get_cost_basis", dict(_P))),
        _params({"project_id": _PID}, ["project_id"]), builtin=True),
    MacroSpec(
        "smart.queue_frame_regen", "Поставить кадр на перегенерацию картинки",
        "Кладёт перегенерацию картинки кадра в очередь. Генерация не запускается.",
        _chain(("montage.queue_ops", {**_P, "ops": [
            {"type": "image_regen", "frame_number": "{{frame_number}}"}]})),
        _params({"project_id": _PID, "frame_number": _PID}, ["project_id", "frame_number"]),
        builtin=True),
    MacroSpec(
        "smart.fix_voiceover", "Исправить закадр и проверить доску",
        "Записывает новый текст закадра кадра и перечитывает доску.",
        _chain(("montage.set_voiceover", {**_P, "frame_id": "{{frame_id}}", "text": "{{text}}"}),
               ("montage.get_board", dict(_P))),
        _params({"project_id": _PID, "frame_id": _PID,
                 "text": {"type": "string", "maxLength": 20000}},
                ["project_id", "frame_id", "text"]), builtin=True),
    MacroSpec(
        "smart.scene_from_scratch", "Сцена с нуля: разделить, улучшить, применить, смонтировать",
        "Делит сцену, улучшает её по заказу (с генерацией картинок), применяет очередь правок "
        "(видео и остальное) и пересобирает ролик. Разделение и улучшение разрушающие."
        + _COST_NOTE,
        _chain(("montage.split_scene", {**_P, "frame_id": "{{frame_id}}"}),
               ("montage.improve_scene", {**_P, "frame_id": "{{frame_id}}",
                                          "prompt": "{{prompt}}",
                                          "max_image_ops": "{{max_image_ops}}",
                                          "image_generator": "{{image_generator}}",
                                          "image_resolution": "{{image_resolution}}"}),
               ("montage.apply_queue", {**_P, "max_image_ops": "{{max_image_ops}}",
                                        "max_video_ops": "{{max_video_ops}}",
                                        "image_generator": "{{image_generator}}",
                                        "image_resolution": "{{image_resolution}}",
                                        "video_generator": "{{video_generator}}",
                                        "video_resolution": "{{video_resolution}}"}),
               ("montage.run_montage", dict(_P))),
        _params({"project_id": _PID, "frame_id": _PID, "prompt": _STR,
                 "max_image_ops": {"type": "integer", "minimum": 1, "maximum": 25},
                 "max_video_ops": {"type": "integer", "minimum": 0, "maximum": 100},
                 "image_generator": _GEN, "image_resolution": _IMG_RES,
                 "video_generator": _GEN, "video_resolution": _VID_RES},
                ["project_id", "frame_id", "prompt", "max_image_ops", "max_video_ops",
                 "image_generator", "image_resolution"]), builtin=True),
    MacroSpec(
        "smart.single_frame_fix", "Точечная правка кадра: в очередь и применить",
        "Ставит ИИ-изменение картинки кадра в очередь и применяет правки только для этого кадра."
        + _COST_NOTE,
        _chain(("montage.queue_ops", {**_P, "ops": [
                    {"type": "image_ai_change", "frame_number": "{{frame_number}}",
                     "instruction": "{{instruction}}"}]}),
               ("montage.apply_queue", {**_P, "frame_numbers": ["{{frame_number}}"],
                                        "max_image_ops": "{{max_image_ops}}",
                                        "max_video_ops": 0,
                                        "image_generator": "{{image_generator}}",
                                        "image_resolution": "{{image_resolution}}"})),
        _params({"project_id": _PID, "frame_number": _PID,
                 "instruction": {"type": "string", "minLength": 1, "maxLength": 4000},
                 "max_image_ops": {"type": "integer", "minimum": 1, "maximum": 200},
                 "image_generator": _GEN, "image_resolution": _IMG_RES},
                ["project_id", "frame_number", "instruction", "max_image_ops"]), builtin=True),
    MacroSpec(
        "smart.recover_generation", "Восстановить после сбоя генерации",
        "Смотрит статус применения правок и забирает готовые карточки из галереи.",
        _chain(("montage.apply_status", dict(_P)), ("montage.recover_outsee", dict(_P))),
        _params({"project_id": _PID}, ["project_id"]), builtin=True),
    MacroSpec(
        "smart.assemble_video", "Разобрать аудио и пересобрать видео",
        "Выравнивает озвучку по таймкодам и запускает монтаж. Платных вызовов нет, но прежний final.mp4 "
        "удаляется, поэтому план нужно подтвердить.",
        _chain(("montage.align_audio", dict(_P)), ("montage.run_montage", dict(_P))),
        _params({"project_id": _PID}, ["project_id"]), builtin=True),
    MacroSpec(
        "smart.voiceover_remount", "Исправить закадр, разобрать аудио и пересобрать видео",
        "Записывает новый текст закадра кадра, выравнивает озвучку по таймкодам и запускает монтаж "
        "(правка закадра сама таймкоды не пересчитывает). Платных вызовов нет, но прежний "
        "final.mp4 удаляется, поэтому план нужно подтвердить.",
        _chain(("montage.set_voiceover", {**_P, "frame_id": "{{frame_id}}", "text": "{{text}}"}),
               ("montage.align_audio", dict(_P)), ("montage.run_montage", dict(_P))),
        _params({"project_id": _PID, "frame_id": _PID,
                 "text": {"type": "string", "maxLength": 20000}},
                ["project_id", "frame_id", "text"]), builtin=True),
    MacroSpec(
        "smart.assemble_settings_remount", "Настройки сборки и пересборка видео",
        "Меняет громкость музыки (0..100) и сразу запускает монтаж. Платных вызовов нет, но "
        "прежний final.mp4 удаляется, поэтому план нужно подтвердить.",
        _chain(("montage.set_assemble_params", {**_P, "bgm_level": "{{bgm_level}}"}),
               ("montage.run_montage", dict(_P))),
        _params({"project_id": _PID, "bgm_level": {"type": "integer", "minimum": 0, "maximum": 100}},
                ["project_id", "bgm_level"]), builtin=True),
    MacroSpec(
        "smart.delete_frame_check", "Удалить кадр и перечитать доску",
        "Удаляет кадр (медиа уходит в карантин) и читает доску: номера кадров после удаления "
        "сдвигаются, поэтому дальнейшие номера берите из новой доски. Разрушающее действие, "
        "план нужно подтвердить.",
        _chain(("montage.delete_frame", {**_P, "frame_id": "{{frame_id}}"}),
               ("montage.get_board", dict(_P))),
        _params({"project_id": _PID, "frame_id": _PID}, ["project_id", "frame_id"]),
        builtin=True),
    MacroSpec(
        "smart.stop_and_check", "Остановить проект и показать, что осталось",
        "Останавливает проект (частичные результаты могут остаться) и читает статусы применения "
        "правок и монтажа.",
        _chain(("project.stop", dict(_P)), ("montage.apply_status", dict(_P)),
               ("montage.montage_status", dict(_P))),
        _params({"project_id": _PID}, ["project_id"]), builtin=True),
    MacroSpec(
        "smart.shots_report", "Отчёт по шотам и меню съёмки",
        "Читает отчёт по кадрам группы проверки и меню съёмки. Ничего не меняет.",
        _chain(("group.shots_report", dict(_P)), ("shot.get_menu", dict(_P))),
        _params({"project_id": _PID}, ["project_id"]), builtin=True),
    MacroSpec(
        "smart.run_node_step", "Запустить ноду (шаг группы)",
        "Читает настройки ноды, считает верхнюю оценку прогона (node.get_run_cost_basis) и "
        "запускает шаг. Для Каркас/Площадка/Шоты/QC передайте заявку стоимости из результата "
        "node.get_run_cost_basis (поле declare): без неё стоимость НЕИЗВЕСТНА, план можно "
        "посмотреть в preview, но нельзя подтвердить и запустить. Оценка верхняя и не "
        "откалибрована, план нужно подтвердить оператором.",
        _chain(("node.get_config", {**_P, "node_key": "{{node_key}}"}),
               ("node.get_run_cost_basis", {**_P, "node_key": "{{node_key}}"}),
               ("node.run_step", {**_P, "node_key": "{{node_key}}",
                                  "min_llm_requests": "{{min_llm_requests}}",
                                  "max_llm_requests": "{{max_llm_requests}}",
                                  "max_input_tokens": "{{max_input_tokens}}",
                                  "max_output_tokens": "{{max_output_tokens}}"})),
        _params({"project_id": _PID, "node_key": _STR,
                 "min_llm_requests": {"type": "integer", "minimum": 1, "maximum": 100000},
                 "max_llm_requests": {"type": "integer", "minimum": 1, "maximum": 100000},
                 "max_input_tokens": {"type": "integer", "minimum": 1, "maximum": 2000000},
                 "max_output_tokens": {"type": "integer", "minimum": 1, "maximum": 2000000}},
                ["project_id", "node_key"]),
        builtin=True),
)
_BUILTIN_BY_ID = {m.id: m for m in BUILTIN_MACROS}


# --- хранение пользовательских макросов -------------------------------------

def _from_row(row: "store.ActionMacro") -> MacroSpec:
    return MacroSpec(row.id, row.title_ru, row.description_ru or "",
                     tuple(row.steps or []), dict(row.params_schema or {}),
                     builtin=False, owner=row.owner, shared=bool(row.shared))


def _visible(m: MacroSpec, operator: str) -> bool:
    return m.builtin or m.shared or m.owner == operator


def _normalize_steps(steps: list) -> list[dict]:
    return [{"action_id": s["action_id"], "params": copy.deepcopy(s.get("params", {})),
             "depends_on": sorted(set(s.get("depends_on", [])))} for s in steps]


async def list_macros(operator: str) -> list[MacroSpec]:
    async with store.sessions()() as s:
        rows = (await s.scalars(select(store.ActionMacro).where(
            (store.ActionMacro.owner == operator) | store.ActionMacro.shared.is_(True)
        ).order_by(store.ActionMacro.created_at))).all()
    return list(BUILTIN_MACROS) + [_from_row(r) for r in rows]


async def get_macro(macro_id: str, operator: str) -> MacroSpec:
    """LookupError, если макроса нет или он чужой и не общий (существование не раскрывается)."""
    if macro_id in _BUILTIN_BY_ID:
        return _BUILTIN_BY_ID[macro_id]
    async with store.sessions()() as s:
        row = await s.get(store.ActionMacro, macro_id)
    if row is None or not _visible(_from_row(row), operator):
        raise LookupError("Макрос не найден.")
    return _from_row(row)


async def save_macro(operator: str, *, title_ru: str, description_ru: str, steps: Any,
                     params_schema: Any, shared: bool = False, macro_id: str | None = None,
                     registry: ActionRegistry | None = None) -> MacroSpec:
    registry = registry or default_registry
    ensure_builtin_actions()
    if not operator:
        raise MacroForbidden("Нужна личность оператора.")
    errors = validate_macro(title_ru, steps, params_schema, registry, require_registered=True,
                            description_ru=description_ru)
    if macro_id is not None and not _USER_ID.match(macro_id):
        errors.append("id must look like 'user.name' (a-z, 0-9, _)")
    if errors:
        raise MacroError(errors)
    clean = _normalize_steps(steps)
    async with store.sessions()() as s:
        row = await s.get(store.ActionMacro, macro_id) if macro_id else None
        if row is not None and row.owner != operator:
            raise MacroForbidden("Макрос принадлежит другому оператору.")
        if row is None:
            count = len((await s.scalars(select(store.ActionMacro.id).where(
                store.ActionMacro.owner == operator))).all())
            if count >= MAX_USER_MACROS:
                raise MacroError(f"Достигнут предел личных макросов ({MAX_USER_MACROS}).")
            row = store.ActionMacro(id=macro_id or "user." + uuid.uuid4().hex[:10],
                                    owner=operator, created_at=datetime.utcnow())
            s.add(row)
        row.title_ru, row.description_ru = title_ru.strip(), description_ru
        row.steps, row.params_schema, row.shared = clean, params_schema, bool(shared)
        row.updated_at = datetime.utcnow()
        await s.commit()
        return _from_row(row)


async def delete_macro(macro_id: str, operator: str) -> None:
    if macro_id in _BUILTIN_BY_ID:
        raise MacroForbidden("Встроенные макросы удалять нельзя.")
    async with store.sessions()() as s:
        row = await s.get(store.ActionMacro, macro_id)
        if row is None or not _visible(_from_row(row), operator):
            raise LookupError("Макрос не найден.")
        if row.owner != operator:
            raise MacroForbidden("Удалить макрос может только его владелец.")
        await s.delete(row)
        await s.commit()
