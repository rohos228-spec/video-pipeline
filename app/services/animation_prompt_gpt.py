"""ChatGPT batch-flow для шага «Промты анимации» (anim_pr).

Один диалог:
  1) сопр. промт + файл мастер-промта (один раз);
  2) пачки: до BATCH_SIZE картинок + uuid/закадровый по каждому кадру;
  3) парсим JSON apply-ops (или legacy «ID / текст анимации») → DB / R48.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.generation_options import build_gen_id_prefix
from app.models import Frame, FrameStatus, Project
from app.services import gpt_text_builder as gtb
from app.services.plan_shot2 import (
    MIN_SHOT2_VIDEO_PROMPT_LEN,
    SHOT2_VIDEO_PROMPT_ATTR,
    find_shot2_image,
    read_shot2_columns,
)
from app.storage.plan_sheet_v8 import (
    read_plan_animation_prompt_cells,
    read_plan_animation_prompt_shot2_cells,
    read_plan_voiceover,
    read_plan_voiceover_cells,
    write_plan_animation_prompt_shot2,
)

MIN_ANIM_PROMPT_LEN = 10

BATCH_SIZE = 6
STRIP_GUTTER_PX = 6
STRIP_MAX_HEIGHT_PX = 512
STRIP_MAX_BYTES = 3_500_000

_ANIM_FIELD_NAMES = frozenset(
    {
        "промт_видео",
        "промт_видео_2",
        "animation_prompt",
        "animation_prompt_shot2",
    }
)

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
    return index_scene_image_paths(project).get(int(frame_number))


def index_scene_image_paths(project: Project) -> dict[int, Path]:
    """Один проход по ``scenes/`` → {номер кадра: newest png}.

    Нельзя звать ``glob(frame_NNN_*)`` на каждый кадр — на 200 кадров это
    секунды синка в event loop и «зависание» Studio во время anim_pr.
    """
    scenes_dir = project.data_dir / "scenes"
    if not scenes_dir.is_dir():
        return {}
    best: dict[int, tuple[float, Path]] = {}
    for path in scenes_dir.glob("frame_*_*.png"):
        # frame_003_abcd.png / frame_003_s2_abcd.png — shot1 = без _s2_
        parts = path.stem.split("_")
        if len(parts) < 3:
            continue
        if parts[0] != "frame":
            continue
        if len(parts) >= 4 and parts[2] == "s2":
            continue
        try:
            num = int(parts[1])
        except ValueError:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        prev = best.get(num)
        if prev is None or mtime > prev[0]:
            best[num] = (mtime, path)
    return {n: p for n, (_m, p) in best.items()}


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
    """Закадровый текст кадра.

    Сначала DB (``Frame.voiceover_text``) — иначе каждый вызов открывает
    весь ``project.xlsx`` (openpyxl + file lock) и на 200 кадров блокирует
    event loop на минуты.
    """
    db_vo = (frame.voiceover_text or "").strip()
    if db_vo:
        return db_vo
    from_plan = read_plan_voiceover(project, frame.number)
    if from_plan:
        return from_plan.strip()
    return ""


def hydrate_voiceovers_from_plan(project: Project, frames: list[Frame]) -> int:
    """Один проход R49 → пустые ``Frame.voiceover_text``. Возвращает число заполненных."""
    need = [fr for fr in frames if not (fr.voiceover_text or "").strip()]
    if not need:
        return 0
    cells = dict(read_plan_voiceover_cells(project, [fr.number for fr in need]))
    n = 0
    for fr in need:
        text = (cells.get(fr.number) or "").strip()
        if text:
            fr.voiceover_text = text
            n += 1
    return n


def panel_label_for_item(it: FrameImageBatchItem) -> str:
    """Текст на панели ленты: F003 — совпадает со списком в batch-сообщении."""
    return f"F{it.frame.number:03d}"


def build_batch_message(items: list[FrameImageBatchItem]) -> str:
    """Текст к пачке: одна PNG-лента + uuid/закадровый; ответ — JSON apply-ops."""
    n = len(items)
    parts: list[str] = [
        "Прикреплено одно изображение-лента: кадры идут слева направо, "
        "между ними тонкие белые вертикальные разделители.",
        "На каждой панели снизу чёрная метка FNNN (номер кадра) — "
        "привязывай промт ТОЛЬКО по frame_uuid из списка, не по догадке о порядке.",
        "Порядок слева → направо совпадает со списком ниже.",
        "",
        f"Верни ТОЛЬКО JSON apply-ops на все {n} кадров (без markdown, без прозы):",
        '{"ops":[{"frame_uuid":"…","fields":{"промт_видео":"…"}}, …]}',
        f"Ровно {n} объектов в ops — по одному на каждый frame_uuid ниже.",
        "Нельзя писать весь JSON в одно поле промта — только короткий текст анимации.",
        "",
    ]
    for pos, it in enumerate(items, start=1):
        uuid = (getattr(it.frame, "uuid", None) or "").strip() or it.image_id
        parts.append(f"Позиция {pos} (слева→направо) метка={panel_label_for_item(it)}")
        parts.append(f"frame_uuid: {uuid}")
        parts.append(f"ID изображения: {it.image_id}")
        parts.append(f"Закадровый текст: {it.voiceover}")
        parts.append("")
    return "\n".join(parts).strip()


def build_batch_strip_path(items: list[FrameImageBatchItem], out_dir: Path) -> Path:
    """Склеивает до BATCH_SIZE кадров в ленту для GPT vision.

    Фактический суффикс — `.png` или `.jpg` (если PNG > STRIP_MAX_BYTES).
    """
    from app.services.image_strip import compose_horizontal_strip

    if not items:
        raise ValueError("build_batch_strip_path: items пустой")
    numbers = "_".join(f"{it.frame.number:03d}" for it in items)
    out_path = out_dir / f"anim_pr_strip_{numbers}.png"
    # compose_horizontal_strip может вернуть .jpg вместо запрошенного .png
    return compose_horizontal_strip(
        [it.image_path for it in items],
        out_path,
        gutter_px=STRIP_GUTTER_PX,
        max_height=STRIP_MAX_HEIGHT_PX,
        max_bytes=STRIP_MAX_BYTES,
        panel_labels=[panel_label_for_item(it) for it in items],
    )


def _plan_xlsx_exists(project: Project) -> bool:
    return (project.data_dir / "project.xlsx").is_file()


def animation_prompt_in_plan_xlsx(project: Project, frame_number: int) -> str:
    """Промт анимации из plan R48 (пустая строка, если ячейки нет)."""
    if not _plan_xlsx_exists(project):
        return ""
    cells = read_plan_animation_prompt_cells(project, [frame_number])
    return (cells[0][1] if cells else "").strip()


def is_apply_ops_blob(text: str) -> bool:
    """Сырой JSON apply-ops ошибочно записанный в animation_prompt целиком."""
    t = (text or "").lstrip()
    if not t.startswith("{"):
        return False
    head = t[:240]
    return '"ops"' in head or "'ops'" in head


def has_animation_prompt_for_frame(project: Project, frame: Frame) -> bool:
    """Готов ли промт: источник правды — DB (Frame.animation_prompt)."""
    _ = project  # xlsx больше не SoT для skip
    text = (frame.animation_prompt or "").strip()
    if len(text) < MIN_ANIM_PROMPT_LEN:
        return False
    # Целый JSON-ответ GPT ≠ готовый промт кадра (баг старого парсера).
    if is_apply_ops_blob(text):
        return False
    return True


_VIDEO_DONE_STATUSES = frozenset(
    {
        FrameStatus.video_generated,
        FrameStatus.video_approved,
        FrameStatus.done,
    }
)


async def sync_animation_prompts_from_xlsx(
    session: AsyncSession, project: Project
) -> int:
    """Лист «план» R48 ↔ Frame.animation_prompt.

    Пустая ячейка в xlsx → сброс устаревшего промта в БД (кроме кадров с видео).
    """
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
    xlsx_exists = _plan_xlsx_exists(project)
    for fr in frames:
        text = (by_num.get(fr.number) or "").strip()
        if len(text) < MIN_ANIM_PROMPT_LEN:
            if (
                xlsx_exists
                and fr.animation_prompt
                and fr.status not in _VIDEO_DONE_STATUSES
            ):
                fr.animation_prompt = None
                if fr.status is FrameStatus.animation_prompt_ready:
                    fr.status = (
                        FrameStatus.image_generated
                        if scene_image_path(project, fr.number)
                        else FrameStatus.image_prompt_ready
                    )
                changed += 1
            continue
        if text == (fr.animation_prompt or "").strip():
            continue
        fr.animation_prompt = text
        if fr.status not in _VIDEO_DONE_STATUSES:
            fr.status = FrameStatus.animation_prompt_ready
        changed += 1
    if changed:
        await session.flush()
        logger.info(
            "[#{}] sync_animation_prompts_from_xlsx: {} кадров синхронизировано с plan R48",
            project.id,
            changed,
        )
    return changed


def scan_missing_animation_prompts_shot2(
    project: Project, frames: list[Frame]
) -> list[int]:
    """shot_02: PNG на диске, но нет промта видео в plan R64 / attrs."""
    return [
        fr.number
        for fr in frames
        if frame_needs_shot2_video_prompt(project, fr)
    ]


def scan_missing_animation_prompts(
    project: Project, frames: list[Frame]
) -> list[int]:
    """Кадры с картинкой shot_01 на диске, но без animation_prompt в plan R48 / БД."""
    missing: list[int] = []
    for fr in frames:
        if has_animation_prompt_for_frame(project, fr):
            continue
        if scene_image_path(project, fr.number) is None:
            continue
        missing.append(fr.number)
    return missing


def scan_missing_animation_prompts_all(
    project: Project, frames: list[Frame]
) -> tuple[list[int], list[int]]:
    """(shot_01, shot_02) — кадры без промта анимации."""
    s1 = scan_missing_animation_prompts(project, frames)
    s2 = scan_missing_animation_prompts_shot2(project, frames)
    return s1, s2


def count_animation_prompt_stats(
    project: Project, frames: list[Frame]
) -> tuple[int, int, int]:
    """(готово по xlsx/БД, заполнено в plan R48, кадров с картинкой на диске)."""
    ready = sum(1 for fr in frames if has_animation_prompt_for_frame(project, fr))
    xlsx_filled = 0
    if _plan_xlsx_exists(project):
        nums = [f.number for f in frames]
        cells = dict(read_plan_animation_prompt_cells(project, nums))
        xlsx_filled = sum(
            1 for n in nums if len((cells.get(n) or "").strip()) >= MIN_ANIM_PROMPT_LEN
        )
    img_index = index_scene_image_paths(project)
    with_image = sum(1 for fr in frames if fr.number in img_index)
    return ready, xlsx_filled, with_image


def scene_shot2_image_path(project: Project, frame_number: int) -> Path | None:
    """Последний ``scenes/frame_NNN_s2_*.png`` для кадра."""
    scenes_dir = project.data_dir / "scenes"
    return find_shot2_image(scenes_dir, frame_number)


def image_id_for_shot2_frame(
    project: Project, frame: Frame, image_path: Path | None
) -> str:
    """ID для shot_02 в batch anim_pr (отличается суффиксом ``-s2-``)."""
    if image_path is not None:
        stem = image_path.stem  # frame_003_s2_a7f2b01c
        parts = stem.split("_")
        if len(parts) >= 4 and parts[2] == "s2" and parts[-1]:
            short = parts[-1][:8]
            return f"[ID: P{project.id}-F{frame.number}-s2-{short}]"
    return f"[ID: P{project.id}-F{frame.number}-s2-00000000]"


def build_batch_message_shot2(items: list[FrameImageBatchItem]) -> str:
    """Текст к пачке shot_02 — промт для видео второго кадра (plan R64)."""
    n = len(items)
    parts: list[str] = [
        "Прикреплено одно изображение-лента: это вторые кадры сцен (shot_02), "
        "слева направо, между ними тонкие белые вертикальные разделители.",
        "На каждой панели снизу метка FNNN — привязывай промт только по frame_uuid.",
        "Порядок слева → направо совпадает со списком ниже.",
        "",
        f"Верни ТОЛЬКО JSON apply-ops на все {n} shot_02 (без markdown):",
        '{"ops":[{"frame_uuid":"…","fields":{"промт_видео_2":"…"}}, …]}',
        f"Ровно {n} объектов в ops — по одному на каждый frame_uuid ниже.",
        "",
    ]
    for pos, it in enumerate(items, start=1):
        uuid = (getattr(it.frame, "uuid", None) or "").strip() or it.image_id
        parts.append(
            f"Позиция {pos} (слева→направо, shot_02) метка={panel_label_for_item(it)}"
        )
        parts.append(f"frame_uuid: {uuid}")
        parts.append(f"ID изображения: {it.image_id}")
        parts.append(f"Закадровый текст сцены: {it.voiceover}")
        parts.append("")
    return "\n".join(parts).strip()


def animation_prompt_shot2_in_plan_xlsx(project: Project, frame_number: int) -> str:
    if not _plan_xlsx_exists(project):
        return ""
    cells = read_plan_animation_prompt_shot2_cells(project, [frame_number])
    return (cells[0][1] if cells else "").strip()


def has_animation_prompt_shot2_for_frame(project: Project, frame: Frame) -> bool:
    if _plan_xlsx_exists(project):
        return (
            len(animation_prompt_shot2_in_plan_xlsx(project, frame.number))
            >= MIN_SHOT2_VIDEO_PROMPT_LEN
        )
    attrs = frame.attrs or {}
    return len((attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()) >= MIN_SHOT2_VIDEO_PROMPT_LEN


def frame_needs_shot2_video_prompt(project: Project, frame: Frame) -> bool:
    """Есть shot_02 на диске, но нет промта видео в plan R64."""
    xlsx_path = project.data_dir / "project.xlsx"
    by_num = read_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
    info = by_num.get(frame.number)
    if info is None or not info.has_shot2:
        return False
    if scene_shot2_image_path(project, frame.number) is None:
        return False
    return not has_animation_prompt_shot2_for_frame(project, frame)


def collect_shot2_batch_items(
    project: Project,
    frames: list[Frame],
) -> list[FrameImageBatchItem]:
    """Кадры shot_02 с PNG на диске и без промта видео (plan R64)."""
    out: list[FrameImageBatchItem] = []
    for fr in frames:
        if not frame_needs_shot2_video_prompt(project, fr):
            continue
        img = scene_shot2_image_path(project, fr.number)
        if img is None:
            continue
        vo = (fr.voiceover_text or "").strip() or voiceover_for_frame(project, fr)
        out.append(
            FrameImageBatchItem(
                frame=fr,
                image_path=img,
                image_id=image_id_for_shot2_frame(project, fr, img),
                voiceover=vo or "—",
            )
        )
    return out


def save_animation_prompt_shot2(
    frame: Frame,
    project: Project,
    text: str,
) -> bool:
    """R64 + attrs после ответа GPT для shot_02."""
    text = text.strip()
    if len(text) < MIN_SHOT2_VIDEO_PROMPT_LEN:
        return False
    attrs = dict(frame.attrs or {})
    attrs[SHOT2_VIDEO_PROMPT_ATTR] = text
    frame.attrs = attrs
    return write_plan_animation_prompt_shot2(project, frame.number, text)


def collect_batch_items(
    project: Project,
    frames: list[Frame],
) -> list[FrameImageBatchItem]:
    """Кадры с картинкой на диске и без animation_prompt (plan R48)."""
    img_index = index_scene_image_paths(project)
    out: list[FrameImageBatchItem] = []
    for fr in frames:
        if has_animation_prompt_for_frame(project, fr):
            continue
        img = img_index.get(fr.number)
        if img is None:
            continue
        vo = (fr.voiceover_text or "").strip() or voiceover_for_frame(project, fr)
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


def build_apply_ops_from_pairs(
    pairs: list[ParsedAnimationPair],
    frames: list[Frame],
    *,
    shot: int,
) -> list[dict]:
    """Пары GPT → apply-ops (DB SoT). shot=1 → промт_видео, shot=2 → промт_видео_2.

    Чистая функция (тестируемо); кадры без uuid пропускаются (backfill
    обязан их заполнить до шага).
    """
    field = "промт_видео" if shot == 1 else "промт_видео_2"
    by_number = {f.number: f for f in frames}
    ops: list[dict] = []
    for pair in pairs:
        if pair.frame_number is None:
            continue
        fr = by_number.get(pair.frame_number)
        if fr is None or not fr.uuid:
            continue
        text = pair.animation_text.strip()
        if len(text) < MIN_ANIM_PROMPT_LEN:
            continue
        ops.append({"frame_uuid": fr.uuid, "fields": {field: text}})
    return ops


def _loads_json_object(text: str) -> dict | None:
    """Достаёт первый JSON-object из ответа (в т.ч. из ```json fence)."""
    t = (text or "").strip()
    if not t:
        return None
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
        t = re.sub(r"\s*```\s*$", "", t)
    start = t.find("{")
    if start < 0:
        return None
    chunk = t[start:]
    try:
        data = json.loads(chunk)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        pass
    # Обрезанный хвост / лишний текст после JSON — вырезаем по скобкам.
    depth = 0
    end = -1
    for i, ch in enumerate(chunk):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end <= 0:
        return None
    try:
        data = json.loads(chunk[:end])
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError:
        return None


def _frame_from_uuid(frames: list[Frame], frame_uuid: str) -> Frame | None:
    uid = (frame_uuid or "").strip()
    if not uid:
        return None
    for fr in frames:
        if (fr.uuid or "").strip() == uid:
            return fr
    # Иногда модель пишет [ID: P54-F3-…] вместо чистого uuid.
    return _frame_from_image_id(frames, uid)


def parse_apply_ops_animation_reply(
    reply: str,
    frames: list[Frame],
) -> list[ParsedAnimationPair]:
    """Парсит ответ мастер-промта: JSON ``{"ops":[{"frame_uuid", "fields":{"промт_видео"}}]}``."""
    data = _loads_json_object(reply)
    if not data:
        return []
    ops = data.get("ops")
    if not isinstance(ops, list):
        return []
    results: list[ParsedAnimationPair] = []
    seen: set[int] = set()
    for op in ops:
        if not isinstance(op, dict):
            continue
        fr = _frame_from_uuid(frames, str(op.get("frame_uuid") or ""))
        if fr is None or fr.number in seen:
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        anim = ""
        for key, val in fields.items():
            if str(key) in _ANIM_FIELD_NAMES and isinstance(val, str):
                anim = val.strip()
                if anim:
                    break
        if not anim:
            for key in ("промт_видео", "промт_видео_2"):
                val = fields.get(key)
                if isinstance(val, str) and val.strip():
                    anim = val.strip()
                    break
        anim = _clean_animation_text(anim)
        if len(anim) < MIN_ANIM_PROMPT_LEN or is_apply_ops_blob(anim):
            continue
        seen.add(fr.number)
        results.append(
            ParsedAnimationPair(
                image_id=(fr.uuid or f"F{fr.number}"),
                animation_text=anim,
                frame_number=fr.number,
            )
        )
    return results


def unpack_animation_prompt_blobs(frames: list[Frame]) -> int:
    """Распаковывает сырой JSON apply-ops из animation_prompt в чистые промты.

    Старый парсер клал весь ответ GPT (часто на 6 кадров) в первый кадр пачки.
    Возвращает число кадров, которым выставили чистый промт.
    """
    by_uuid = { (fr.uuid or "").strip(): fr for fr in frames if (fr.uuid or "").strip() }
    collected: dict[str, str] = {}
    for fr in frames:
        raw = (fr.animation_prompt or "").strip()
        if not is_apply_ops_blob(raw):
            continue
        for pair in parse_apply_ops_animation_reply(raw, frames):
            if pair.frame_number is None:
                continue
            target = next((f for f in frames if f.number == pair.frame_number), None)
            if target is None or not (target.uuid or "").strip():
                continue
            uid = target.uuid.strip()
            existing = (target.animation_prompt or "").strip()
            # Уже чистый промт — не затираем распаковкой чужого blob.
            if (
                existing
                and not is_apply_ops_blob(existing)
                and len(existing) >= MIN_ANIM_PROMPT_LEN
            ):
                continue
            if uid in collected:
                continue
            collected[uid] = pair.animation_text

    n = 0
    for uid, text in collected.items():
        fr = by_uuid.get(uid)
        if fr is None:
            continue
        if (fr.animation_prompt or "").strip() == text:
            continue
        fr.animation_prompt = text
        if fr.status is not FrameStatus.animation_prompt_ready:
            fr.status = FrameStatus.animation_prompt_ready
        n += 1

    # Хвосты, где blob так и не разобрался — сбрасываем, чтобы GPT переделал.
    cleared = 0
    for fr in frames:
        if is_apply_ops_blob(fr.animation_prompt or ""):
            fr.animation_prompt = None
            cleared += 1
    if n or cleared:
        logger.info(
            "anim_pr: unpack apply-ops blobs → filled={}, cleared_bad={}",
            n,
            cleared,
        )
    return n


def parse_animation_reply(
    reply: str,
    frames: list[Frame],
    *,
    batch_items: list[FrameImageBatchItem],
) -> list[ParsedAnimationPair]:
    """Извлекает пары кадр / текст анимации из ответа GPT.

    Сначала JSON apply-ops (мастер-промт veo31), затем legacy
    «ID изображения» / «текст анимации».

    Позиционный zip «чанк N → кадр N» запрещён: он перепутывал промты
    между кадрами при сбое порядка/частичном JSON.
    """
    batch_nums = {it.frame.number for it in batch_items}

    json_pairs = parse_apply_ops_animation_reply(reply, frames)
    if json_pairs:
        # Только кадры этой пачки; чужие uuid игнорируем.
        return [p for p in json_pairs if p.frame_number in batch_nums]

    # Целый apply-ops blob без разбора — не раскладываем по позициям.
    if is_apply_ops_blob(reply or ""):
        return []

    results: list[ParsedAnimationPair] = []
    seen_frames: set[int] = set()
    batch_ids = {(it.image_id or "").strip() for it in batch_items}

    for m in _REPLY_BLOCK_RE.finditer(reply or ""):
        image_id = (m.group("id") or "").strip()
        anim = _clean_animation_text(m.group("text") or "")
        if len(anim) < 10 or is_apply_ops_blob(anim):
            continue
        fr = _frame_from_image_id(frames, image_id)
        if fr is None:
            continue
        if fr.number not in batch_nums or fr.number in seen_frames:
            continue
        # Без fuzzy «id in id»: только точное сопоставление через uuid/ID.
        if batch_ids and image_id not in batch_ids:
            # Допуск: модель вернула сырой uuid без обёртки [ID: …]
            uid = (fr.uuid or "").strip()
            if uid and uid not in image_id and image_id not in uid:
                continue
        seen_frames.add(fr.number)
        results.append(
            ParsedAnimationPair(
                image_id=image_id,
                animation_text=anim,
                frame_number=fr.number,
            )
        )

    return results
