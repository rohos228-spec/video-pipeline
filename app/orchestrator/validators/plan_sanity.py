"""План: только whitelist, схемы аргументов, verify перед run_node, без удалений.

Источник истины — реестр ``app.orchestrator.actions.registry`` или
``app.services.action_layer.spec.registry`` после ``ensure_builtin_actions()``,
если модуль импортируется.
Иначе временный ``FALLBACK_WHITELIST``: 11 новых действий §6 плюс read-only
имена из того же параграфа, ``run_node`` и ``verify_code_state``.
``context["whitelist"]`` и ``context["registry"]`` могут только сузить это
множество. Имя вне реестра из context не становится разрешённым.

Удаление сцен и кадров блокируется даже если имя попало в whitelist.
``montage.delete_frame`` и ``montage.delete_child_frame`` проходят только вместе,
и только если сырое имя шага уже равно нормализованному токену:
``metadata_of(name).get("soft_delete") is True`` и одобрение ядра
``context["approval"]`` с ``risk == "high"`` (без strip и lower), ``destructive is True``,
``granted is True`` и ``approval["action"]``, строго равным сырому имени шага.
Нет поля ``soft_delete`` или нет ``metadata_of`` у серверного реестра модуля —
это false. ``context["registry"]`` для решения об удалении не читается.
``edit_files`` / ``exec`` / ``shell`` / ``run_tests`` в запасном списке отсутствуют
(E8).
"""

from __future__ import annotations

import importlib
import re
from typing import Any

from app.orchestrator.validators.result import make_result

_NAME = "plan_sanity"

_NODE_KEY = re.compile(r"^n_[a-z0-9_]+$")
_GROUP_KEY = re.compile(r"^[a-z0-9_]+$")
_DELETE = re.compile(
    r"(?:^|[._-])(delete|remove|drop|wipe|destroy)[._-]?(scene|shot|frame)s?(?:$|[._-])",
    re.IGNORECASE,
)
_DENY_EXACT = frozenset(
    {
        "delete_scene",
        "delete_scenes",
        "delete_shot",
        "delete_shots",
        "delete_frame",
        "delete_frames",
        "remove_scene",
        "remove_scenes",
        "remove_shot",
        "remove_shots",
        "remove_frame",
        "remove_frames",
        "drop_scene",
        "drop_shot",
        "drop_frame",
    }
)
_SECTION_MODES = frozenset({"replace_section", "section", "append_section"})
_PATCH_MODES = frozenset({"replace", "replace_full", "replace_section", "section", "append_section"})

# Запасной whitelist до реестра WS2. Не расширять молча: 102 действия #342
# приходят из registry, не из этого кортежа.
FALLBACK_WHITELIST = frozenset(
    {
        "read_pipeline_code",
        "rag_search",
        "run_validators",
        "stop_node",
        "resume_node",
        "create_node",
        "edit_node_prompt",
        "edit_node_params",
        "create_report",
        "update_report",
        "export_nodes_snapshot",
        "run_node",
        "verify_code_state",
        "get_project_state",
        "list_nodes",
        "get_node",
        "get_scenes",
        "get_shots",
        "get_run_logs",
        "get_spend_report",
    }
)

_STRING = "string"
_INT = "integer"
_ARRAY = "array"
_OBJECT = "object"


def check(project_id: int, context: dict[str, Any]) -> Any:
    del project_id
    plan = context.get("plan")
    if plan is None:
        return make_result(
            name=_NAME,
            passed=True,
            blocking=False,
            details={"checked": False, "reason": "no_plan"},
        )
    if not isinstance(plan, list):
        return _fail([_err(-1, "", "plan_not_list")])
    steps = [_step(item) for item in plan]
    whitelist, registry = _whitelist(context)
    errors: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        action = step["action"]
        params = step["params"]
        if not action:
            errors.append(_err(index, action, "missing_action"))
            continue
        if is_deletion(action, params) and not _soft_delete_granted(action, context, registry):
            errors.append(_err(index, action, "deletion_forbidden"))
            continue
        if action not in whitelist:
            errors.append(_err(index, action, "not_in_whitelist"))
            continue
        errors.extend(_schema_errors(index, action, params, context, registry))
    errors.extend(_verify_errors(steps))
    if errors:
        return _fail(errors)
    return make_result(
        name=_NAME,
        passed=True,
        blocking=False,
        details={"reason": "ok", "steps": len(steps)},
    )


def is_deletion(action: str, params: Any) -> bool:
    if _action_deletes(action):
        return True
    return _params_delete(params)


def _fail(errors: list[dict[str, Any]]) -> Any:
    return make_result(
        name=_NAME,
        passed=False,
        blocking=True,
        details={"reason": "plan_rejected", "errors": errors},
    )


def _err(index: int, action: str, reason: str, **extra: Any) -> dict[str, Any]:
    row = {"index": index, "action": action, "reason": reason}
    row.update(extra)
    return row


def _step(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        action = item.get("action") or item.get("name") or ""
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        return {"action": str(action), "params": dict(params)}
    action = getattr(item, "action", "") or ""
    params = getattr(item, "params", None)
    if not isinstance(params, dict):
        params = {}
    return {"action": str(action), "params": dict(params)}


def _whitelist(context: dict[str, Any]) -> tuple[set[str], Any]:
    """Реестр задаёт потолок имён. Context пересекается с ним и не расширяет."""

    names, registry = _import_registry()
    if not names:
        names, registry = set(FALLBACK_WHITELIST), None
    allowed = set(names)
    context_registry = context.get("registry")
    if context_registry is not None:
        narrowed = _registry_names(context_registry)
        if narrowed:
            allowed &= narrowed
    if "whitelist" in context:
        raw = context.get("whitelist") or []
        allowed &= {str(item) for item in raw}
    return allowed, registry


def _import_registry() -> tuple[set[str], Any]:
    for module_name, attr in (
        ("app.orchestrator.actions", "registry"),
        ("app.services.action_layer.spec", "registry"),
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:  # noqa: BLE001 — реестра ещё нет
            continue
        ensure = getattr(module, "ensure_builtin_actions", None)
        if callable(ensure):
            try:
                ensure()
            except Exception:  # noqa: BLE001 — пустой реестр уйдёт в fallback
                pass
        registry = getattr(module, attr, None)
        if callable(registry):
            try:
                registry = registry()
            except Exception:  # noqa: BLE001
                continue
        names = _registry_names(registry)
        if names:
            return names, registry
    return set(), None


def _registry_names(registry: Any) -> set[str]:
    if registry is None:
        return set()
    names_fn = getattr(registry, "names", None)
    if callable(names_fn):
        try:
            return {str(item) for item in names_fn()}
        except Exception:  # noqa: BLE001
            return set()
    actions = getattr(registry, "_actions", None)
    if isinstance(actions, dict):
        return {str(key) for key in actions}
    if isinstance(registry, dict):
        return {str(key) for key in registry}
    return set()


_SOFT_FRAME_DELETES = frozenset({"montage.delete_frame", "montage.delete_child_frame"})


def _soft_delete_granted(action: str, context: dict[str, Any], registry: Any) -> bool:
    """Мягкое удаление кадра монтажа: пометка и одобрение ядра, иначе блок."""

    token = action.strip().lower().replace("-", "_")
    if action != token or token not in _SOFT_FRAME_DELETES:
        return False
    if not _metadata_soft_delete(action, registry):
        return False
    return _core_approved_high_destructive(context, action)


def _metadata_soft_delete(action: str, registry: Any) -> bool:
    """``metadata_of`` только у серверного реестра модуля.

    ``context["registry"]`` сюда не входит: его кладёт вызывающий, не сервер.
    Нет метода или поля ``soft_delete`` — false.
    """

    fn = getattr(registry, "metadata_of", None)
    if not callable(fn):
        return False
    try:
        meta = fn(action)
    except Exception:  # noqa: BLE001 — нет карточки, как отсутствие поля
        return False
    getter = getattr(meta, "get", None)
    if not callable(getter):
        return False
    return getter("soft_delete") is True


def _core_approved_high_destructive(context: dict[str, Any], action: str) -> bool:
    """Ядро выдало одобрение именно этому действию.

    ``context["approval"]`` — форма WS3: ``change_id``, ``action``, ``risk``,
    ``destructive``, ``granted``, ``cc_change_id``. Обязательны ``risk == "high"``
    без strip и lower, ``destructive is True``, ``granted is True`` и
    ``approval["action"]``, строго равный сырому имени шага.
    """

    approval = context.get("approval")
    if isinstance(approval, dict):
        risk = approval.get("risk")
        destructive = approval.get("destructive")
        granted = approval.get("granted")
        approved_action = approval.get("action")
    elif approval is not None:
        risk = getattr(approval, "risk", None)
        destructive = getattr(approval, "destructive", None)
        granted = getattr(approval, "granted", None)
        approved_action = getattr(approval, "action", None)
    else:
        return False
    return (
        risk == "high"
        and destructive is True
        and granted is True
        and approved_action == action
    )


def _action_deletes(action: str) -> bool:
    token = action.strip().lower().replace("-", "_")
    if token in _DENY_EXACT or "delete_child_frame" in token:
        return True
    return _DELETE.search(token) is not None


def _params_delete(params: Any) -> bool:
    if not isinstance(params, dict):
        return False
    for key in params:
        if _action_deletes(str(key)):
            return True
    op = str(params.get("op") or params.get("operation") or "").lower()
    target = " ".join(
        str(params.get(key) or "")
        for key in ("target", "entity", "kind", "what")
    ).lower()
    if op in {"delete", "remove", "drop"} and any(
        word in target for word in ("scene", "shot", "frame", "сцен", "кадр")
    ):
        return True
    ops = params.get("ops")
    if isinstance(ops, list):
        return any(_params_delete(item) for item in ops)
    return False


def _verify_errors(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    seen_verify = False
    seen_code = False
    seen_node = False
    for index, step in enumerate(steps):
        action = step["action"]
        if action == "verify_code_state":
            seen_verify = True
        elif action == "read_pipeline_code":
            seen_code = True
        elif action == "get_node":
            seen_node = True
        elif action == "run_node":
            if not (seen_verify or (seen_code and seen_node)):
                errors.append(_err(index, action, "missing_verify_code_state"))
            seen_verify = False
            seen_code = False
            seen_node = False
    return errors


def _schema_errors(
    index: int,
    action: str,
    params: dict[str, Any],
    context: dict[str, Any],
    registry: Any,
) -> list[dict[str, Any]]:
    from_registry = _registry_validate(registry, action, params)
    if from_registry is not None:
        return [
            _err(index, action, "schema", field="", message=message) for message in from_registry
        ]
    schemas = context.get("schemas") if isinstance(context.get("schemas"), dict) else None
    spec = None if schemas is None else schemas.get(action)
    if spec is None:
        spec = _SCHEMAS.get(action)
    if spec is None:
        return []
    errors: list[dict[str, Any]] = []
    fields = spec.get("fields") or {}
    for field, rule in fields.items():
        required = bool(rule.get("required"))
        if field not in params or params.get(field) is None:
            if required:
                errors.append(_err(index, action, "missing_param", field=field))
            continue
        if not _type_ok(params[field], rule):
            errors.append(_err(index, action, "bad_type", field=field, expected=rule.get("type")))
    mode = params.get("patch_mode")
    if action == "edit_node_prompt" and mode in _SECTION_MODES and not str(params.get("section_anchor") or "").strip():
        errors.append(_err(index, action, "missing_param", field="section_anchor"))
    return errors


def _registry_validate(registry: Any, action: str, params: dict[str, Any]) -> list[str] | None:
    fn = getattr(registry, "validate_params", None)
    if not callable(fn):
        return None
    try:
        found = fn(action, params)
    except KeyError:
        return None
    except Exception as exc:  # noqa: BLE001
        return [str(exc)]
    if found is None:
        return []
    return [str(item) for item in found]


def _type_ok(value: Any, rule: dict[str, Any]) -> bool:
    kind = rule.get("type")
    if kind == _STRING:
        if not isinstance(value, str):
            return False
        pattern = rule.get("pattern")
        if pattern and re.fullmatch(pattern, value) is None:
            return False
        enum = rule.get("enum")
        return not (enum and value not in enum)
    if kind == _INT:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == _ARRAY:
        return isinstance(value, list)
    if kind == _OBJECT:
        return isinstance(value, dict)
    return True


def _field(type_name: str, *, required: bool = False, pattern: str | None = None, enum: set[str] | None = None) -> dict[str, Any]:
    rule: dict[str, Any] = {"type": type_name, "required": required}
    if pattern:
        rule["pattern"] = pattern
    if enum:
        rule["enum"] = enum
    return rule


_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_pipeline_code": {"fields": {"path": _field(_STRING, required=True), "max_bytes": _field(_INT)}},
    "rag_search": {
        "fields": {
            "query": _field(_STRING, required=True),
            "corpora": _field(_ARRAY),
            "top_k": _field(_INT),
        }
    },
    "run_validators": {
        "fields": {
            "project_id": _field(_INT, required=True),
            "validators": _field(_ARRAY),
            "context": _field(_OBJECT),
        }
    },
    "stop_node": {
        "fields": {
            "project_id": _field(_INT, required=True),
            "node_key": _field(_STRING, required=True),
            "reason": _field(_STRING, required=True),
        }
    },
    "resume_node": {
        "fields": {
            "project_id": _field(_INT, required=True),
            "node_key": _field(_STRING, required=True),
        }
    },
    "create_node": {
        "fields": {
            "node_key": _field(_STRING, required=True, pattern=_NODE_KEY.pattern),
            "group": _field(_STRING, required=True, pattern=_GROUP_KEY.pattern),
            "title": _field(_STRING, required=True),
            "prompt_template": _field(_STRING, required=True),
            "params": _field(_OBJECT),
            "position": _field(_OBJECT),
        }
    },
    "edit_node_prompt": {
        "fields": {
            "node_key": _field(_STRING, required=True),
            "patch_mode": _field(_STRING, required=True, enum=_PATCH_MODES),
            "content": _field(_STRING, required=True),
            "reason": _field(_STRING, required=True),
            "section_anchor": _field(_STRING),
        }
    },
    "edit_node_params": {
        "fields": {
            "node_key": _field(_STRING, required=True),
            "params_patch": _field(_OBJECT, required=True),
            "reason": _field(_STRING, required=True),
        }
    },
    "create_report": {
        "fields": {
            "title": _field(_STRING, required=True),
            "project_id": _field(_INT, required=True),
            "sections": _field(_ARRAY, required=True),
        }
    },
    "update_report": {
        "fields": {
            "report_id": _field(_INT, required=True),
            "sections_patch": _field(_ARRAY, required=True),
        }
    },
    "export_nodes_snapshot": {"fields": {"node_keys": _field(_ARRAY)}},
    "run_node": {
        "fields": {
            "project_id": _field(_INT, required=True),
            "node_key": _field(_STRING, required=True),
        }
    },
    "verify_code_state": {
        "fields": {
            "path": _field(_STRING, required=True),
            "node_key": _field(_STRING, required=True),
        }
    },
    "get_node": {"fields": {"node_key": _field(_STRING, required=True)}},
    "get_scenes": {"fields": {"project_id": _field(_INT)}},
    "get_shots": {"fields": {"project_id": _field(_INT)}},
    "get_project_state": {"fields": {"project_id": _field(_INT)}},
    "get_run_logs": {"fields": {"project_id": _field(_INT)}},
    "get_spend_report": {"fields": {"project_id": _field(_INT)}},
    "list_nodes": {"fields": {}},
}
