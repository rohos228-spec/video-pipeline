"""ChatGPT batch-flow для шага «Промты анимации» (anim_pr).

Один диалог:
  1) сопр. промт + файл мастер-промта (один раз);
  2) пачки: до 5 картинок + ID и закадровый по каждому кадру;
  3) парсим «ID изображения» / «текст анимации» → xlsx план R48.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.generation_options import build_gen_id_prefix
from app.models import Frame, FrameStatus, Project
from app.services import gpt_text_builder as gtb
from app.storage.plan_sheet_v8 import (
    read_plan_animation_prompt_cells,
    read_plan_voiceover,
)

MIN_ANIM_PROMPT_LEN = 10

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


def build_initial_message(
    project: Project,
    frames: list[Frame],
    *,
    prompt_file_name: str,
) -> str:
    """Первое сообщение: сопр. текст + мастер-промт файлом (без картинок)."""
    _ = frames
    override = gtb.get_override(project, "anim_pr")
    if override is not None:
        return (
            override.strip()
            + f"\n\n(Мастер-промт — в прикреплённом файле {prompt_file_name}.)"
        )
    return gtb.build_anim_pr_initial_default(
        project, prompt_file_name=prompt_file_name
    )


def voiceover_for_frame(project: Project, frame: Frame) -> str:
    """Закадровый текст: только лист «план» R49 (одна ячейка = один кадр)."""
    from_plan = read_plan_voiceover(project, frame.number)
    if from_plan:
        return from_plan.strip()
    return (frame.voiceover_text or "").strip()


def build_batch_message(items: list[FrameImageBatchItem]) -> str:
    """Текст к пачке: ID изображения + закадровый для каждого кадра (фото отдельно)."""
    parts: list[str] = [
        "По каждому прикреплённому изображению ответь строго:",
        "ID изображения: …",
        "текст анимации: …",
        "(в «текст анимации» — только готовый промт для видео)\n",
    ]
    for it in items:
        parts.append(f"ID изображения: {it.image_id}")
        parts.append(f"Закадровый текст: {it.voiceover}")
        parts.append("")
    return "\n".join(parts).strip()


async def sync_animation_prompts_from_xlsx(
    session: AsyncSession, project: Project
) -> int:
    """Лист «план» R48 → Frame.animation_prompt (источник истины при догонке)."""
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        return 0
    cells = read_plan_animation_prompt_cells(project, [f.number for f in frames])
    by_num = dict(cells)
    changed = 0
    for fr in frames:
        text = (by_num.get(fr.number) or "").strip()
        if len(text) < MIN_ANIM_PROMPT_LEN:
            continue
        if text == (fr.animation_prompt or "").strip():
            continue
        fr.animation_prompt = text
        if fr.status not in (
            FrameStatus.video_approved,
            FrameStatus.video_generated,
            FrameStatus.done,
        ):
            fr.status = FrameStatus.animation_prompt_ready
        changed += 1
    if changed:
        await session.flush()
        logger.info(
            "[#{}] sync_animation_prompts_from_xlsx: {} кадров из plan R48 → БД",
            project.id,
            changed,
        )
    return changed


def scan_missing_animation_prompts(
    project: Project, frames: list[Frame]
) -> list[int]:
    """Кадры с картинкой на диске, но без animation_prompt в БД."""
    missing: list[int] = []
    for fr in frames:
        if (fr.animation_prompt or "").strip():
            continue
        if scene_image_path(project, fr.number) is None:
            continue
        missing.append(fr.number)
    return missing


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
        vo = voiceover_for_frame(project, fr)
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

    # Fallback: порядок блоков = порядок кадров в batch (если ID не распознаны)
    if len(results) < len(batch_items):
        got = {p.frame_number for p in results if p.frame_number is not None}
        chunks = [c.strip() for c in re.split(r"\n{2,}", reply or "") if c.strip()]
        for it, chunk in zip(batch_items, chunks, strict=False):
            if it.frame.number in got:
                continue
            anim = _clean_animation_text(chunk)
            if len(anim) < 10:
                m_anim = re.search(
                    r"текст\s+анимации\s*:\s*(.+)",
                    chunk,
                    re.IGNORECASE | re.DOTALL,
                )
                if m_anim:
                    anim = _clean_animation_text(m_anim.group(1))
            if len(anim) >= 10:
                got.add(it.frame.number)
                results.append(
                    ParsedAnimationPair(
                        image_id=it.image_id,
                        animation_text=anim,
                        frame_number=it.frame.number,
                    )
                )
    return results
