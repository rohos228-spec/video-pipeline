"""ChatGPT batch-flow для шага «Промты анимации» (anim_pr).

Один диалог:
  1) мастер-промт + закадровый текст (все кадры);
  2) пачки до 5 картинок + для каждой «ID изображения» и «Закадровый текст»;
  3) парсим ответы «ID изображения» / «текст анимации» и пишем в xlsx (план R48).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.generation_options import build_gen_id_prefix
from app.models import Frame, Project
from app.services import gpt_text_builder as gtb

BATCH_SIZE = 5

_ID_IN_PROMPT_RE = re.compile(
    r"\[ID:\s*P\d+-F(\d+)-[a-f0-9]+\]",
    re.IGNORECASE,
)
_FRAME_FROM_ID_RE = re.compile(
    r"F(\d+)",
    re.IGNORECASE,
)

# Блоки ответа GPT: ID изображения + текст анимации
_REPLY_BLOCK_RE = re.compile(
    r"ID\s+изображения\s*:\s*(?P<id>.+?)\s*"
    r"текст\s+анимации\s*:\s*(?P<text>.+?)"
    r"(?=ID\s+изображения\s*:|$)",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class FrameImageBatchItem:
    frame: Frame
    image_path: Path
    image_id: str
    voiceover: str


def _normalize_ws(s: str) -> str:
    return " ".join((s or "").split())


def scene_image_path(project: Project, frame_number: int) -> Path | None:
    """Последний `scenes/frame_NNN_*.png` для кадра."""
    scenes_dir = project.data_dir / "scenes"
    if not scenes_dir.exists():
        return None
    candidates = sorted(
        scenes_dir.glob(f"frame_{frame_number:03d}_*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def image_id_for_frame(project: Project, frame: Frame, image_path: Path | None) -> str:
    """Строка «ID изображения» для ChatGPT — как в outsee (`[ID: P…-F…-uuid8]`)."""
    ip = frame.image_prompt or ""
    m = _ID_IN_PROMPT_RE.search(ip)
    if m:
        start = ip.find("[ID:")
        end = ip.find("]", start)
        if end > start:
            return ip[start : end + 1].strip()

    if image_path is not None:
        stem = image_path.stem  # frame_003_a7f2b01c
        parts = stem.split("_")
        if len(parts) >= 3 and parts[-1]:
            short = parts[-1][:8]
            return build_gen_id_prefix(project.id, frame.number, short)

    return build_gen_id_prefix(project.id, frame.number, "00000000")


def build_initial_message(project: Project, frames: list[Frame]) -> str:
    """Первое сообщение в чат: override или мастер + закадровые тексты."""
    override = gtb.get_override(project, "anim_pr")
    if override is not None:
        return override
    return gtb.build_anim_pr_initial_default(project, frames)


def build_batch_message(items: list[FrameImageBatchItem]) -> str:
    """Текст к пачке изображений (без мастер-промта)."""
    parts: list[str] = [
        "По каждому изображению ниже верни пару строк строго в формате:\n"
        "ID изображения: …\n"
        "текст анимации: …\n"
        "(без пояснений вне этих полей; в «текст анимации» — только готовый промт)\n",
    ]
    for it in items:
        parts.append(f"ID изображения: {it.image_id}")
        parts.append(f"Закадровый текст: {it.voiceover}")
        parts.append("")
    return "\n".join(parts).strip()


def collect_batch_items(
    project: Project,
    frames: list[Frame],
) -> list[FrameImageBatchItem]:
    """Кадры с картинкой на диске и без animation_prompt."""
    out: list[FrameImageBatchItem] = []
    for fr in frames:
        if (fr.animation_prompt or "").strip():
            continue
        img = scene_image_path(project, fr.number)
        if img is None:
            continue
        vo = (fr.voiceover_text or "").strip()
        out.append(
            FrameImageBatchItem(
                frame=fr,
                image_path=img,
                image_id=image_id_for_frame(project, fr, img),
                voiceover=vo or "—",
            )
        )
    return out


def _frame_from_image_id(frames: list[Frame], image_id: str) -> Frame | None:
    m = _FRAME_FROM_ID_RE.search(image_id or "")
    if not m:
        return None
    num = int(m.group(1))
    for fr in frames:
        if fr.number == num:
            return fr
    return None


def _frame_from_voiceover(frames: list[Frame], voiceover: str) -> Frame | None:
    norm = _normalize_ws(voiceover)
    if not norm or norm == "—":
        return None
    for fr in frames:
        if _normalize_ws(fr.voiceover_text or "") == norm:
            return fr
    return None


def _clean_animation_text(raw: str) -> str:
    t = (raw or "").strip()
    # Убираем повторную метку в начале, если модель продублировала.
    t = re.sub(
        r"^текст\s+анимации\s*:\s*",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip()
    return t


@dataclass(frozen=True)
class ParsedAnimationPair:
    image_id: str
    animation_text: str
    frame_number: int | None


def parse_animation_reply(
    reply: str,
    frames: list[Frame],
    *,
    batch_items: list[FrameImageBatchItem],
) -> list[ParsedAnimationPair]:
    """Извлекает пары ID / текст анимации из ответа GPT."""
    results: list[ParsedAnimationPair] = []
    seen_frames: set[int] = set()

    for m in _REPLY_BLOCK_RE.finditer(reply or ""):
        image_id = (m.group("id") or "").strip()
        anim = _clean_animation_text(m.group("text") or "")
        if len(anim) < 10:
            continue
        fr = _frame_from_image_id(frames, image_id)
        if fr is None:
            # Попробуем сопоставить по закадровому из batch (порядок ID в ответе)
            for it in batch_items:
                if it.image_id in image_id or image_id in it.image_id:
                    fr = it.frame
                    break
        if fr is None:
            continue
        if fr.number in seen_frames:
            continue
        seen_frames.add(fr.number)
        results.append(
            ParsedAnimationPair(
                image_id=image_id,
                animation_text=anim,
                frame_number=fr.number,
            )
        )

    # Fallback: если модель не выдержала формат — по одному блоку на кадр из batch
    if not results and batch_items:
        chunks = [c.strip() for c in re.split(r"\n{2,}", reply or "") if c.strip()]
        for it, chunk in zip(batch_items, chunks, strict=False):
            anim = _clean_animation_text(chunk)
            if len(anim) >= 10:
                results.append(
                    ParsedAnimationPair(
                        image_id=it.image_id,
                        animation_text=anim,
                        frame_number=it.frame.number,
                    )
                )
    return results
