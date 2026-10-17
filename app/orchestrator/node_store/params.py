"""Разбор params_json. Битый JSON — NodeStoreError, не JSONDecodeError."""

from __future__ import annotations

import json
from typing import Any

from app.orchestrator.node_store._db import NodeStoreError


def parse_params_json(params_json: str, *, node_key: str, version: int | None = None) -> dict[str, Any]:
    where = f"ноды {node_key}"
    if version is not None:
        where = f"{where} версии {version}"
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError as exc:
        raise NodeStoreError(f"params_json {where} не JSON") from exc
    if not isinstance(params, dict):
        raise NodeStoreError(f"params_json {where} должен быть объектом")
    return params
