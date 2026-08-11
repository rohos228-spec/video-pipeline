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
# Legacy: chars/world/style/action параллельно → camera.
WAVE1_AGENTS: tuple[str, ...] = ("characters", "world", "style", "action")
WAVE2_AGENTS: tuple[str, ...] = ("camera",)
# chrono_dyn: верхние 3 → action → camera (как на канвасе).
CHRONO_WAVE1_AGENTS: tuple[str, ...] = ("characters", "world", "style")
CHRONO_WAVE2_AGENTS: tuple[str, ...] = ("action",)
CHRONO_WAVE3_AGENTS: tuple[str, ...] = ("camera",)


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


def agent_waves(project: Any | None) -> tuple[tuple[str, ...], ...]:
    """Порядок волн GPT для категорийных агентов."""
    if uses_chrono_dyn(project):
        return (CHRONO_WAVE1_AGENTS, CHRONO_WAVE2_AGENTS, CHRONO_WAVE3_AGENTS)
    return (WAVE1_AGENTS, WAVE2_AGENTS)

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


def _chain_phases(scene: dict[str, Any]) -> list[Any]:
    raw = scene.get("цепь_действия") or scene.get("chain") or []
    return raw if isinstance(raw, list) else []


_BEAT_OK = frozenset({"setup", "develop", "turn", "payoff"})


_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PLACE_BUCKETS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hospital", re.compile(r"больниц|стационар|палат|психиатр|отделени", re.I)),
    ("police", re.compile(r"милиц|участков|следовател|кабинет милиц", re.I)),
    ("court", re.compile(r"суд\b|заседани|приговор", re.I)),
    ("home", re.compile(r"квартир|подъезд|кухн|лифт|дверь\s*357", re.I)),
    ("street", re.compile(r"пустыр|улиц|окраин|новокузнецк", re.I)),
)


def _phase_action_text(ph: dict[str, Any]) -> str:
    return " ".join(
        str(ph.get(k) or "") for k in ("action", "действие", "продолжает")
    )


def _place_buckets_in_text(text: str) -> set[str]:
    found: set[str] = set()
    for name, pat in _PLACE_BUCKETS:
        if pat.search(text or ""):
            found.add(name)
    return found


def _is_compound_action(action: str) -> bool:
    """Несколько жестов в одной фазе: «X, Y и Z» / два « и »."""
    t = re.sub(r"\s+", " ", (action or "").strip())
    if not t:
        return False
    if t.count(" и ") >= 2:
        return True
    # «встречаются у лифта, забирают письма и проходят»
    if "," in t and " и " in t:
        return True
    return False


def validate_chrono_dyn_action_scenes(scenes: list[Any]) -> None:
    """Брак: склейка действий, коллаж мест/лет, нет арки/связей, мало cNN."""
    if not scenes:
        return
    phase_counts: list[int] = []
    object_phases = 0
    cnn_phases = 0
    with_beats = 0
    with_transitions = 0
    missing_links = 0
    missing_hooks = 0
    missing_payoff = 0
    teleport_scenes = 0
    year_jump_scenes = 0
    compound_phases = 0
    dict_scenes = 0
    for i, sc in enumerate(scenes):
        if not isinstance(sc, dict):
            continue
        dict_scenes += 1
        chain = _chain_phases(sc)
        phase_counts.append(len(chain))
        if i > 0 and not str(sc.get("связь_с_прошлой") or "").strip():
            missing_links += 1
        if not str(sc.get("крючок_в_следующую") or "").strip():
            missing_hooks += 1
        beats: list[str] = []
        scene_years: set[str] = set()
        scene_places: set[str] = set()
        for ph in chain:
            if not isinstance(ph, dict):
                continue
            object_phases += 1
            sub = str(ph.get("subject") or "").strip().lower()
            if re.match(r"^c\d+$", sub):
                cnn_phases += 1
            beat = str(ph.get("beat") or "").strip().lower()
            if beat in _BEAT_OK:
                with_beats += 1
                beats.append(beat)
            if str(ph.get("переход_к_следующей") or "").strip():
                with_transitions += 1
            act = str(ph.get("action") or ph.get("действие") or "")
            if _is_compound_action(act):
                compound_phases += 1
            txt = _phase_action_text(ph)
            scene_years.update(_YEAR_RE.findall(txt))
            scene_places |= _place_buckets_in_text(txt)
        if object_phases and beats and "payoff" not in beats:
            missing_payoff += 1
        # ≥2 разных календарных года в одной цепи = телепорт по времени
        if len(scene_years) >= 2:
            year_jump_scenes += 1
        # ≥3 разных типа мест в одной сцене = коллаж локаций
        if len(scene_places) >= 3:
            teleport_scenes += 1
    if not phase_counts or object_phases == 0:
        # legacy строки в цепи — не chrono_dyn контракт
        return
    n = len(phase_counts)
    two_or_less = sum(1 for c in phase_counts if c <= 2)
    avg = sum(phase_counts) / n
    if two_or_less / n > 0.35 and n >= 5:
        raise SceneDesignAgentError(
            f"scene_design/action: брак плотности — {two_or_less}/{n} сцен с <=2 "
            f"фазами (avg={avg:.1f}). Нужно >=4 фазы на типичную сцену "
            f"(1 кадр = 1 действие)."
        )
    if avg < 4.0 and n >= 5:
        raise SceneDesignAgentError(
            f"scene_design/action: avg фаз {avg:.1f} < 4.0 — действия слиты; "
            f"разбей: одно действие на фазу (встретили / письма / мимо двери)."
        )
    # 0.25: после split-чанков модель часто даёт ~26–30% cNN; жёсткие 0.35
    # крутили soft-retry часами. Промпт V6 требует ≥50% — это цель, не брак.
    if object_phases >= 10 and cnn_phases / object_phases < 0.25:
        raise SceneDesignAgentError(
            f"scene_design/action: персонажи cNN только в {cnn_phases}/"
            f"{object_phases} фазах — задействуй героев текста, не crowd/prop."
        )
    # Арочные поля — обязательны, когда модель уже пишет объекты-фазы.
    if object_phases >= 8 and with_beats / object_phases < 0.7:
        raise SceneDesignAgentError(
            "scene_design/action: у фаз нет beat setup|develop|turn|payoff — "
            "сцена должна иметь начало/середину/конец."
        )
    if object_phases >= 8 and with_transitions < max(1, object_phases - dict_scenes):
        raise SceneDesignAgentError(
            "scene_design/action: нет переход_к_следующей между фазами — "
            "нужна монтажная последовательность, не набор кадров."
        )
    if dict_scenes >= 3 and missing_links / max(1, dict_scenes - 1) > 0.4:
        raise SceneDesignAgentError(
            "scene_design/action: сцены не связаны (нет связь_с_прошлой) — "
            "нужна сквозная последовательность сюжета."
        )
    if dict_scenes >= 3 and missing_hooks / dict_scenes > 0.4:
        raise SceneDesignAgentError(
            "scene_design/action: нет крючок_в_следующую — сцены не ведут сюжет дальше."
        )
    if missing_payoff > max(1, dict_scenes // 3):
        raise SceneDesignAgentError(
            f"scene_design/action: {missing_payoff} сцен без payoff — "
            f"у каждой сцены должен быть видимый итог бита."
        )
    if year_jump_scenes > 0:
        raise SceneDesignAgentError(
            f"scene_design/action: {year_jump_scenes} сцен прыгают по годам "
            f"внутри одной цепи — смена года = новая сцена, не фаза. "
            f"Нужна непрерывная цепочка в одном месте (вход→жест→стол→итог)."
        )
    if teleport_scenes > 0:
        raise SceneDesignAgentError(
            f"scene_design/action: {teleport_scenes} сцен — коллаж локаций "
            f"(больница/милиция/дом в одной цепи). Одна сцена = одно пространство "
            f"и непрерывный blocking, не набор кадров из разных мест."
        )
    if object_phases >= 8 and compound_phases / object_phases >= 0.2:
        raise SceneDesignAgentError(
            f"scene_design/action: {compound_phases}/{object_phases} фаз склеивают "
            f"несколько действий («…, … и …»). 1 кадр = 1 действие — разбей фазы."
        )


def parse_agent_slice(
    agent: str, text: str, *, validate: bool = True
) -> dict[str, Any]:
    """Распарсить и провалидировать JSON-срез категорийного агента.

    ``validate=False`` — для частичных чанков после 524 (полная проверка на merge).
    """
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
    if validate and agent == "action":
        validate_chrono_dyn_action_scenes(items)
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
