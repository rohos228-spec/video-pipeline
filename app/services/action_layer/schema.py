"""Минимальный валидатор JSON-Schema для параметров действий.

Зависимости ``jsonschema`` в проекте нет, поэтому поддерживается строгое
подмножество. Неизвестное ключевое слово в схеме = ошибка регистрации
(fail-closed), а не молчаливый пропуск проверки.
"""
from __future__ import annotations

import re
from typing import Any

_ANNOTATIONS = {"title", "description", "default", "examples", "$comment"}
_KEYWORDS = _ANNOTATIONS | {
    "type", "properties", "required", "additionalProperties", "enum", "const",
    "minimum", "maximum", "minLength", "maxLength", "pattern", "items",
    "minItems", "maxItems",
}
_TYPES = {"string", "integer", "number", "boolean", "object", "array", "null"}


def check_schema(schema: Any, path: str = "$") -> list[str]:
    """Проверка самой схемы: только известные ключевые слова и типы."""
    if not isinstance(schema, dict):
        return [f"{path}: schema must be an object"]
    errors: list[str] = []
    for key in schema:
        if key not in _KEYWORDS:
            errors.append(f"{path}: unsupported keyword {key!r}")
    types = schema.get("type")
    if types is not None:
        for t in (types if isinstance(types, list) else [types]):
            if t not in _TYPES:
                errors.append(f"{path}: unknown type {t!r}")
    for name, sub in (schema.get("properties") or {}).items():
        errors.extend(check_schema(sub, f"{path}.{name}"))
    if "items" in schema:
        errors.extend(check_schema(schema["items"], f"{path}[]"))
    extra = schema.get("additionalProperties")
    if isinstance(extra, dict):
        errors.extend(check_schema(extra, f"{path}.*"))
    if "pattern" in schema:
        try:
            re.compile(schema["pattern"])
        except re.error:
            errors.append(f"{path}: invalid pattern")
    req = schema.get("required")
    if req is not None and not (isinstance(req, list) and all(isinstance(r, str) for r in req)):
        errors.append(f"{path}: required must be a list of strings")
    return errors


def _is_type(value: Any, name: str) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "object":
        return isinstance(value, dict)
    if name == "array":
        return isinstance(value, list)
    return value is None


def validate(schema: dict, value: Any, path: str = "$") -> list[str]:
    """Список ошибок (пустой = валидно). Ничего не бросает."""
    errors: list[str] = []
    types = schema.get("type")
    if types is not None:
        names = types if isinstance(types, list) else [types]
        if not any(_is_type(value, n) for n in names):
            return [f"{path}: expected {'/'.join(names)}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: must be one of {schema['enum']!r}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above {schema['maximum']}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than {schema['maxItems']} items")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                errors.extend(validate(schema["items"], item, f"{path}[{i}]"))
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{path}.{name}: required")
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                errors.extend(validate(props[key], item, f"{path}.{key}"))
            elif extra is False:
                errors.append(f"{path}.{key}: unknown field")
            elif isinstance(extra, dict):
                errors.extend(validate(extra, item, f"{path}.{key}"))
    return errors
