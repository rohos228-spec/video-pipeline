"""Промты с рефами героев: 1 референс = 1 свой человек, не клон одного лица."""

from __future__ import annotations

import re

from app.generation_options import OUTSEE_PROMPT_TARGET_BODY_CHARS

_LOCK = (
    "ты обязан указать с каждого референса по 1 персонажу, "
    "клоны, близнецы запрещены"
)
_HARD_HEAD = "HARD CAST LOCK:"
_C_ID_RE = re.compile(r"\bc\d{2}\b", re.IGNORECASE)
_STYLE_LOCK_LINE_RE = re.compile(
    r"(?im)^(?:STYLE:|Final style lock|Negative:)",
)

# Глобальный «no duplicate faces» модель читает как «в кадре одно лицо».
_TWIN_SWAPS: tuple[tuple[str, str], ...] = (
    (
        "no twins, no clones, no duplicate faces",
        "do not clone the same character id; each reference is a different person with a different face",
    ),
    (
        "no twins, no clones, no duplicate face",
        "do not clone the same character id; each reference is a different person with a different face",
    ),
    (
        "no twins/clones/duplicate faces of the same character id",
        "do not clone the same character id onto two bodies",
    ),
    (
        "twins, clones, duplicate identical faces, mirrored double of same character",
        "copying one reference face onto two bodies, cloning the same id, mirrored double of the same id",
    ),
    (
        "no duplicate identical faces",
        "do not copy one reference face onto another person's body",
    ),
    (
        "identity lock for this ONE person only",
        "identity lock for each listed person separately — one body per reference",
    ),
    (
        "Exactly one body of this id — no twins, no clones, no duplicate face",
        "Exactly one body of this id from this reference; other references are other people",
    ),
    (
        "one body only, no twins/clones",
        "one body per referenced id, different face per reference",
    ),
)


def char_ids_from_prompt(prompt: str) -> list[str]:
    out: list[str] = []
    for match in _C_ID_RE.finditer(prompt or ""):
        rid = match.group(0).lower()
        if rid not in out:
            out.append(rid)
    return out


def _strip_existing_lock(text: str) -> str:
    raw = (text or "").strip()
    while raw.startswith(_HARD_HEAD) or raw.startswith(_LOCK):
        idx = raw.find("\n\n")
        if idx < 0:
            return ""
        raw = raw[idx + 2 :].strip()
    return raw


def _replace_twin_language(text: str) -> str:
    out = text
    for old, new in _TWIN_SWAPS:
        out = out.replace(old, new)
    return out


def _cast_lock(ids: list[str]) -> str:
    mapped = ""
    if len(ids) == 1:
        mapped = f" Image 1={ids[0]} only, one body, do not clone."
    elif len(ids) >= 2:
        bits = " ".join(f"Image {i}={rid}" for i, rid in enumerate(ids, 1))
        mapped = (
            f" {bits}. Both must appear as different people. "
            "Using only one reference = fail."
        )
    return (
        f"{_HARD_HEAD} 1 ref=1 character. Use EVERY attached reference. "
        "Do not change look from the reference. No clones/duplicates. "
        f"{_LOCK} Вид с референса не менять.{mapped}"
    )


def _split_style_lock(text: str) -> tuple[str, str]:
    raw = text or ""
    match = _STYLE_LOCK_LINE_RE.search(raw)
    start = match.start() if match else raw.find("STYLE:")
    if start < 0:
        return raw, ""
    return raw[:start].rstrip(), raw[start:].strip()


def _cut_at_space(text: str, body_limit: int) -> str:
    if len(text) <= body_limit:
        return text
    cut = text[:body_limit]
    sp = cut.rfind(" ")
    if sp > int(body_limit * 0.85):
        cut = cut[:sp]
    return cut


def fit_prompt_for_outsee(
    text: str,
    max_chars: int = OUTSEE_PROMPT_TARGET_BODY_CHARS,
) -> str:
    """Влезает в Outsee без GPT-сжатия: режем сцену, lock и STYLE не трогаем."""
    raw = (text or "").strip()
    if len(raw) <= max_chars:
        return raw
    scene, style = _split_style_lock(raw)
    if not style:
        return _cut_at_space(raw, max_chars)
    sep = 2 if scene else 0
    scene_limit = max_chars - len(style) - sep
    if scene_limit < 40:
        if len(style) <= max_chars:
            return style
        return _cut_at_space(style, max_chars)
    return f"{_cut_at_space(scene, scene_limit)}\n\n{style}"


def rewrite_hero_ref_prompt(
    prompt: str,
    char_ids: list[str] | None = None,
    *,
    child: bool = False,
) -> str:
    """Переписать промт: 1 hero-ref = 1 персонаж, без схлопывания лиц."""
    raw = (prompt or "").strip()
    if not raw:
        return raw
    ids = [str(x).strip().lower() for x in (char_ids or []) if str(x).strip()]
    if not ids:
        ids = char_ids_from_prompt(raw)
    text = _replace_twin_language(_strip_existing_lock(raw))
    if child:
        note = (
            f"{_LOCK}. Люди уже на кадре-референсе — разные персонажи, "
            "не делай их близнецами с одним лицом."
        )
        if text.startswith("Image 1 is the previous coverage still"):
            return fit_prompt_for_outsee(text)
        return fit_prompt_for_outsee(note + "\n\n" + text)
    lock = _cast_lock(ids)
    if text.startswith("Image 1 is the previous coverage still"):
        return fit_prompt_for_outsee(text)
    return fit_prompt_for_outsee(lock + "\n\n" + text)


def prompt_has_hero_ref(prompt: str, char_ids: list[str] | None = None) -> bool:
    if char_ids:
        return True
    p = (prompt or "").lower()
    if "character sheet" in p or p.startswith("reference:"):
        return True
    return bool(char_ids_from_prompt(prompt or ""))
