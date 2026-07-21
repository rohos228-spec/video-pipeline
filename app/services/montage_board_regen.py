"""Перегенерация одного кадра/shot для панели монтажа (без HITL)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
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
    clamp_image_resolution_id,
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


@dataclass
class ImageRegenPrep:
    project_id: int
    frame_number: int
    shot: int
    prompt_text: str
    file_path: Path
    refs: list[Path] = field(default_factory=list)
    prompt_id_prefix: str = ""
    gen_id: str = ""
    aspect_slug: str = "9:16"
    model_slug: str | None = None
    res_slug: str | None = None
    quality_slug: str | None = None
    image_relax: bool = False


@dataclass
class VideoRegenPrep:
    project_id: int
    frame_number: int
    shot: int
    prompt_text: str
    file_path: Path
    start_frame: Path
    prompt_id_prefix: str = ""
    aspect_slug: str = "9:16"
    video_model_slug: str | None = None
    video_res_slug: str | None = None
    video_relax: bool = True


async def _ensure_cdp_ready() -> None:
    try:
        await fetch_cdp_version(settings.browser_cdp_url)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Chrome CDP :29229 не отвечает — запустите Start-Chrome.cmd и откройте outsee.io"
        ) from exc


def image_prompt_from_excel(project: Project, frame: Frame, shot: int) -> str:
    """Промт исходного изображения: Excel R45/R46 → Frame/attrs."""
    cells = read_plan_image_prompt_cells(project, [frame.number], shot=shot)
    excel_prompt = (cells[0][1] if cells else "").strip()
    if excel_prompt:
        return excel_prompt
    if shot == 2:
        attrs = dict(frame.attrs or {})
        return (attrs.get(SHOT2_PROMPT_ATTR) or "").strip()
    return (frame.image_prompt or "").strip()


def video_prompt_from_excel(project: Project, frame: Frame, shot: int) -> str:
    """Промт исходного видео: Excel R48/R64 → Frame/attrs."""
    if shot == 2:
        prompt = animation_prompt_shot2_in_plan_xlsx(project, frame.number)
        if len(prompt) < MIN_SHOT2_VIDEO_PROMPT_LEN:
            attrs = frame.attrs or {}
            prompt = (attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
        return prompt
    cells = read_plan_animation_prompt_cells(project, [frame.number])
    return (cells[0][1] if cells else "").strip() or (frame.animation_prompt or "").strip()


# Совместимость со старыми импортами/вызовами внутри модуля.
_image_prompt_from_excel = image_prompt_from_excel
_video_prompt_from_excel = video_prompt_from_excel


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


async def prepare_image_regen(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    mode: str = "same_prompt",
    new_prompt: str | None = None,
    correction: str | None = None,
    board: dict | None = None,
) -> ImageRegenPrep:
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
        # Как img-шаг: полный image_prompt из Excel + инструкция правки,
        # текущий кадр — reference. Раньше слали только короткий correction
        # → Outsee держал Generate disabled / «промт не принят».
        base = _image_prompt_from_excel(project, fr, shot).strip()
        if base:
            prompt_text = (
                f"{base}\n\n"
                f"=== CORRECTION / ПРАВКА ===\n{text}"
            )
        else:
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

    # Как в generate_images: gen_id и prefix из одного uuid.
    gen_id = uuid.uuid4().hex
    short_uuid = gen_id[:8]
    if shot == 2:
        file_path = scenes_dir / f"frame_{frame_number:03d}_s2_{short_uuid}.png"
        prompt_id_prefix = build_gen_id_prefix(project.id, frame_number, short_uuid) + "-S2"
    else:
        file_path = scenes_dir / f"frame_{frame_number:03d}_{short_uuid}.png"
        prompt_id_prefix = build_gen_id_prefix(project.id, frame_number, short_uuid)

    img_gen = IMAGE_GENERATORS_BY_ID.get(project.image_generator or DEFAULTS["image_generator"])
    ar = ASPECT_RATIOS_BY_ID.get(project.aspect_ratio or DEFAULTS["aspect_ratio"])
    ir = IMAGE_RESOLUTIONS_BY_ID.get(
        clamp_image_resolution_id(
            project.image_generator, project.image_resolution
        )
    )

    return ImageRegenPrep(
        project_id=project.id,
        frame_number=frame_number,
        shot=shot,
        prompt_text=prompt_text,
        file_path=file_path,
        refs=refs,
        prompt_id_prefix=prompt_id_prefix,
        gen_id=gen_id,
        aspect_slug=ar.outsee_slug if ar else "9:16",
        model_slug=img_gen.outsee_slug if img_gen else None,
        res_slug=ir.outsee_slug if ir else None,
        quality_slug=resolve_image_quality_slug(project.image_generator, project.image_quality),
        image_relax=bool(project.image_relax),
    )


async def execute_image_regen(prep: ImageRegenPrep) -> Path:
    await _ensure_cdp_ready()
    logger.info(
        "montage regen image #{} frame {} shot {} → outsee ({} симв.)",
        prep.project_id,
        prep.frame_number,
        prep.shot,
        len(prep.prompt_text),
    )
    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        gpt = ChatGPTBot(bs)
        try:
            result = await generate_image_with_retries(
                outsee,
                gpt,
                prompt=prep.prompt_text,
                out_path=prep.file_path,
                max_attempts_per_prompt=3,
                gpt_rewrite=True,
                aspect_ratio=prep.aspect_slug,
                gen_id=prep.gen_id or uuid.uuid4().hex,
                model_slug=prep.model_slug,
                resolution=prep.res_slug,
                quality=prep.quality_slug,
                relax=prep.image_relax,
                prompt_id_prefix=prep.prompt_id_prefix,
                reference_image=prep.refs if prep.refs else None,
                project_id=prep.project_id,
            )
            return Path(result.file_path)
        except Exception as exc:  # noqa: BLE001
            # Generate уже оплачен — один раз ищем готовую карточку в истории.
            if prep.prompt_id_prefix and not _ready_regen_file(prep.file_path):
                recovered = await _recover_montage_image_from_history(
                    outsee, prep
                )
                if recovered is not None:
                    return recovered
            if _ready_regen_file(prep.file_path):
                logger.warning(
                    "montage regen image #{} frame {} shot {}: "
                    "execute failed but file ready: {}",
                    prep.project_id,
                    prep.frame_number,
                    prep.shot,
                    exc,
                )
                return prep.file_path
            raise


def _ready_regen_file(path: Path, *, min_bytes: int = 200_000) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


async def _recover_montage_image_from_history(
    outsee: OutseeBot,
    prep: ImageRegenPrep,
) -> Path | None:
    """После сбоя download: как regenerate_image — queue «Скачать», не cold ID-hunt.

    Cold cascade по новому [ID] бессмысленен, если Generate не принял промт —
    карточки с этим ID в галерее нет (см. лог «перебрал 1 картинок»).
    """
    from app.bots.outsee import (
        _download_via_queue_result,
        _image_page_url,
        _outsee_queue_mode,
        _validate_downloaded_image,
        download_saved_image_by_prompt_id,
    )
    from app.services.outsee_lane import outsee_lane

    prefix = prep.prompt_id_prefix
    if not prefix:
        return None
    try:
        async with outsee_lane(
            project_id=prep.project_id, op="montage_history_recover"
        ):
            page = await outsee.session.open_page(
                _image_page_url(prep.model_slug), reuse=True
            )
            # 1) Тот же путь, что после успешного Generate / regenerate_image.
            if _outsee_queue_mode():
                try:
                    await _download_via_queue_result(
                        page,
                        img_url="",
                        out_path=prep.file_path,
                        gen_id=prep.gen_id or prefix,
                        project_id=prep.project_id,
                    )
                    _validate_downloaded_image(
                        prep.file_path,
                        gen_id=prep.gen_id or prefix,
                        img_url="",
                    )
                    logger.info(
                        "montage history recover #{} F{} shot{} via queue Download",
                        prep.project_id,
                        prep.frame_number,
                        prep.shot,
                    )
                    return prep.file_path
                except Exception as qe:  # noqa: BLE001
                    logger.warning(
                        "montage history recover queue fail: {} — ID cascade",
                        type(qe).__name__,
                    )
            await download_saved_image_by_prompt_id(
                page,
                prompt_id_prefix=prefix,
                out_path=prep.file_path,
                project_id=prep.project_id,
                gen_id=prep.gen_id or prefix,
                model_slug=prep.model_slug,
            )
            logger.info(
                "montage history recover #{} frame {} shot {} → {} ({} B)",
                prep.project_id,
                prep.frame_number,
                prep.shot,
                prep.file_path,
                prep.file_path.stat().st_size,
            )
            return prep.file_path
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "montage history recover #{} frame {} shot {} failed: {}",
            prep.project_id,
            prep.frame_number,
            prep.shot,
            e,
        )
        return None


async def finalize_image_regen(
    session: AsyncSession,
    project: Project,
    prep: ImageRegenPrep,
    new_path: Path,
    *,
    board: dict | None = None,
) -> dict:
    await finalize_scene_image(
        session, project, prep.frame_number, shot=prep.shot, new_path=new_path
    )
    if board is not None:
        mark_stale_videos(board, prep.frame_number, shot=prep.shot)
    await session.flush()
    logger.info(
        "montage regen image #{} frame {} shot {} → {}",
        project.id,
        prep.frame_number,
        prep.shot,
        new_path,
    )
    return {
        "ok": True,
        "kind": "image",
        "frame_number": prep.frame_number,
        "shot": prep.shot,
        "path": str(new_path),
        "highlight": f"{prep.frame_number}:image{prep.shot}",
    }


async def prepare_video_regen(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    mode: str = "same_prompt",
    new_prompt: str | None = None,
    board: dict | None = None,
) -> VideoRegenPrep:
    del board
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
        prompt_id_prefix = build_gen_id_prefix(project.id, frame_number, short_uuid) + "-S2"
    else:
        file_path = videos_dir / f"clip_{frame_number:03d}_{short_uuid}.mp4"
        prompt_id_prefix = build_gen_id_prefix(project.id, frame_number, short_uuid)

    vg = VIDEO_GENERATORS_BY_ID.get(project.video_generator or DEFAULTS["video_generator"])
    vr_o = VIDEO_RESOLUTIONS_BY_ID.get(project.video_resolution or DEFAULTS["video_resolution"])
    ar = ASPECT_RATIOS_BY_ID.get(project.aspect_ratio or DEFAULTS["aspect_ratio"])

    return VideoRegenPrep(
        project_id=project.id,
        frame_number=frame_number,
        shot=shot,
        prompt_text=prompt_text,
        file_path=file_path,
        start_frame=start_frame,
        prompt_id_prefix=prompt_id_prefix,
        aspect_slug=ar.outsee_slug if ar else "9:16",
        video_model_slug=vg.outsee_slug if vg else None,
        video_res_slug=vr_o.outsee_slug if vr_o else None,
        video_relax=project.video_relax is not False,
    )


async def execute_video_regen(prep: VideoRegenPrep) -> Path:
    await _ensure_cdp_ready()
    logger.info(
        "montage regen video #{} frame {} shot {} → outsee ({} симв.)",
        prep.project_id,
        prep.frame_number,
        prep.shot,
        len(prep.prompt_text),
    )
    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        gpt = ChatGPTBot(bs)
        videos_dir = prep.file_path.parent
        if prep.shot == 2:
            dup_globs = list(videos_dir.glob(f"clip_{prep.frame_number:03d}_s2_*.mp4"))
        else:
            dup_globs = [
                p
                for p in videos_dir.glob(f"clip_{prep.frame_number:03d}_*.mp4")
                if "_s2_" not in p.name
            ]
        duplicate_check_paths = list(
            dict.fromkeys(p.resolve() for p in dup_globs if p.is_file())
        )
        result = await generate_video_with_retries(
            outsee,
            gpt,
            prompt=prep.prompt_text,
            out_path=prep.file_path,
            max_attempts_per_prompt=3,
            gpt_rewrite=True,
            project_id=prep.project_id,
            start_frame=prep.start_frame,
            aspect_ratio=prep.aspect_slug,
            timeout=1200,
            model_slug=prep.video_model_slug,
            resolution=prep.video_res_slug,
            relax=prep.video_relax,
            prompt_id_prefix=prep.prompt_id_prefix,
            duplicate_check_paths=duplicate_check_paths,
        )
    return Path(result.file_path)


async def finalize_video_regen(
    session: AsyncSession,
    project: Project,
    prep: VideoRegenPrep,
    new_path: Path,
    *,
    board: dict | None = None,
) -> dict:
    await finalize_scene_video(
        session, project, prep.frame_number, shot=prep.shot, new_path=new_path
    )
    if board is not None:
        clear_stale_video(board, prep.frame_number, prep.shot)
    await session.flush()
    logger.info(
        "montage regen video #{} frame {} shot {} → {}",
        project.id,
        prep.frame_number,
        prep.shot,
        new_path,
    )
    return {
        "ok": True,
        "kind": "video",
        "frame_number": prep.frame_number,
        "shot": prep.shot,
        "path": str(new_path),
        "highlight": trim_key(prep.frame_number, prep.shot),
    }


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
    prep = await prepare_image_regen(
        session,
        project,
        frame_number,
        shot=shot,
        mode=mode,
        new_prompt=new_prompt,
        correction=correction,
        board=board,
    )
    new_path = await execute_image_regen(prep)
    return await finalize_image_regen(session, project, prep, new_path, board=board)


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
    prep = await prepare_video_regen(
        session,
        project,
        frame_number,
        shot=shot,
        mode=mode,
        new_prompt=new_prompt,
        board=board,
    )
    new_path = await execute_video_regen(prep)
    return await finalize_video_regen(session, project, prep, new_path, board=board)
