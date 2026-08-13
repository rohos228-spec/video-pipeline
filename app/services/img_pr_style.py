"""STYLE LOCK для img_pr — вшивается пайплайном, не GPT.

GPT пишет только сцену (фон/действие/свет/accent/…). Полный style-блок
для Outsee добавляем здесь → меньше токенов на ответ → крупные батчи.

Стиль НЕ хардкодится: выбирается из проекта (`meta.img_pr_style_id`,
дальше `image_style`/`visual_style`/`img_style`/`style` — конвенция
`scene_design.context_builder`). Стиль не задан → сцена проходит без
обёртки + warning (инцидент #14: knitted-проект получил 110 нуар-обёрток).
Явный нуар доступен как ``style_id="noir"``.
"""

from __future__ import annotations

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
    "STYLE: Knitted Wool Felt Textile Illustration. "
    "handmade knitted wool texture + soft felted fabric + cozy "
    "children's-book textile illustration + warm storybook fiber art + "
    "soft sculpture yarn craft. Visible yarn stitches, chunky knit rows, "
    "loose wool fuzz, felt appliqué shapes, embroidered contour stitches, "
    "stuffed soft volume, woven textile background, tiny handmade "
    "imperfections. Palette: warm cream, soft beige, muted terracotta, "
    "dusty rose, moss green, warm gray-brown wool tones, gentle natural "
    "daylight. One unified textile frame, not collage panels. Improve "
    "only inside this style (richer knit texture, softer felt), never "
    "change medium."
)

KNITTED_STYLE_TAIL = (
    "Final style lock: unified cozy frame, Knitted Wool Felt Textile "
    "Illustration, visible yarn stitches and felted fibers, handmade soft "
    "sculpture volume, embroidered details, woven background, warm wool "
    "palette, soft diffused daylight, no photorealism, no glossy 3D, "
    "no plastic or metal surfaces, no neon, no dark horror tones, "
    "no multi-panel collage, no readable text/letters/logos, "
    "character id when present, no copied voiceover. "
    "Negative: photorealism, glossy 3D render, plastic, metal, glass, "
    "neon cyberpunk, flat digital color, smooth vector gradients, "
    "dark gore, blood, horror lighting, readable text, captions, "
    "subtitles, watermark, multi-panel collage, separate collage panels, "
    "twins, clones, duplicate identical faces, mirrored double of same "
    "character, style drift away from knitted wool felt textile "
    "illustration."
)

_KNITTED_STYLE_MARKER = "Knitted Wool Felt Textile Illustration"

# Реестр STYLE-lock'ов: style_id → (head, tail).
# None = обёртка отключена: пластилин пишет стиль сам (×3 внутри промта),
# пайплайн ничего не вшивает (см. is_plastilin_master / _PLASTILIN_*).
STYLE_REGISTRY: dict[str, tuple[str, str] | None] = {
    "noir": (STYLE_HEAD, STYLE_TAIL),
    "knitted": (KNITTED_STYLE_HEAD, KNITTED_STYLE_TAIL),
    "plasticine": None,
}

# Алиасы свободных значений из meta проекта → ключи STYLE_REGISTRY.
_STYLE_ALIASES = {
    "noir": "noir",
    "archival noir": "noir",
    "archival noir watercolor": "noir",
    "archival noir watercolor grunge dossier poster illustration": "noir",
    "нуар": "noir",
    "knitted": "knitted",
    "knit": "knitted",
    "knitted wool": "knitted",
    "вязаный": "knitted",
    "вязанный": "knitted",
    "вязаная": "knitted",
    "войлок": "knitted",
    "войлочный": "knitted",
    "felt": "knitted",
    "textile": "knitted",
    "текстиль": "knitted",
    "текстильный": "knitted",
    "plasticine": "plasticine",
    "plastilin": "plasticine",
    "пластилин": "plasticine",
    "пластилиновый": "plasticine",
    "clay": "plasticine",
    "claymation": "plasticine",
}

# Ключи meta проекта, где искать стиль. Первый — канонический для img_pr,
# остальные — существующая конвенция scene_design.context_builder.
PROJECT_STYLE_META_KEYS = (
    "img_pr_style_id",
    "image_style",
    "visual_style",
    "img_style",
    "style",
)


def normalize_style_id(value: object) -> str | None:
    """Свободное значение (id/алиас/RU) → ключ STYLE_REGISTRY или None."""
    if not isinstance(value, str):
        return None
    v = value.strip().casefold()
    if not v:
        return None
    if v in STYLE_REGISTRY:
        return v
    return _STYLE_ALIASES.get(v)


def resolve_project_style_id(meta: object) -> str | None:
    """style_id из meta проекта (``img_pr_style_id`` → прочие style-ключи)."""
    if not isinstance(meta, dict):
        return None
    for key in PROJECT_STYLE_META_KEYS:
        sid = normalize_style_id(meta.get(key))
        if sid:
            return sid
    return None


def _style_blocks(
    *,
    style_id: str | None = None,
    style_block: str | None = None,
) -> tuple[str, str] | None:
    """(head, tail) для обёртки; None — оборачивать нечем/не нужно."""
    if isinstance(style_block, str) and style_block.strip():
        return style_block.strip(), ""
    sid = normalize_style_id(style_id)
    if sid is None:
        if isinstance(style_id, str) and style_id.strip():
            logger.warning(
                "img_pr style_id={!r} unknown (registry: {}) — no wrap applied",
                style_id,
                sorted(STYLE_REGISTRY),
            )
        else:
            logger.warning("img_pr style not set — no wrap applied")
        return None
    return STYLE_REGISTRY[sid]


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


def is_plastilin_master(
    variant: str | None = None, master: str | None = None
) -> bool:
    blob = f"{variant or ''}\n{(master or '')[:1200]}".casefold()
    return any(m in blob for m in PLASTILIN_MASTER_MARKERS)


def already_has_style(text: str) -> bool:
    t = text or ""
    if ("Final style lock" in t or "STYLE:" in t) and (
        _STYLE_MARKER in t or _KNITTED_STYLE_MARKER in t
    ):
        return True
    low = t.casefold()
    return any(m in low for m in _CLAY_STYLE_MARKERS)


def _wrap_with_blocks(text: str, blocks: tuple[str, str] | None) -> str:
    body = (text or "").strip()
    if not body:
        return body
    if already_has_style(body):
        return body
    if blocks is None:
        return body
    head, tail = blocks
    if tail:
        return f"{head}\n\n{body}\n\n{tail}"
    return f"{head}\n\n{body}"


def wrap_scene_with_style(
    scene_body: str,
    *,
    style_id: str | None = None,
    style_block: str | None = None,
) -> str:
    """Сцена от GPT + STYLE LOCK → готовый промт Outsee.

    Разрешение стиля: явный ``style_block`` → ``style_id`` из
    ``STYLE_REGISTRY`` → None (стиль не задан: сцена как есть + warning,
    никакого молчаливого нуара). ``style_id="plasticine"`` — обёртка
    отключена, стиль внутри промта пишет GPT.
    """
    body = (scene_body or "").strip()
    if not body:
        return body
    if already_has_style(body):
        return body
    blocks = _style_blocks(style_id=style_id, style_block=style_block)
    return _wrap_with_blocks(body, blocks)


def wrap_ops_styles(
    ops: list[dict],
    *,
    style_id: str | None = None,
    style_block: str | None = None,
) -> list[dict]:
    """Вшить STYLE в промт_картинки каждого op (shot2 не трогаем — короткий).

    ``style_block`` (явный текст) побеждает ``style_id``. Стиль не задан →
    ops проходят без обёртки + warning. ``style_id="plasticine"`` — skip
    без warning (стиль уже внутри промта от GPT).
    """
    if not ops:
        return []
    blocks = _style_blocks(style_id=style_id, style_block=style_block)
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
                new_fields[key] = _wrap_with_blocks(new_fields[key], blocks)
                break
        cloned = dict(op)
        cloned["fields"] = new_fields
        out.append(cloned)
    return out
