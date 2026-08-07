"""Реестр агентов scene_design: промпты, парсеры и валидаторы JSON-срезов."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app.project_root import find_project_root

PROMPTS_SUBDIR = Path("prompts") / "scene_design"

ASSEMBLER = "assemble"
CATEGORY_AGENTS: tuple[str, ...] = ("characters", "world", "style", "camera", "action")

# Обязательный ключ-список в JSON-срезе агента (непустой при успехе).
LIST_KEY: dict[str, str] = {
    "characters": "characters",
    "world": "locations",
    "style": "style_arc",
    "camera": "shot_plan",
    "action": "scenes",
    ASSEMBLER: "scenes",
}


class SceneDesignAgentError(RuntimeError):
    """Агент вернул невалидный/пустой JSON-срез."""


def prompts_dir() -> Path:
    return find_project_root() / PROMPTS_SUBDIR


def load_prompt(agent: str) -> str:
    path = prompts_dir() / f"{agent}.md"
    if not path.is_file():
        raise SceneDesignAgentError(f"scene_design: нет файла промпта {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SceneDesignAgentError(f"scene_design: пустой промпт {path}")
    return text


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json_object(
    text: str, *, marker_keys: tuple[str, ...]
) -> dict[str, Any] | None:
    """Достать JSON-объект из ответа модели (fence или голый JSON с маркером)."""
    if not text:
        return None
    candidates: list[str] = [m.group(1) for m in _JSON_FENCE_RE.finditer(text)]
    idxs = [i for i in (text.find(f'"{k}"') for k in marker_keys) if i != -1]
    idx = min(idxs) if idxs else -1
    if idx != -1:
        start = text.rfind("{", 0, idx)
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                ch = text[i]
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append(text[start : i + 1])
                        break
    for cand in candidates:
        try:
            data = json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_agent_slice(agent: str, text: str) -> dict[str, Any]:
    """Распарсить и провалидировать JSON-срез категорийного агента."""
    list_key = LIST_KEY[agent]
    data = extract_json_object(text, marker_keys=(list_key, "error"))
    if data is None:
        raise SceneDesignAgentError(
            f"scene_design/{agent}: в ответе нет JSON (len={len(text or '')})"
        )
    err = str(data.get("error") or "").strip()
    if err:
        raise SceneDesignAgentError(f"scene_design/{agent}: агент вернул error: {err}")
    items = data.get(list_key)
    if not isinstance(items, list) or not items:
        raise SceneDesignAgentError(
            f"scene_design/{agent}: пустой «{list_key}» — срез не принят"
        )
    return data


def parse_assembler_payload(text: str) -> dict[str, Any]:
    """Финальный JSON сборщика: {characters, scenes, ops, report}."""
    data = extract_json_object(text, marker_keys=("scenes", "ops", "characters", "error"))
    if data is None:
        raise SceneDesignAgentError(
            f"scene_design/{ASSEMBLER}: в ответе нет JSON (len={len(text or '')})"
        )
    err = str(data.get("error") or "").strip()
    if err:
        raise SceneDesignAgentError(f"scene_design/{ASSEMBLER}: error: {err}")
    for key in ("characters", "scenes", "ops"):
        if not isinstance(data.get(key), list):
            raise SceneDesignAgentError(
                f"scene_design/{ASSEMBLER}: ключ «{key}» — не список"
            )
    if not data["scenes"]:
        raise SceneDesignAgentError(f"scene_design/{ASSEMBLER}: пустой scenes[]")
    if not data["ops"]:
        raise SceneDesignAgentError(f"scene_design/{ASSEMBLER}: пустой ops[]")
    return data
