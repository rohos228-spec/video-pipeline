"""Канонический JSON для стабильных sha (chunk_id и ключ LLM-фикстуры)."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    """Стабильная строка: sort_keys, без пробелов, юникод как есть."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_payload(payload: Any) -> str:
    return sha256_text(canonical_json(payload))
