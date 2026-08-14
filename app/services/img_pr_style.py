"""Стиль проекта для img_pr (resolve / markers).

Обёртка STYLE_HEAD/TAIL отключена: в `промт_картинки` уходит текст GPT
как есть. Хелперы `wrap_*` оставлены no-op для совместимости импортов.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

_STYLE_MARKER = "Archival Noir Watercolor Grunge Dossier Poster Illustration"

STYLE_HEAD = (
    "STYLE: Archival Noir Watercolor Grunge Dossier Poster Illustration. "
    "archival true-crime noir dossier poster + dark watercolor wash + "
    "grunge prison mystery illustration + distressed printmaking + "
    "noir comic graphic novel inking + high-contrast historical mixed media. "
    "Transparent watercolor washes, gray-blue wash layers, dirty cream pigment "
    "bleeding into paper, ink-and-water stains, raw brush smears, rough ink "
    "splashes, distressed/torn archive paper, worn newspaper texture, "
    "halftone dots, rough print imperfections, gritty comic inking, charcoal "
    "shadow masses. Palette: washed gray-blue, cold oceanic gray, off-white, "
    "dirty cream, charcoal, muted amber-gray, faded paper yellow, diluted ink "
    "black; rare distressed blood-red stress marks only. One unified poster "
    "frame, not collage panels. Improve only inside this style "
    "(stronger wash/silhouette/grain), never change medium."
)

STYLE_TAIL = (
    "Final style lock: unified cinematic frame, Archival Noir Watercolor "
    "Grunge Dossier Poster Illustration, dark comic graphic novel inking, "
    "distressed printmaking, realistic historical texture via watercolor "
    "absorption, high-contrast mixed media, transparent washes, "
    "paper-absorbed pigment, washed gray-blue and dirty cream palette, "
    "ink-and-water stains, rough contour lines, heavy charcoal shadow masses, "
    "halftone grain, damaged archive-paper surface, raw brush smears, rough "
    "ink splashes, torn newspaper fragments, minimal distressed red stress "
    "marks only, no red circles/arrows, no photorealism, no glossy 3D, "
    "no clean vector, no pastel, no neon, no multi-panel collage, "
    "no automatic detective board, no large foreground props, "
    "no copper basin/washstand, no twins/clones/duplicate faces of the same "
    "character id, character id when present, no copied voiceover. "
    "Negative: photorealism, glossy 3D render, clean minimalist, cute, "
    "pastel, bright cheerful colors, neon cyberpunk, flat digital color, "
    "smooth vector gradients, plastic digital paint, oil painting impasto, "
    "airbrushed fantasy, pure black-and-white without watercolor wash, "
    "multi-panel collage, separate collage panels, automatic detective board, "
    "evidence circles, red arrows, red outlines around clues, overlay text, "
    "captions, subtitles, watermark, floating text, copied voiceover, "
    "narration context, copper basin, brass basin, washstand, large "
    "foreground props, synonym duplicate scene, modern objects in historical "
    "scene, gore, twins, clones, duplicate identical faces, mirrored double "
    "of same character, style drift away from archival noir watercolor "
    "grunge dossier poster."
)

KNITTED_STYLE_HEAD = (
    "STYLE: knitted textile children's book illustration. "
    "handmade textile / cut-paper / felt, fibrous surfaces, "
    "soft stylized forms, warm autumnal palette "
    "(deep crimson, burnt orange, mustard, olive, forest green, warm peach). "
    "One unified frame. Improve only inside this style, never change medium."
)

KNITTED_STYLE_TAIL = (
    "Final style lock: children's book illustration, handmade textile texture, "
    "cut-paper and felt layering, fibrous surfaces, stylized simple faces, "
    "warm autumnal palette, poetic emotional warmth. "
    "Negative: photorealistic humans, 3D plastic, glossy skin, anime, "
    "realistic facial details, hard outlines, cold colors, scary mood, "
    "text, watermark, logo."
)

_PROMPT_FIELD_KEYS = (
    "промт_картинки",
    "image_prompt",
    "промпт_картинки",
)

PLASTILIN_MASTER_MARKERS = (
    "plastilin_prompt_minimalist_db",
    "plastilin_prompt_minimalist",
    "minimalist claymation plasticine",
    "минималистичная пластилиновая анимация",
)

_CLAY_STYLE_MARKERS = (
    "minimalist claymation plasticine",
    "claymation plasticine 2d-look",
    "минималистичная пластилиновая анимация",
    "handmade claymation",
)

_KNITTED_MARKERS = (
    "knitted",
    "textile",
    "вязан",
    "войлок",
)

_NOIR_MARKERS = (
    "noir",
    "archival",
    "trash polka",
    "trash_polka",
    "watercolor grunge",
)

_KNITTED_BODY_MARKERS = (
    "handmade textile",
    "knitted textile",
    "вязаный",
    "cut-paper and felt",
)

_STYLE_META_KEYS = (
    "img_pr_style_id",
    "img_style",
    "image_style",
    "visual_style",
    "style",
)

_NO_STYLE_WARNED = False


def is_plastilin_master(
    variant: str | None = None, master: str | None = None
) -> bool:
    blob = f"{variant or ''}\n{(master or '')[:1200]}".casefold()
    return any(m in blob for m in PLASTILIN_MASTER_MARKERS)


def looks_knitted(text: str | None) -> bool:
    blob = (text or "").casefold()
    return any(m in blob for m in _KNITTED_MARKERS)


def looks_noir(text: str | None) -> bool:
    blob = (text or "").casefold()
    return any(m in blob for m in _NOIR_MARKERS)


def already_has_style(text: str) -> bool:
    t = text or ""
    if _STYLE_MARKER in t and ("Final style lock" in t or "STYLE:" in t):
        return True
    low = t.casefold()
    if any(m in low for m in _CLAY_STYLE_MARKERS):
        return True
    return any(m in low for m in _KNITTED_BODY_MARKERS) and (
        "Final style lock" in t or "STYLE:" in t
    )


def _local_variant_name(project: Any | None, meta: dict | None) -> str:
    """Project-chosen img_pr variant names — not the global default prompt."""
    parts: list[str] = []
    if project is None:
        return ""
    overrides = getattr(project, "prompt_overrides", None) or {}
    if isinstance(overrides, dict):
        chosen = overrides.get("img_pr")
        if isinstance(chosen, str) and chosen.strip():
            parts.append(chosen.strip())
        nested = overrides.get("prompt_variants")
        if isinstance(nested, dict):
            chosen = nested.get("img_pr")
            if isinstance(chosen, str) and chosen.strip():
                parts.append(chosen.strip())
    if isinstance(meta, dict):
        slots = meta.get("prompt_slot_variants")
        if isinstance(slots, dict):
            for node_slots in slots.values():
                if not isinstance(node_slots, dict):
                    continue
                for val in node_slots.values():
                    if isinstance(val, str) and val.strip():
                        parts.append(val.strip())
    return "\n".join(parts)


def resolve_project_img_style(
    project: Any | None = None,
    *,
    variant: str | None = None,
    master: str | None = None,
) -> str | None:
    """Cheap style id: meta.img_style (etc.) or knitted/noir in variant/master."""
    meta = getattr(project, "meta", None) if project is not None else None
    if isinstance(meta, dict):
        for key in _STYLE_META_KEYS:
            val = meta.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    local = _local_variant_name(project, meta if isinstance(meta, dict) else None)
    names = f"{variant or ''}\n{local}"
    blob = f"{names}\n{(master or '')[:1200]}"
    if looks_knitted(blob):
        return "knitted"
    if looks_noir(names):
        return "noir"
    return None


def _warn_no_style_once() -> None:
    global _NO_STYLE_WARNED
    if _NO_STYLE_WARNED:
        return
    _NO_STYLE_WARNED = True
    logger.warning(
        "img_pr_style: no project style configured — leaving scene unwrapped "
        "(not injecting Archival Noir)"
    )


def _wrap_with_block(body: str, block: str) -> str:
    if "Final style lock" in block and "STYLE:" in block:
        idx = block.find("Final style lock")
        head = block[:idx].strip()
        tail = block[idx:].strip()
        if head and tail:
            return f"{head}\n\n{body}\n\n{tail}"
    return f"{block}\n\n{body}"


def wrap_scene_with_style(
    scene_body: str,
    *,
    style_id: str | None = None,
    style_block: str | None = None,
) -> str:
    """No-op: стиль больше не вшиваем — возвращаем сцену как есть."""
    _ = style_id, style_block
    return (scene_body or "").strip()


def wrap_ops_styles(
    ops: list[dict],
    *,
    style_id: str | None = None,
    style_block: str | None = None,
) -> list[dict]:
    """No-op: ops без STYLE-обёртки."""
    _ = style_id, style_block
    return list(ops)
