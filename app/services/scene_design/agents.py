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
# Волна 1 (параллельно) → волна 2 camera (нужен срез action).
WAVE1_AGENTS: tuple[str, ...] = ("characters", "world", "style", "action")
WAVE2_AGENTS: tuple[str, ...] = ("camera",)


def project_scene_design_variant(project: Any | None) -> str:
    """``meta.scene_design_variant`` или вывод из prompt_slot_variants (*_chrono_dyn)."""
    if project is None:
        return ""
    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return ""
    raw = str(meta.get("scene_design_variant") or "").strip()
    if raw:
        return raw
    slots = meta.get("prompt_slot_variants")
    if isinstance(slots, dict):
        for per in slots.values():
            if not isinstance(per, dict):
                continue
            for v in per.values():
                name = str(v or "")
                if name.endswith("_chrono_dyn") or "chrono_dyn" in name:
                    return "chrono_dyn"
    return ""


def uses_chrono_dyn(project: Any | None) -> bool:
    return project_scene_design_variant(project) == "chrono_dyn"

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


# Доп. файлы, доклеиваемые к базовому промпту агента (каталоги, словари).
PROMPT_INCLUDES: dict[str, tuple[str, ...]] = {
    "camera": ("camera_sets.md",),
}


def _excel_gpt_prompts_dir() -> Path:
    from app.services.prompt_library import step_dir

    return step_dir("excel_gpt")


def _node_prompt_variant(project: Any, agent: str) -> str | None:
    """Вариант промпта, назначенный на ноду агента на канвасе (SSoT —
    meta.prompt_slot_variants[node_key], как у всех нод «Работа с GPT»)."""
    from app.services.excel_gpt_node import sd_agent_marker

    meta = getattr(project, "meta", None)
    if not isinstance(meta, dict):
        return None
    cg = meta.get("canvas_graph")
    if not isinstance(cg, dict):
        return None
    node_key: str | None = None
    for n in cg.get("nodes") or []:
        if sd_agent_marker(n) == agent:
            node_key = str(n.get("id") or "") or None
            break
    if not node_key:
        return None
    slots = meta.get("prompt_slot_variants")
    if not isinstance(slots, dict):
        return None
    per_node = slots.get(node_key)
    if not isinstance(per_node, dict):
        return None
    for slot_id in ("main", "gpt", "prompt"):
        v = str(per_node.get(slot_id) or "").strip()
        if v:
            return v
    return None


def load_prompt(agent: str, project: Any | None = None) -> str:
    """Промпт агента: сначала вариант ноды «Работа с GPT» (05_excel_gpt),
    затем конвенция sd_<агент>.md там же, затем legacy scene_design/<агент>.md.
    """
    text = ""
    excel_dir = _excel_gpt_prompts_dir()
    if project is not None:
        variant = _node_prompt_variant(project, agent)
        if variant:
            from app.services.prompt_library import is_valid_prompt_name

            if is_valid_prompt_name(variant):
                p = excel_dir / f"{variant}.md"
                if p.is_file():
                    text = p.read_text(encoding="utf-8").strip()
    if not text:
        conv = excel_dir / f"sd_{agent}.md"
        if conv.is_file():
            text = conv.read_text(encoding="utf-8").strip()
    if not text:
        path = prompts_dir() / f"{agent}.md"
        if not path.is_file():
            raise SceneDesignAgentError(f"scene_design: нет файла промпта {path}")
        text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise SceneDesignAgentError(f"scene_design: пустой промпт агента {agent}")
    for extra_name in PROMPT_INCLUDES.get(agent, ()):
        extra_path = prompts_dir() / extra_name
        if not extra_path.is_file():
            raise SceneDesignAgentError(
                f"scene_design: нет файла включаемого промпта {extra_path}"
            )
        extra = extra_path.read_text(encoding="utf-8").strip()
        if extra:
            text = f"{text}\n\n---\n\n{extra}"
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
