"""Роли аттачей для GPT Image 2: identity vs style/scene.

Модель копирует людей с любой приложенной картинки и игнорирует текстовый
блок персонажа. Особенно ломается на turnaround-листе (несколько тел/голов
одного id). Здесь: кроп одного тела + Image N по фактическому порядку файлов.
"""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from app.services.character_sheet_ref import identity_ref_from_sheet
from app.services.hero_ref_prompt import fit_prompt_for_outsee, rewrite_hero_ref_prompt

# identity | parent_still | item | style | other
RefRole = str

_CHAR_STEM_RE = re.compile(r"^c\d{2}", re.IGNORECASE)
_FRAME_KIND_RE = re.compile(
    r"frame_\d+_(character|item|background|other)_",
    re.IGNORECASE,
)


def classify_attached_ref(path: Path) -> tuple[RefRole, str | None]:
    """Роль файла и cNN, если это лист персонажа."""
    p = Path(path)
    parent = p.parent.name.lower()
    stem = p.stem.lower().replace("_front", "")
    name = p.name.lower()

    frame_kind = _FRAME_KIND_RE.search(name)
    if frame_kind:
        kind = frame_kind.group(1).lower()
        if kind == "character":
            return "identity", _char_id_from_stem(stem)
        if kind == "item":
            return "item", None
        if kind == "background":
            return "style", None
        return "other", None

    char_id = _char_id_from_stem(stem)
    if parent == "characters" or (char_id and parent in {"tmp_gpt", "ref_crops", "crops"}):
        return "identity", char_id
    if char_id and parent != "scenes":
        return "identity", char_id
    if parent == "items":
        return "item", None
    if parent in {"backgrounds", "locations"}:
        return "style", None
    if parent == "scenes" or stem.startswith("frame_"):
        return "parent_still", None
    return "other", None


def _char_id_from_stem(stem: str) -> str | None:
    token = stem.split("_", 1)[0]
    if _CHAR_STEM_RE.match(token):
        return token.lower()[:3]
    return None


def crop_identity_refs(refs: list[Path], cache_dir: Path) -> list[Path]:
    """Кропнуть только turnaround-листы; остальные файлы не трогать."""
    out: list[Path] = []
    cache = Path(cache_dir)
    for src in refs:
        role, _cid = classify_attached_ref(src)
        if role != "identity":
            out.append(src)
            continue
        ident = identity_ref_from_sheet(src, cache)
        if ident != src:
            logger.info(
                "image_ref_lock: sheet {} → identity crop {}",
                src.name,
                ident.name,
            )
        out.append(ident)
    return out


def strip_leading_attach_lock(text: str) -> str:
    """Снять прежний HARD CAST / Image N — его мог написать агент вкривую."""
    return split_identity_lock(text)[1]


def split_identity_lock(text: str) -> tuple[str, str]:
    """Префикс Image N / HARD CAST и остаток сцены."""
    raw = (text or "").strip()
    rest = raw
    while rest.startswith("HARD CAST LOCK:") or rest.startswith("Image 1 is the"):
        idx = rest.find("\n\n")
        if idx < 0:
            return raw, ""
        rest = rest[idx + 2 :].strip()
    if rest == raw:
        return "", raw
    if not rest:
        return raw, ""
    if raw.endswith(rest):
        return raw[: -len(rest)].rstrip(), rest
    return "", raw


def with_attached_ref_lock(
    prompt: str,
    refs: list[Path],
    *,
    sheet_ids: list[str] | None = None,
) -> str:
    """Первый абзац: Image N = роль реального аттача, не выдумка агента."""
    raw = strip_leading_attach_lock(prompt)
    if not refs:
        return raw
    wanted = [str(x).strip().lower() for x in (sheet_ids or []) if str(x).strip()]
    lines: list[str] = []
    identity_ids: list[str] = []
    for i, src in enumerate(refs, start=1):
        role, cid = classify_attached_ref(src)
        if role == "identity":
            rid = cid or (wanted[len(identity_ids)] if len(identity_ids) < len(wanted) else "")
            if rid:
                identity_ids.append(rid)
                lines.append(
                    f"Image {i} is the identity reference of {rid} — use this "
                    f"face, hair, body and clothes for person {rid} only. "
                    "A character sheet grid repeats one person on purpose; "
                    "copy identity, not the grid, not extra bodies, not the pose."
                )
            else:
                lines.append(
                    f"Image {i} is an identity reference — one body only, "
                    "do not copy the sheet grid."
                )
            continue
        if role == "parent_still":
            lines.append(
                f"Image {i} is the previous coverage still of the SAME scene "
                "(layout / set / wardrobe / lighting lock). "
                "Preserve the people already visible; do not invent extras. "
                "Change camera and this shot's action only."
            )
            continue
        if role == "item":
            lines.append(
                f"Image {i} is an OBJECT reference only. Copy the object, "
                "not any person who happens to be in that picture."
            )
            continue
        lines.append(
            f"Image {i} is style / mood / location only. "
            "Do not clone faces or bodies from this image. "
            "Cast comes from the prompt and from identity references, "
            "not from people who appear in a style still."
        )
    if len(identity_ids) >= 2:
        named = " and ".join(identity_ids)
        lines.append(
            f"{named} are TWO different people from TWO different attached "
            "images. Show two distinct bodies and two distinct faces — "
            "not twins, not one man copied twice, not a merge of the sheets."
        )
    elif len(identity_ids) == 1:
        lines.append(
            f"Exactly one living body of {identity_ids[0]}. "
            "Do not spawn copies from the sheet."
        )
    if any(classify_attached_ref(src)[0] != "identity" for src in refs):
        lines.append(
            "If a non-identity attach contains a person, ignore that person. "
            "Do not replace the written cast with people cloned from refs."
        )
    return "\n".join(lines) + "\n\n" + raw


def prepare_refs_and_prompt(
    prompt: str,
    refs: list[Path],
    *,
    sheet_ids: list[str] | None = None,
    cache_dir: Path | None = None,
    child: bool = False,
) -> tuple[str, list[Path]]:
    """Кроп листов + lock, согласованный с порядком image_urls."""
    raw = (prompt or "").strip()
    if not refs:
        return raw, []
    prepared = list(refs)
    if cache_dir is not None:
        prepared = crop_identity_refs(prepared, Path(cache_dir))
    roles = [classify_attached_ref(p)[0] for p in prepared]
    mixed = any(r != "identity" for r in roles)
    if child:
        locked = with_attached_ref_lock(raw, prepared, sheet_ids=sheet_ids)
        return rewrite_hero_ref_prompt(locked, [], child=True), prepared
    locked = with_attached_ref_lock(raw, prepared, sheet_ids=sheet_ids)
    if mixed:
        return fit_prompt_for_outsee(locked), prepared
    ids = [
        classify_attached_ref(p)[1]
        or (sheet_ids[i] if sheet_ids and i < len(sheet_ids) else "")
        for i, p in enumerate(prepared)
    ]
    ids = [x for x in ids if x]
    return rewrite_hero_ref_prompt(locked, ids), prepared
