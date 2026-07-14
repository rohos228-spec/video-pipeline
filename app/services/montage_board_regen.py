"""Перегенерация одного кадра/shot для панели монтажа (без HITL)."""

from __future__ import annotations

import uuid
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import OutseeBot
from app.generation_options import (
    ASPECT_RATIOS_BY_ID,
    DEFAULTS,
    IMAGE_GENERATORS_BY_ID,
    IMAGE_RESOLUTIONS_BY_ID,
    VIDEO_GENERATORS_BY_ID,
    VIDEO_RESOLUTIONS_BY_ID,
    build_gen_id_prefix,
    resolve_image_quality_slug,
)
from app.bots.chrome_cdp import fetch_cdp_version
from app.settings import settings
from app.models import Frame, Project
from app.orchestrator.steps.generate_images import _load_refs_for_frame
from app.services.animation_prompt_gpt import animation_prompt_shot2_in_plan_xlsx
from app.services.montage_board_assets import (
    finalize_scene_image,
    finalize_scene_video,
)
from app.services.montage_board_meta import (
    clear_stale_video,
    mark_stale_videos,
    store_correction,
    trim_key,
)
from app.services.outsee_retry import generate_image_with_retries, generate_video_with_retries
from app.services.plan_shot2 import (
    MIN_SHOT2_VIDEO_PROMPT_LEN,
    SHOT2_PROMPT_ATTR,
    SHOT2_VIDEO_PROMPT_ATTR,
    find_shot1_image,
    find_shot2_image,
)
from app.storage.plan_sheet_v8 import (
    read_plan_animation_prompt_cells,
    read_plan_image_prompt_cells,
    write_plan_animation_prompt,
    write_plan_animation_prompt_shot2,
    write_plan_image_prompt,
    write_plan_image_prompt_shot2,
)


async def _ensure_cdp_ready() -> None:
    try:
        await fetch_cdp_version(settings.browser_cdp_url)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Chrome CDP :29229 не отвечает — запустите Start-Chrome.cmd и откройте outsee.io"
        ) from exc


def _image_prompt_from_excel(project: Project, frame: Frame, shot: int) -> str:
    cells = read_plan_image_prompt_cells(project, [frame.number], shot=shot)
    excel_prompt = (cells[0][1] if cells else "").strip()
    if excel_prompt:
        return excel_prompt
    if shot == 2:
        attrs = dict(frame.attrs or {})
        return (attrs.get(SHOT2_PROMPT_ATTR) or "").strip()
    return (frame.image_prompt or "").strip()


def _video_prompt_from_excel(project: Project, frame: Frame, shot: int) -> str:
    if shot == 2:
        prompt = animation_prompt_shot2_in_plan_xlsx(project, frame.number)
        if len(prompt) < MIN_SHOT2_VIDEO_PROMPT_LEN:
            attrs = frame.attrs or {}
            prompt = (attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
        return prompt
    cells = read_plan_animation_prompt_cells(project, [frame.number])
    return (cells[0][1] if cells else "").strip() or (frame.animation_prompt or "").strip()


async def _frame_by_number(
    session: AsyncSession,
    project_id: int,
    frame_number: int,
) -> Frame | None:
    return (
        await session.execute(
            select(Frame).where(
                Frame.project_id == project_id,
                Frame.number == frame_number,
            )
        )
    ).scalar_one_or_none()


async def regen_scene_image(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    mode: str = "same_prompt",
    new_prompt: str | None = None,
    correction: str | None = None,
    board: dict | None = None,
) -> dict:
    """mode: same_prompt | edit_prompt | correction."""
    fr = await _frame_by_number(session, project.id, frame_number)
    if fr is None:
        raise RuntimeError(f"кадр {frame_number} не найден")

    scenes_dir = project.data_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    if mode == "edit_prompt":
        text = (new_prompt or "").strip()
        if not text:
            raise RuntimeError("пустой промт")
        if shot == 2:
            ok = write_plan_image_prompt_shot2(project, frame_number, text)
            attrs = dict(fr.attrs or {})
            attrs[SHOT2_PROMPT_ATTR] = text
            fr.attrs = attrs
        else:
            ok = write_plan_image_prompt(project, frame_number, text)
            fr.image_prompt = text
        if not ok:
            raise RuntimeError("не удалось записать промт в Excel")
        await session.flush()
        prompt_text = text
        refs: list[Path] = []
        if shot == 1:
            refs = await _load_refs_for_frame(session, project, frame_number)
    elif mode == "correction":
        text = (correction or "").strip()
        if not text:
            raise RuntimeError("пустая корректировка")
        if board is not None:
            store_correction(board, frame_number, shot, text)
        current = find_shot2_image(scenes_dir, frame_number) if shot == 2 else find_shot1_image(
            scenes_dir, frame_number
        )
        if current is None:
            raise RuntimeError("нет текущего изображения для корректировки")
        prompt_text = text
        refs = [current]
    else:
        prompt_text = _image_prompt_from_excel(project, fr, shot)
        if not prompt_text:
            row = "R46" if shot == 2 else "R45"
            raise RuntimeError(
                f"нет промта картинки в Excel (строка {row}, кадр {frame_number})"
            )
        if shot == 1:
            refs = await _load_refs_for_frame(session, project, frame_number)
        elif shot == 2:
            ref1 = find_shot1_image(scenes_dir, frame_number)
            refs = [ref1] if ref1 is not None else []
        else:
            refs = []

    short_uuid = uuid.uuid4().hex[:8]
    if shot == 2:
        file_path = scenes_dir / f"frame_{frame_number:03d}_s2_{short_uuid}.png"
        prompt_id_prefix = build_gen_id_prefix(project.id, frame_number, short_uuid) + "-S2"
    else:
        file_path = scenes_dir / f"frame_{frame_number:03d}_{short_uuid}.png"
        prompt_id_prefix = build_gen_id_prefix(project.id, frame_number, short_uuid)

    img_gen = IMAGE_GENERATORS_BY_ID.get(project.image_generator or DEFAULTS["image_generator"])
    ar = ASPECT_RATIOS_BY_ID.get(project.aspect_ratio or DEFAULTS["aspect_ratio"])
    ir = IMAGE_RESOLUTIONS_BY_ID.get(project.image_resolution or DEFAULTS["image_resolution"])
    aspect_slug = ar.outsee_slug if ar else "9:16"
    model_slug = img_gen.outsee_slug if img_gen else None
    res_slug = ir.outsee_slug if ir else None
    quality_slug = resolve_image_quality_slug(project.image_generator, project.image_quality)

    await _ensure_cdp_ready()
    logger.info(
        "montage regen image #{} frame {} shot {} → outsee ({} симв.)",
        project.id,
        frame_number,
        shot,
        len(prompt_text),
    )

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        gpt = ChatGPTBot(bs)
        result = await generate_image_with_retries(
            outsee,
            gpt,
            prompt=prompt_text,
            out_path=file_path,
            max_attempts_per_prompt=3,
            gpt_rewrite=True,
            aspect_ratio=aspect_slug,
            gen_id=uuid.uuid4().hex,
            model_slug=model_slug,
            resolution=res_slug,
            quality=quality_slug,
            relax=bool(project.image_relax),
            prompt_id_prefix=prompt_id_prefix,
            reference_image=refs if refs else None,
            project_id=project.id,
        )

    new_path = Path(result.file_path)
    await finalize_scene_image(
        session, project, frame_number, shot=shot, new_path=new_path
    )
    if board is not None:
        mark_stale_videos(board, frame_number, shot=shot)
    await session.flush()
    logger.info(
        "montage regen image #{} frame {} shot {} → {}",
        project.id,
        frame_number,
        shot,
        result.file_path,
    )
    return {
        "ok": True,
        "kind": "image",
        "frame_number": frame_number,
        "shot": shot,
        "path": str(result.file_path),
        "highlight": f"{frame_number}:image{shot}",
    }


async def regen_scene_video(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    mode: str = "same_prompt",
    new_prompt: str | None = None,
    board: dict | None = None,
) -> dict:
    fr = await _frame_by_number(session, project.id, frame_number)
    if fr is None:
        raise RuntimeError(f"кадр {frame_number} не найден")

    scenes_dir = project.data_dir / "scenes"
    videos_dir = project.data_dir / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)

    if mode == "edit_prompt":
        text = (new_prompt or "").strip()
        if not text:
            raise RuntimeError("пустой промт")
        if shot == 2:
            ok = write_plan_animation_prompt_shot2(project, frame_number, text)
            attrs = dict(fr.attrs or {})
            attrs[SHOT2_VIDEO_PROMPT_ATTR] = text
            fr.attrs = attrs
        else:
            ok = write_plan_animation_prompt(project, frame_number, text)
            fr.animation_prompt = text
        if not ok:
            raise RuntimeError("не удалось записать промт в Excel")
        await session.flush()
        prompt_text = text
    else:
        prompt_text = _video_prompt_from_excel(project, fr, shot)
        if not prompt_text:
            raise RuntimeError("нет промта анимации в Excel")

    if shot == 2:
        start_frame = find_shot2_image(scenes_dir, frame_number)
    else:
        start_frame = find_shot1_image(scenes_dir, frame_number)
    if start_frame is None:
        raise RuntimeError(f"нет стартового кадра для видео shot {shot} (папка scenes/)")

    short_uuid = uuid.uuid4().hex[:8]
    if shot == 2:
        file_path = videos_dir / f"clip_{frame_number:03d}_s2_{short_uuid}.mp4"
    else:
        file_path = videos_dir / f"clip_{frame_number:03d}_{short_uuid}.mp4"
    prompt_id_prefix = build_gen_id_prefix(project.id, frame_number, short_uuid)

    vg = VIDEO_GENERATORS_BY_ID.get(project.video_generator or DEFAULTS["video_generator"])
    vr_o = VIDEO_RESOLUTIONS_BY_ID.get(project.video_resolution or DEFAULTS["video_resolution"])
    ar = ASPECT_RATIOS_BY_ID.get(project.aspect_ratio or DEFAULTS["aspect_ratio"])
    video_model_slug = vg.outsee_slug if vg else None
    video_res_slug = vr_o.outsee_slug if vr_o else None
    aspect_slug = ar.outsee_slug if ar else "9:16"
    video_relax = project.video_relax is not False

    await _ensure_cdp_ready()
    logger.info(
        "montage regen video #{} frame {} shot {} → outsee ({} симв.)",
        project.id,
        frame_number,
        shot,
        len(prompt_text),
    )

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        gpt = ChatGPTBot(bs)
        result = await generate_video_with_retries(
            outsee,
            gpt,
            prompt=prompt_text,
            out_path=file_path,
            max_attempts_per_prompt=3,
            gpt_rewrite=True,
            project_id=project.id,
            start_frame=start_frame,
            aspect_ratio=aspect_slug,
            timeout=1200,
            model_slug=video_model_slug,
            resolution=video_res_slug,
            relax=video_relax,
            prompt_id_prefix=prompt_id_prefix,
            duplicate_check_paths=[],
        )

    new_path = Path(result.file_path)
    await finalize_scene_video(
        session, project, frame_number, shot=shot, new_path=new_path
    )
    if board is not None:
        clear_stale_video(board, frame_number, shot)
    await session.flush()
    logger.info(
        "montage regen video #{} frame {} shot {} → {}",
        project.id,
        frame_number,
        shot,
        result.file_path,
    )
    return {
        "ok": True,
        "kind": "video",
        "frame_number": frame_number,
        "shot": shot,
        "path": str(result.file_path),
        "highlight": trim_key(frame_number, shot),
    }
