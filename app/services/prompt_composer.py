"""Сборка промтов из шаблонов steps/ + блоков blocks/ + переменных vars.

Плейсхолдеры:
  {{BLOCK:world}}           → blocks/world/<name>.md
  {{VAR:VIDEO_DURATION_SEC}} → подстановка из dict vars

Стили (пресеты): prompts/styles/*.json

Формат значения блока в `prompt_overrides.blocks[category]` (обратная
совместимость сохранена — старые проекты с чистыми строками работают
без изменений):

  "world": "cats_anthropomorphic"                       # как раньше: имя файла
  "world": {"name": "cats_anthropomorphic", "weight": 0.6}  # вес 0..1
  "world": {"text": "свой текст с {{VAR:X}}", "weight": 1}  # свой текст вместо файла

Вес — не числовой параметр для модели (LLM не умеют веса как diffusion),
а текстовая метка приоритета, которую добавляет `_weight_prefix()` перед
контентом блока, если вес отличается от 1.0 (см. WEIGHT_LABELS).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.local_library import current_prompts_root, use_local_library_prompts

PROMPTS_ROOT = Path(__file__).resolve().parent.parent.parent / "prompts"
BLOCKS_ROOT = PROMPTS_ROOT / "blocks"
STEPS_ROOT = PROMPTS_ROOT / "steps"
STYLES_ROOT = PROMPTS_ROOT / "styles"

BLOCK_RE = re.compile(r"\{\{BLOCK:([a-z0-9_]+)\}\}")
VAR_RE = re.compile(r"\{\{VAR:([A-Z0-9_]+)\}\}")
STEP_BLOCK_HEADER_RE = re.compile(r"^## (\d+)\.\s+(.+?)\s*$", re.MULTILINE)
STEP_H1_RE = re.compile(r"^(#\s+[^\n]+)\n")

# Значение блока в prompt_overrides.blocks[cat]: либо просто имя файла
# (legacy), либо объект {name?, text?, weight?}.
BlockValue = str | dict[str, Any]

# node type (workflow) → папка steps/
NODE_TYPE_TO_STEP: dict[str, str] = {
    "plan": "01_plan",
    "script": "02_script",
    "split": "03_razbivka",
    "hero": "04_hero",
    "items": "04b_items",
    "enrich_1": "05a_enrich_1",
    "enrich_2": "05b_enrich_2",
    "enrich_3": "05c_enrich_3",
    "enrich_4": "05d_enrich_4",
    "enrich_5": "05e_enrich_5",
    "image_prompts": "06_image_prompts",
    "animation_prompts": "07_animation",
}

# step_code из prompt_library (menu) → steps/
STEP_CODE_TO_COMPOSE: dict[str, str] = {
    "plan": "01_plan",
    "script": "02_script",
    "split": "03_razbivka",
    "hero": "04_hero",
    "items": "04b_items",
    "enrich_1": "05a_enrich_1",
    "enrich_2": "05b_enrich_2",
    "enrich_3": "05c_enrich_3",
    "enrich_4": "05d_enrich_4",
    "enrich_5": "05e_enrich_5",
    "img_pr": "06_image_prompts",
    "anim_pr": "07_animation",
}

DEFAULT_BLOCKS: dict[str, str] = {
    "world": "cats_anthropomorphic",
    "visual_style": "micro_pixelart",
    "lighting": "cinematic_chiaroscuro",
    "negative": "no_humans_no_text",
    "voice_tone": "documentary_calm",
    "composition": "vertical_9_16_character",
    "background_density": "isolated_no_background",
    "camera_framing": "medium_full_mix",
    "camera_motion": "slow_push_in",
    "forbidden_phrases": "ai_cliches_ru",
    "narrative_structure": "shorts_hook_insight",
    "character_anatomy": "anthro_cat_sheet",
    "script_role": "voiceover_author",
    "source_policy": "xlsx_general_plan_only",
    "script_mode_selector": "universal_modes",
    "script_domain_skills": "biography_history_science_process_object",
    "script_narrative_structure": "short_voiceover_arc",
    "script_continuity_rules": "smooth_voiceover_flow",
    "script_voice_tone": "human_documentary_voice",
    "script_anti_gpt_patterns": "zinser_filter",
    "script_output_contract": "voiceover_txt_60s",
    "script_self_check": "voiceover_quality_gate",
    "script_segmentation_rules": "long_cells_110_140",
    "script_source_full": "scenario_agent_full",
    "img_input_rules": "one_cell_one_prompt",
    "img_scene_interpretation": "realism_and_abstract_five_ways",
    "img_hero_policy": "hero_reference_strict",
    "img_diversity_rules": "scene_variety",
    "img_context_logic": "source_only_no_invention",
    "img_composition_discipline": "trash_polka_foreground_v25",
    "img_prop_text_rules": "blank_papers_default",
    "img_output_contract": "xlsx_dash_separated",
    "img_self_check": "pre_output_gate",
    "img_source_full": "default_full",
    "plan_role": "shorts_planner",
    "plan_structure": "viral_60s_timeline",
    "plan_voice_tone": "human_clear_pitch",
    "plan_output_contract": "xlsx_plan_timing",
    "plan_self_check": "plan_quality_gate",
    "split_role": "voiceover_segmenter",
    "split_rules": "microthought_cells",
    "split_output_contract": "xlsx_row49",
    "split_self_check": "no_broken_words_gate",
    "enrich_role": "xlsx_editor",
    "enrich_edit_rules": "sheet_safe_edits",
    "enrich_source_policy": "xlsx_task_only",
    "enrich_output_contract": "return_full_xlsx",
    "enrich_self_check": "no_structure_damage_gate",
    "anim_motion_layers": "three_plane_motion",
    "anim_output_contract": "veo_single_prompt",
    "anim_negative": "no_style_shift",
    "plan_source_full": "default_full",
    "split_source_full": "default_full",
    "hero_source_full": "default_full",
    "hero_style_source_full": "default_full",
    "items_source_full": "default_full",
    "enrich_source_full": "default_full",
    "anim_source_full": "default_full",
}

DEFAULT_VARS: dict[str, str | int] = {
    "VIDEO_DURATION_SEC": 60,
    "VOICEOVER_MIN_CHARS": 800,
    "VOICEOVER_MAX_CHARS": 900,
    "BLOCK_LEN_MIN_CHARS": 45,
    "BLOCK_LEN_MAX_CHARS": 100,
    "ASPECT_RATIO_VIDEO": "9:16",
    "ASPECT_RATIO_HERO": "16:9",
    "PROMPT_LEN_MIN": 500,
    "PROMPT_LEN_MAX": 4800,
    "VIDEO_DURATION_MAX_SEC": 8,
    "FRAME_DURATION_MIN_SEC": 2,
    "FRAME_DURATION_MAX_SEC": 4,
    "ITEM_STYLE_NOTE": "",
    "ENRICH_1_TASK": "",
    "ENRICH_2_TASK": "",
    "ENRICH_3_TASK": "",
    "ENRICH_4_TASK": "",
    "ENRICH_5_TASK": "",
    "ENRICH_1_SHEET": "план",
    "ENRICH_2_SHEET": "план",
    "ENRICH_3_SHEET": "план",
    "ENRICH_4_SHEET": "план",
    "ENRICH_5_SHEET": "план",
}

# Метки приоритета для веса блока (0..1). weight >= 1.0 → без метки.
WEIGHT_LABELS: tuple[tuple[float, str], ...] = (
    (0.7, "[ВАЖНО, соблюдай строго] "),
    (0.4, "[учитывай наравне с другими блоками] "),
    (0.0, "[фоновый акцент, второстепенно] "),
)

LEGACY_BLOCK_ALIASES: dict[str, str] = {
    # Old projects/tests used `voice_tone`; the script constructor now has a
    # script-specific category, but the legacy override must still affect it.
    "script_voice_tone": "voice_tone",
    "plan_structure": "narrative_structure",
    "plan_voice_tone": "voice_tone",
}


def _prompt_roots() -> list[Path]:
    """Local library first, repo prompts as fallback."""
    local = current_prompts_root()
    roots: list[Path] = []
    for configured in (STEPS_ROOT.parent, BLOCKS_ROOT.parent, STYLES_ROOT.parent):
        if configured != PROMPTS_ROOT and configured not in roots:
            roots.append(configured)
    if use_local_library_prompts() and local.is_dir():
        roots.append(local)
    if PROMPTS_ROOT not in roots:
        roots.append(PROMPTS_ROOT)
    return roots


def _first_existing(*parts: str) -> Path | None:
    for root in _prompt_roots():
        path = root.joinpath(*parts)
        if path.exists():
            return path
    return None


def _block_entry_for_category(
    category: str, blocks: dict[str, BlockValue]
) -> BlockValue | None:
    entry = blocks.get(category)
    legacy_category = LEGACY_BLOCK_ALIASES.get(category)
    if legacy_category:
        legacy_entry = blocks.get(legacy_category)
        if legacy_entry is not None and legacy_entry != DEFAULT_BLOCKS.get(legacy_category):
            return legacy_entry
    return entry


def list_block_categories() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for root in reversed(_prompt_roots()):
        blocks_root = root / "blocks"
        if not blocks_root.is_dir():
            continue
        for cat_dir in sorted(blocks_root.iterdir()):
            if not cat_dir.is_dir():
                continue
            names = set(out.get(cat_dir.name, []))
            names.update(p.stem for p in cat_dir.glob("*.md") if p.is_file())
            out[cat_dir.name] = sorted(names)
    return out


def _block_label(body: str, block_id: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip() or block_id
        if s:
            return s[:80]
    return block_id.replace("_", " ")


def list_block_catalog() -> list[dict[str, Any]]:
    """All block files with body — for Prompt Builder UI."""
    items: list[dict[str, Any]] = []
    for category, names in list_block_categories().items():
        for name in names:
            path = _first_existing("blocks", category, f"{name}.md")
            if not path.is_file():
                continue
            body = path.read_text(encoding="utf-8")
            items.append(
                {
                    "category": category,
                    "id": name,
                    "label": _block_label(body, name),
                    "preview": body.strip()[:200],
                    "body": body,
                }
            )
    return items


def list_step_templates() -> list[str]:
    names: set[str] = set()
    for root in _prompt_roots():
        steps_root = root / "steps"
        if not steps_root.is_dir():
            continue
        names.update(
            d.name for d in steps_root.iterdir() if d.is_dir() and (d / "template.md").is_file()
        )
    return sorted(names)


def parse_step_template_blocks(step_id: str) -> list[dict[str, Any]]:
    """Разбирает `steps/<id>/template.md` на список смысловых блоков
    `## N. ЗАГОЛОВОК` для визуального блочного редактора в Studio UI.

    Возвращает `[{"number": 1, "title": "ТЕХНИЧЕСКАЯ ЧАСТЬ", "body": "..."}, ...]`
    в порядке следования в файле."""
    text = _read_template(step_id)
    matches = list(STEP_BLOCK_HEADER_RE.finditer(text))
    blocks: list[dict[str, Any]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        blocks.append(
            {
                "number": int(m.group(1)),
                "title": m.group(2).strip(),
                "body": text[start:end].strip("\n"),
            }
        )
    return blocks


def write_step_template_blocks(step_id: str, blocks: list[dict[str, Any]]) -> str:
    """Пересобирает `steps/<id>/template.md` из списка блоков (обратная
    операция к `parse_step_template_blocks`). Сохраняет заголовок `# ...`
    первой строки файла (если он был), если явно не задан в блоках.

    `step_id` должен быть уже существующим шаблоном (проверяется на
    роутере через `list_step_templates()`, чтобы исключить path traversal)."""
    if STEPS_ROOT.parent != PROMPTS_ROOT or not use_local_library_prompts():
        path = STEPS_ROOT / step_id / "template.md"
    else:
        path = current_prompts_root() / "steps" / step_id / "template.md"
    h1 = ""
    existing = _first_existing("steps", step_id, "template.md")
    if existing is not None and existing.is_file():
        m0 = STEP_H1_RE.match(existing.read_text(encoding="utf-8"))
        if m0:
            h1 = m0.group(1) + "\n\n"
    parts = [h1]
    for b in sorted(blocks, key=lambda b: int(b["number"])):
        title = str(b["title"]).strip()
        body = str(b["body"]).strip()
        parts.append(f"## {int(b['number'])}. {title}\n\n{body}\n\n")
    new_text = "".join(parts).rstrip("\n") + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new_text, encoding="utf-8")
    return new_text


def step_block_categories(step_id: str) -> list[str]:
    """Категории `{{BLOCK:cat}}`, реально используемые шаблоном шага.

    Нужно, чтобы UI никогда не показывал юзеру категорию, которая для этого
    шага не подставляется никуда (например «camera_motion» для «плана») —
    меньше шансов сохранить override, который ни на что не влияет."""
    try:
        text = _read_template(step_id)
    except FileNotFoundError:
        return []
    return sorted(set(BLOCK_RE.findall(text)))


def list_style_presets() -> list[dict[str, Any]]:
    presets: list[dict[str, Any]] = []
    seen: set[str] = set()
    paths: list[Path] = []
    for root in _prompt_roots():
        styles_root = root / "styles"
        if not styles_root.is_dir():
            continue
        for p in sorted(styles_root.glob("*.json")):
            if p.stem in seen:
                continue
            seen.add(p.stem)
            paths.append(p)
    for p in paths:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data["id"] = p.stem
            presets.append(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("prompt_composer: bad style {}: {}", p, e)
    return presets


def load_style_preset(preset_id: str) -> dict[str, Any]:
    path = _first_existing("styles", f"{preset_id}.json")
    if path is None or not path.is_file():
        raise FileNotFoundError(f"style preset not found: {preset_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id"] = preset_id
    return data


def _read_block(category: str, name: str) -> str:
    path = _first_existing("blocks", category, f"{name}.md")
    if path is None or not path.is_file():
        raise FileNotFoundError(f"block not found: {category}/{name}")
    return path.read_text(encoding="utf-8").strip()


def _read_template(step_id: str) -> str:
    path = _first_existing("steps", step_id, "template.md")
    if path is None or not path.is_file():
        raise FileNotFoundError(f"step template not found: {step_id}")
    return path.read_text(encoding="utf-8")


def read_step_template(step_id: str) -> str:
    return _read_template(step_id)


def compose_step_sections(
    step_id: str,
    blocks: dict[str, BlockValue],
) -> list[dict[str, str]]:
    """Resolved block sections for storage/inspection of the final prompt."""
    sections: list[dict[str, str]] = []
    for category in step_block_categories(step_id):
        content, weight = resolve_block_value(category, _block_entry_for_category(category, blocks))
        sections.append(
            {
                "kind": category,
                "label": category.replace("_", " "),
                "body": f"{_weight_prefix(weight)}{content}" if weight < 0.999 else content,
            }
        )
    return sections


def clamp_weight(value: Any, default: float = 1.0) -> float:
    """Приводит произвольное значение к весу 0..1. Никогда не бросает исключение —
    любое некорректное значение (строка, None, NaN) тихо превращается в дефолт,
    чтобы битый override не мог сломать сборку промта."""
    try:
        w = float(value)
    except (TypeError, ValueError):
        return default
    if w != w:  # NaN
        return default
    return max(0.0, min(1.0, w))


def _weight_prefix(weight: float) -> str:
    if weight >= 0.999:
        return ""
    for threshold, label in WEIGHT_LABELS:
        if weight >= threshold:
            return label
    return ""


def resolve_block_value(category: str, entry: BlockValue | None) -> tuple[str, float]:
    """Возвращает (текст блока без weight-префикса, вес 0..1).

    Правила (в порядке приоритета), рассчитано так, чтобы никогда не бросить
    исключение — при любой проблеме возвращается диагностический html-комментарий
    вместо текста, а не падение сборки:
      1. entry is None            → берём DEFAULT_BLOCKS[category] (имя файла)
      2. entry — строка           → имя файла в blocks/<category>/<entry>.md, weight=1.0
      3. entry — dict с "text"    → свой текст, weight из dict (default 1.0)
      4. entry — dict с "name"    → имя файла из dict, weight из dict
      5. иначе                    → диагностика "invalid block config"
    """
    if entry is None:
        entry = DEFAULT_BLOCKS.get(category)
        if entry is None:
            return f"<!-- missing block: {category} -->", 1.0

    if isinstance(entry, str):
        try:
            return _read_block(category, entry), 1.0
        except FileNotFoundError:
            return f"<!-- block file missing: {category}/{entry} -->", 1.0

    if isinstance(entry, dict):
        weight = clamp_weight(entry.get("weight"), default=1.0)
        custom_text = entry.get("text")
        if isinstance(custom_text, str) and custom_text.strip():
            return custom_text.strip(), weight
        name = entry.get("name") or DEFAULT_BLOCKS.get(category)
        if not name:
            return f"<!-- missing block: {category} -->", weight
        try:
            return _read_block(category, str(name)), weight
        except FileNotFoundError:
            return f"<!-- block file missing: {category}/{name} -->", weight

    return f"<!-- invalid block config: {category} -->", 1.0


def merge_project_prompt_config(
    overrides: dict[str, Any] | None,
    *,
    hero_description: str | None = None,
    topic: str | None = None,
) -> tuple[dict[str, BlockValue], dict[str, str | int]]:
    """blocks, vars из Project.prompt_overrides + дефолты + style preset."""
    po = dict(overrides or {})
    blocks: dict[str, BlockValue] = dict(DEFAULT_BLOCKS)
    vars_: dict[str, str | int] = dict(DEFAULT_VARS)

    preset_id = po.get("style_profile") or po.get("style_preset")
    if preset_id:
        try:
            preset = load_style_preset(str(preset_id))
            blocks.update(preset.get("blocks") or {})
            vars_.update(preset.get("vars") or {})
        except FileNotFoundError:
            logger.warning("style preset {} not found", preset_id)

    from app.services.prompt_step_presets import apply_prompt_presets_from_overrides

    apply_prompt_presets_from_overrides(po, blocks, vars_)

    if isinstance(po.get("blocks"), dict):
        for k, v in po["blocks"].items():
            # Значение может быть строкой (legacy) либо объектом {name/text/weight}.
            blocks[k] = v if isinstance(v, dict) else str(v)
    if isinstance(po.get("vars"), dict):
        vars_.update(po["vars"])

    if hero_description:
        vars_["HERO_DESCRIPTION"] = hero_description
    if topic:
        vars_["PROJECT_TOPIC"] = topic

    return blocks, vars_


def compose_step(
    step_id: str,
    blocks: dict[str, BlockValue],
    vars_: dict[str, Any],
) -> str:
    """Склеить финальный промт для шага."""
    text = _read_template(step_id)

    def repl_block(m: re.Match[str]) -> str:
        cat = m.group(1)
        content, weight = resolve_block_value(cat, _block_entry_for_category(cat, blocks))
        prefix = _weight_prefix(weight)
        return f"{prefix}{content}" if prefix else content

    def repl_var(m: re.Match[str]) -> str:
        key = m.group(1)
        if key in vars_:
            return str(vars_[key])
        return f"<!-- missing var: {key} -->"

    text = BLOCK_RE.sub(repl_block, text)
    text = VAR_RE.sub(repl_var, text)
    return text.strip()


def compose_for_node_type(
    node_type: str,
    overrides: dict[str, Any] | None,
    *,
    hero_description: str | None = None,
    topic: str | None = None,
) -> str:
    step_id = NODE_TYPE_TO_STEP.get(node_type)
    if not step_id:
        raise ValueError(f"no step template for node type: {node_type}")
    blocks, vars_ = merge_project_prompt_config(
        overrides, hero_description=hero_description, topic=topic
    )
    return compose_step(step_id, blocks, vars_)


def project_uses_blocks_v2(overrides: dict[str, Any] | None) -> bool:
    po = overrides or {}
    if po.get("use_blocks_v2") is True:
        return True
    return isinstance(po.get("blocks"), dict) and len(po["blocks"]) > 0
