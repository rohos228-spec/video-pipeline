"""Формирование виртуальной Excel-сетки напрямую из SQLite БД (без чтения/записи файла на диске).

Используется для мгновенного отображения табличного вида в веб-студии (вкладка «База» -> «Зеркало Excel»).
"""

from __future__ import annotations

from typing import Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, Frame, FrameText, Project, PromptVersion
from app.services.xlsx_v8_import import (
    ROW_DURATION_V8,
    ROW_IMAGE_PROMPT_2_V8,
    ROW_IMAGE_PROMPT_V8,
    ROW_TIMECODE_V8,
    ROW_VIDEO_PROMPT_2_V8,
    ROW_VIDEO_PROMPT_V8,
    ROW_VOICEOVER_V8,
    SHEET_GENERAL_V8,
    SHEET_PLAN_V8,
)

ROW_LABELS_PLAN: dict[int, str] = {
    1: "Номер кадра",
    2: "ID сцены / shot",
    4: "Место",
    5: "Главное действие",
    6: "Акцент",
    7: "Смысл сцены",
    8: "Персонажи",
    10: "Тип сцены",
    11: "Особенность сцены",
    12: "Кластер",
    ROW_TIMECODE_V8: "Таймкод",
    ROW_IMAGE_PROMPT_V8: "Промпт для картинки 1",
    ROW_IMAGE_PROMPT_2_V8: "Промпт для картинки 2",
    ROW_VIDEO_PROMPT_V8: "Промпт для видео",
    ROW_VOICEOVER_V8: "Закадровый текст",
    ROW_DURATION_V8: "Время на кадр (сек)",
    ROW_VIDEO_PROMPT_2_V8: "Промпт для видео 2",
}


def _str_val(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


async def build_virtual_project_sheets(
    session: AsyncSession, project: Project
) -> dict[str, list[list[str]]]:
    """Генерирует словарь матриц {sheet_name: rows} напрямую из БД SQLite."""
    # 1. Загружаем кадры
    stmt = (
        select(Frame)
        .where(Frame.project_id == project.id)
        .order_by(Frame.number)
    )
    frames = list((await session.execute(stmt)).scalars().all())

    # 2. Загружаем активные промпты и тексты
    frame_ids = [f.id for f in frames]
    active_prompts: dict[int, dict[str, str]] = {}
    if frame_ids:
        p_stmt = select(PromptVersion).where(
            PromptVersion.frame_id.in_(frame_ids),
            PromptVersion.is_active.is_(True),
        )
        for p in (await session.execute(p_stmt)).scalars().all():
            if p.frame_id not in active_prompts:
                active_prompts[p.frame_id] = {}
            active_prompts[p.frame_id][p.kind] = p.text

    voiceovers: dict[int, str] = {}
    if frame_ids:
        t_stmt = select(FrameText).where(
            FrameText.frame_id.in_(frame_ids),
            FrameText.kind == "voiceover",
        )
        for t in (await session.execute(t_stmt)).scalars().all():
            voiceovers[t.frame_id] = t.text

    # 3. Загружаем сущности (персонажи, фоны, предметы)
    stmt_entities = (
        select(Entity)
        .where(Entity.project_id == project.id)
        .order_by(Entity.id)
    )
    entities = list((await session.execute(stmt_entities)).scalars().all())

    sheets: dict[str, list[list[str]]] = {}

    # === Лист «план» ===
    total_cols = max(3, len(frames) + 2)
    max_row_index = max(max(ROW_LABELS_PLAN.keys()), 55)
    plan_matrix: list[list[str]] = [["" for _ in range(total_cols)] for _ in range(max_row_index)]

    for r_idx, label in ROW_LABELS_PLAN.items():
        if 1 <= r_idx <= len(plan_matrix):
            plan_matrix[r_idx - 1][0] = label

    for i, frame in enumerate(frames):
        col_idx = 2 + i

        plan_matrix[0][col_idx] = str(frame.number)

        shot_id = f"shot_{frame.number:02d}"
        if frame.uuid:
            shot_id = f"{shot_id} ({frame.uuid[:8]})"
        plan_matrix[1][col_idx] = shot_id

        attrs = frame.attrs if isinstance(frame.attrs, dict) else {}
        plan_matrix[3][col_idx] = _str_val(attrs.get("place"))
        plan_matrix[4][col_idx] = _str_val(attrs.get("main_action"))
        plan_matrix[5][col_idx] = _str_val(attrs.get("accent"))
        plan_matrix[6][col_idx] = _str_val(attrs.get("scene_sense"))
        plan_matrix[7][col_idx] = _str_val(attrs.get("characters"))
        plan_matrix[9][col_idx] = _str_val(attrs.get("visual_type"))
        plan_matrix[10][col_idx] = _str_val(attrs.get("scene_feature"))
        plan_matrix[11][col_idx] = _str_val(attrs.get("cluster"))

        timecode = _str_val(attrs.get("timecode"))
        if not timecode and frame.start_ts is not None and frame.end_ts is not None:
            timecode = f"{frame.start_ts:.2f} - {frame.end_ts:.2f}"
        plan_matrix[ROW_TIMECODE_V8 - 1][col_idx] = timecode

        # Промпт картинки 1 (R45)
        img_prompt = frame.image_prompt or active_prompts.get(frame.id, {}).get("img", "")
        plan_matrix[ROW_IMAGE_PROMPT_V8 - 1][col_idx] = _str_val(img_prompt)

        # Промпт картинки 2 (R46)
        plan_matrix[ROW_IMAGE_PROMPT_2_V8 - 1][col_idx] = _str_val(attrs.get("image_prompt_2"))

        # Промпт видео (R48)
        vid_prompt = frame.animation_prompt or active_prompts.get(frame.id, {}).get("video", "")
        plan_matrix[ROW_VIDEO_PROMPT_V8 - 1][col_idx] = _str_val(vid_prompt)

        # Закадровый текст (R49)
        voiceover = frame.voiceover_text or voiceovers.get(frame.id, "")
        plan_matrix[ROW_VOICEOVER_V8 - 1][col_idx] = _str_val(voiceover)

        dur_str = f"{frame.duration_seconds:.2f}" if frame.duration_seconds is not None else ""
        plan_matrix[ROW_DURATION_V8 - 1][col_idx] = dur_str

        if len(plan_matrix) >= ROW_VIDEO_PROMPT_2_V8:
            plan_matrix[ROW_VIDEO_PROMPT_2_V8 - 1][col_idx] = _str_val(attrs.get("video_prompt_2"))

    sheets[SHEET_PLAN_V8] = plan_matrix

    # === Лист «Общий план» ===
    meta = project.meta if isinstance(project.meta, dict) else {}
    general_plan_text = _str_val(project.general_plan or meta.get("general_plan") or "")
    general_sheet_rows = [
        ["Параметр", "Значение"],
        ["Тема ролика", _str_val(project.topic or project.title)],
        ["Сценарий / Концепт", general_plan_text],
        ["Статус проекта", _str_val(project.status.value if project.status else "")],
        ["Всего кадров", str(len(frames))],
    ]
    sheets[SHEET_GENERAL_V8] = general_sheet_rows

    # === Лист «Персонажи» ===
    chars = [e for e in entities if e.type in ("character", "hero")]
    char_rows = [["Код", "Имя / Название", "Описание"]]
    for c in chars:
        desc = (c.attrs or {}).get("description") or c.name or ""
        char_rows.append([_str_val(c.code or f"c{c.id}"), _str_val(c.name), _str_val(desc)])
    if len(char_rows) == 1:
        char_rows.append(["c01", "Герой 1", ""])
    sheets["Персонажи"] = char_rows

    # === Лист «Фоны» ===
    bgs = [e for e in entities if e.type in ("background", "bg")]
    bg_rows = [["Код", "Локация / Окружение", "Описание"]]
    for b in bgs:
        desc = (b.attrs or {}).get("description") or b.name or ""
        bg_rows.append([_str_val(b.code or f"bg{b.id}"), _str_val(b.name), _str_val(desc)])
    if len(bg_rows) == 1:
        bg_rows.append(["bg01", "Локация 1", ""])
    sheets["Фоны"] = bg_rows

    # === Лист «Предметы» ===
    props = [e for e in entities if e.type in ("prop", "item")]
    prop_rows = [["Код", "Предмет", "Описание"]]
    for pr in props:
        desc = (pr.attrs or {}).get("description") or pr.name or ""
        prop_rows.append([_str_val(pr.code or f"p{pr.id}"), _str_val(pr.name), _str_val(desc)])
    if len(prop_rows) == 1:
        prop_rows.append(["p01", "Предмет 1", ""])
    sheets["Предметы"] = prop_rows

    return sheets