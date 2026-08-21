"""Локальный fallback промтов анимации (Veo), когда kie.ai на maintenance.

Не заменяет GPT-vision качеством, но даёт валидные R48/DB промты:
NO VOICE · continuous take · без новых props · 9:16.
"""

from __future__ import annotations

import re

from app.models import Frame
from app.services.animation_prompt_gpt import MIN_ANIM_PROMPT_LEN

_NO_TAIL = (
    "--no voice, speech, dialogue, lip-sync, narration, text, subtitles, logos, "
    "watermarks, abrupt cuts, shot changes, scene changes, montage, multi-shot, "
    "style shift, added characters, new objects, new props, camera shake"
)

_CAMERAS = (
    "slight push-in toward the main subject, single continuous take, no cuts",
    "lock-off with micro breathing motion, single continuous take, no cuts",
    "slow pan right across existing elements, single continuous take, no cuts",
    "slow pull-out revealing the same composition, single continuous take, no cuts",
)


def _gist_from_image_prompt(image_prompt: str, *, max_chars: int = 420) -> str:
    text = (image_prompt or "").strip()
    if not text:
        return "the reference still image"

    chunks: list[str] = []
    for label in (
        "Background (detailed)",
        "Action",
        "Accent / focal point",
        "Scene sense (visible)",
        "Scene sense",
    ):
        m = re.search(
            rf"{re.escape(label)}\s*:\s*(.+?)(?="
            r"\b(?:Background \(detailed\)|Action|Accent / focal point|"
            r"Scene sense \(visible\)|Scene sense|Light|Negative|SAFE ARCHIVE)\b"
            r"|$)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if not m:
            continue
        part = re.sub(r"\s+", " ", m.group(1)).strip(" .;")
        if not part or part.lower().startswith("нет исходных"):
            continue
        chunks.append(part)

    if not chunks:
        # RU-сцена после STYLE-блока (без английских маркеров).
        m = re.search(
            r"never change medium\.\s*(.+?)(?:\bNegative\b|\bSAFE ARCHIVE\b|$)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            chunks.append(re.sub(r"\s+", " ", m.group(1)).strip(" .;"))

    if not chunks:
        m = re.search(
            r"(?:сюжет|scene|subject)\s*[:\-]\s*(.+)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if m:
            chunks.append(re.sub(r"\s+", " ", m.group(1)).strip(" .;"))

    gist = " ".join(chunks).strip()
    # Не тащить STYLE/палитру в video-промт.
    gist = re.sub(r"^STYLE\s*:[^.]*\.\s*", "", gist, flags=re.IGNORECASE)
    if len(gist) > max_chars:
        gist = gist[: max_chars - 1].rstrip() + "…"
    return gist or "the reference still image"


def compose_local_animation_prompt(frame: Frame) -> str:
    """Собрать один Veo-промт из image_prompt + voiceover (без GPT)."""
    gist = _gist_from_image_prompt(frame.image_prompt or "")
    vo = re.sub(r"\s+", " ", (frame.voiceover_text or "").strip())
    if len(vo) > 160:
        vo = vo[:159].rstrip() + "…"
    meaning = re.sub(r"\s+", " ", (getattr(frame, "meaning", None) or "").strip())
    if len(meaning) > 160:
        meaning = meaning[:159].rstrip() + "…"

    sense_bits = [f"Animate the archival noir reference still: {gist}."]
    if meaning:
        sense_bits.append(f"Scene meaning: {meaning}.")
    elif vo:
        sense_bits.append(
            f"Visual beat supporting narration (do not speak/lip-sync): {vo}."
        )
    sense = " ".join(sense_bits)

    cam = _CAMERAS[(int(frame.number) - 1) % len(_CAMERAS)]
    prompt = (
        f"{sense} "
        f"Camera: {cam}. "
        "Foreground: subtle natural motion of already-visible people/objects only "
        "(cloth, breath, hand micro-move, steam/dust if already present). "
        "Midground/background: slow atmospheric drift of existing environment only. "
        "Light: soft archival grunge light shift, no palette/era change. "
        "Tone: tense documentary noir. "
        "Constraints: NO VOICE, silent, no speech, no dialogue, no lip-sync, mouth closed or idle, "
        "no new objects/props, no new characters, keep composition and style of the reference PNG, "
        "single continuous take, no cuts, no shot/scene changes, no text/subtitles/logos, "
        "aspect 9:16, duration 8s. "
        f"{_NO_TAIL}"
    )
    prompt = re.sub(r"\s+", " ", prompt).strip()
    if len(prompt) < MIN_ANIM_PROMPT_LEN:
        prompt = (
            "Animate the reference still with slight push-in, single continuous take. "
            "NO VOICE, silent, no new props, no cuts. " + _NO_TAIL
        )
    return prompt


def build_local_ops_for_missing(
    frames: list[Frame],
    *,
    shot: int = 1,
    force: bool = False,
) -> list[dict]:
    """apply-ops для кадров без animation_prompt (shot_01 → промт_видео).

    force=True — перезаписать уже заполненные (после улучшения gist).
    """
    field = "промт_видео" if shot == 1 else "промт_видео_2"
    ops: list[dict] = []
    for fr in frames:
        if not fr.uuid:
            continue
        if (
            not force
            and shot == 1
            and len((fr.animation_prompt or "").strip()) >= MIN_ANIM_PROMPT_LEN
        ):
            continue
        text = compose_local_animation_prompt(fr)
        ops.append({"frame_uuid": fr.uuid, "fields": {field: text}})
    return ops
