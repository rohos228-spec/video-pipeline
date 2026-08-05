"""Trash-polka STYLE LOCK — вшивается пайплайном, не GPT.

GPT пишет только сцену (фон/действие/свет/accent/…). Полный style-блок
для Outsee добавляем здесь → меньше токенов на ответ → крупные батчи.
"""

from __future__ import annotations

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

_PROMPT_FIELD_KEYS = (
    "промт_картинки",
    "image_prompt",
    "промпт_картинки",
)


def already_has_style(text: str) -> bool:
    t = text or ""
    return _STYLE_MARKER in t and ("Final style lock" in t or "STYLE:" in t)


def wrap_scene_with_style(scene_body: str) -> str:
    """Сцена от GPT + STYLE LOCK → готовый промт Outsee."""
    body = (scene_body or "").strip()
    if not body:
        return body
    if already_has_style(body):
        return body
    return f"{STYLE_HEAD}\n\n{body}\n\n{STYLE_TAIL}"


def wrap_ops_styles(ops: list[dict]) -> list[dict]:
    """Вшить STYLE в промт_картинки каждого op (shot2 не трогаем — короткий)."""
    out: list[dict] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            out.append(op)
            continue
        new_fields = dict(fields)
        for key in _PROMPT_FIELD_KEYS:
            if key in new_fields and isinstance(new_fields[key], str):
                new_fields[key] = wrap_scene_with_style(new_fields[key])
                break
        cloned = dict(op)
        cloned["fields"] = new_fields
        out.append(cloned)
    return out
