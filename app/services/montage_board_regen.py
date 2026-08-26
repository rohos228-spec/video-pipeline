"""Перегенерация одного кадра/shot для панели монтажа (без HITL).

Генерация идёт тем же путём, что ноды img/video:
``generate_*_with_retries`` → Grsai / Outsee HTTP API (CDP — только fallback,
если API-провайдер выключен).

Промты: source of truth = БД (``prompt_versions`` активная → Frame.* →
Excel как запасной вид).
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
from app.models import Frame, Project, PromptVersion
from app.orchestrator.steps.generate_images import _load_refs_for_frame
from app.services.gpt_client import get_gpt_client
from app.services.montage_board_assets import (
    finalize_scene_image,
    finalize_scene_video,
    frame_shot_character_ids,
)
from app.services.montage_board_meta import (
    clear_stale_video,
    mark_stale_videos,
    store_correction,
    trim_key,
)
from app.services.outsee_retry import generate_image_with_retries, generate_video_with_retries
from app.services.vibecode_catalog import resolve_node_media_settings
from app.services.plan_shot2 import (
    MIN_SHOT2_VIDEO_PROMPT_LEN,
    SHOT2_PROMPT_ATTR,
    SHOT2_VIDEO_PROMPT_ATTR,
    find_shot1_image,
    find_shot2_image,
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


def _image_api_enabled() -> bool:
    """Montage image: любой HTTP-путь (grsai / outsee API). Chrome не используем."""
    from app.bots.grsai import grsai_enabled
    from app.bots.outsee_http import outsee_api_configured, outsee_api_enabled_for_image

    return bool(
        grsai_enabled() or outsee_api_enabled_for_image() or outsee_api_configured()
    )


def _video_api_enabled() -> bool:
    """Montage video: любой HTTP-путь. Chrome не используем."""
    from app.bots.grsai import grsai_video_enabled
    from app.bots.outsee_http import outsee_api_configured, outsee_api_enabled_for_video

    return bool(
        grsai_video_enabled()
        or outsee_api_enabled_for_video()
        or outsee_api_configured()
    )


class _ApiOnlyOutseeStub:
    """Заглушка OutseeBot: montage regen никогда не открывает Chrome CDP."""

    async def generate_image(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "montage regen: CDP отключён — нужен OUTSEE_API_KEY / GRSAI_API_KEY"
        )

    async def generate_video(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "montage regen: CDP отключён — нужен OUTSEE_API_KEY / GRSAI_API_KEY"
        )

    async def retry_image_download(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "montage regen: CDP download отключён — повтор скачивания только через HTTP API"
        )


def majority_scene_image_model(scenes_dir: Path) -> str | None:
    """Модель исходных кадров: самая частая в sidecar scenes/frame_*.json."""
    if not scenes_dir.is_dir():
        return None
    counts: Counter[str] = Counter()
    for path in scenes_dir.glob("frame_*.json"):
        if "_s2_" in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        model = str(data.get("model") or "").strip()
        if model:
            counts[model] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def image_prompt_from_excel(project: Project, frame: Frame, shot: int) -> str:
    """Legacy name: Excel больше не SoT — только Frame/attrs."""
    del project  # API compat
    if shot == 2:
        attrs = dict(frame.attrs or {})
        return (attrs.get(SHOT2_PROMPT_ATTR) or "").strip()
    return (frame.image_prompt or "").strip()


def video_prompt_from_excel(project: Project, frame: Frame, shot: int) -> str:
    """Legacy name: Excel больше не SoT — только Frame/attrs."""
    del project
    if shot == 2:
        attrs = dict(frame.attrs or {})
        return (attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
    return (frame.animation_prompt or "").strip()


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


async def _active_prompt_text(
    session: AsyncSession,
    frame_id: int,
    kind: str,
) -> str:
    """Активная версия из prompt_versions (DB v2), иначе пусто."""
    pv = (
        await session.execute(
            select(PromptVersion).where(
                PromptVersion.frame_id == frame_id,
                PromptVersion.kind == kind,
                PromptVersion.is_active.is_(True),
            )
        )
    ).scalars().first()
    return (pv.text or "").strip() if pv is not None else ""


async def resolve_image_prompt(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    shot: int,
) -> str:
    """БД first: prompt_versions → Frame/attrs → Excel."""
    if shot == 2:
        attrs = dict(frame.attrs or {})
        db = (attrs.get(SHOT2_PROMPT_ATTR) or "").strip()
        return db or image_prompt_from_excel(project, frame, shot)
    active = await _active_prompt_text(session, frame.id, "img")
    if active:
        return active
    db = (frame.image_prompt or "").strip()
    return db or image_prompt_from_excel(project, frame, shot)


async def resolve_video_prompt(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    shot: int,
) -> str:
    """БД first: prompt_versions → Frame/attrs → Excel."""
    if shot == 2:
        attrs = dict(frame.attrs or {})
        db = (attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
        if len(db) >= MIN_SHOT2_VIDEO_PROMPT_LEN:
            return db
        return video_prompt_from_excel(project, frame, shot)
    active = await _active_prompt_text(session, frame.id, "video")
    if active:
        return active
    db = (frame.animation_prompt or "").strip()
    return db or video_prompt_from_excel(project, frame, shot)


async def _persist_image_prompt(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    shot: int,
    text: str,
) -> None:
    """Запись промта только в БД (Excel — явный Export)."""
    from app.services import db_v2

    del project
    if shot == 2:
        attrs = dict(frame.attrs or {})
        attrs[SHOT2_PROMPT_ATTR] = text
        frame.attrs = attrs
    else:
        frame.image_prompt = text
        try:
            await db_v2.add_prompt_version(
                session, frame.project_id, frame.id, kind="img", text=text, set_active=True
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("montage regen: prompt_versions img write failed: {}", e)
    await session.flush()


async def _persist_video_prompt(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    shot: int,
    text: str,
) -> None:
    from app.services import db_v2

    del project
    if shot == 2:
        attrs = dict(frame.attrs or {})
        attrs[SHOT2_VIDEO_PROMPT_ATTR] = text
        frame.attrs = attrs
    else:
        frame.animation_prompt = text
        try:
            await db_v2.add_prompt_version(
                session, frame.project_id, frame.id, kind="video", text=text, set_active=True
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("montage regen: prompt_versions video write failed: {}", e)
            low = str(e).lower()
            if "database is locked" in low or "database is busy" in low:
                try:
                    await session.rollback()
                except Exception:  # noqa: BLE001
                    pass
                raise
    try:
        await session.flush()
    except Exception as e:  # noqa: BLE001
        low = str(e).lower()
        if "database is locked" in low or "database is busy" in low:
            try:
                await session.rollback()
            except Exception:  # noqa: BLE001
                pass
            raise
        raise


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
    pinned_prompt: str | None = None,
    ref_person_ids: list[str] | None = None,
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
        await _persist_image_prompt(session, project, fr, shot, text)
        prompt_text = text
        refs: list[Path] = []
        if shot == 1:
            if ref_person_ids is not None:
                override = ref_person_ids
            else:
                db_ids = frame_shot_character_ids(fr, 1)
                override = db_ids if db_ids else None
            refs = await _load_refs_for_frame(
                session,
                project,
                frame_number,
                persons_override=override,
            )
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
        # Только текст из модалки + текущий кадр как reference.
        # НЕ склеивать с Excel/БД — иначе в генератор уходит «другой» промт.
        prompt_text = text
        refs = [current]
    else:
        # same_prompt: pin с доски → БД (prompt_versions/Frame) → Excel.
        pinned = (pinned_prompt or "").strip()
        prompt_text = pinned or await resolve_image_prompt(session, project, fr, shot)
        if not prompt_text:
            raise RuntimeError(
                f"нет промта картинки в БД/Excel (кадр {frame_number}, shot {shot})"
            )
        if shot == 1:
            db_ids = frame_shot_character_ids(fr, 1)
            refs = await _load_refs_for_frame(
                session,
                project,
                frame_number,
                persons_override=db_ids if db_ids else None,
            )
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

    media = resolve_node_media_settings(project, node_type="images")
    img_gid = media["image_generator_id"]
    img_gen = IMAGE_GENERATORS_BY_ID.get(img_gid)
    orig_model = majority_scene_image_model(scenes_dir)
    model_slug = orig_model or (img_gen.outsee_slug if img_gen else None)
    if orig_model and orig_model != (img_gen.outsee_slug if img_gen else None):
        logger.info(
            "montage regen image #{}: модель исходных кадров {} "
            "(нода/проект был {})",
            project.id,
            orig_model,
            img_gen.outsee_slug if img_gen else img_gid,
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
        aspect_slug=media["aspect_slug"] or "9:16",
        model_slug=model_slug,
        res_slug=media["resolution_slug"],
        quality_slug=media["quality_slug"],
        image_relax=bool(project.image_relax),
    )


async def execute_image_regen(prep: ImageRegenPrep) -> Path:
    """Только HTTP API (Outsee/Grsai). Chrome CDP для монтажа отключён."""
    if not _image_api_enabled():
        raise RuntimeError(
            "montage regen image: нет HTTP API — задайте OUTSEE_API_KEY "
            "(IMAGE_PROVIDER=outsee) или GRSAI_API_KEY (IMAGE_PROVIDER=grsai). "
            "Chrome CDP больше не используется."
        )
    preview = (prep.prompt_text or "").replace("\n", " ")[:160]
    logger.info(
        "montage regen image #{} frame {} shot {} → API "
        "({} симв., refs={}, prefix={}, preview={!r})",
        prep.project_id,
        prep.frame_number,
        prep.shot,
        len(prep.prompt_text),
        len(prep.refs),
        prep.prompt_id_prefix,
        preview,
    )
    from app.services.llm_override import bind_generation_llm

    gpt = get_gpt_client()
    try:
        with bind_generation_llm(None, node_type="image_prompts"):
            result = await generate_image_with_retries(
                _ApiOnlyOutseeStub(),  # type: ignore[arg-type]
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
        if _ready_regen_file(prep.file_path):
            logger.warning(
                "montage regen image #{} frame {} shot {}: "
                "API failed but file ready: {}",
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
        await _persist_video_prompt(session, project, fr, shot, text)
        prompt_text = text
    else:
        prompt_text = await resolve_video_prompt(session, project, fr, shot)
        if not prompt_text:
            raise RuntimeError("нет промта анимации в БД/Excel")

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
    """Только HTTP API (Outsee/Grsai). Chrome CDP для монтажа отключён."""
    if not _video_api_enabled():
        raise RuntimeError(
            "montage regen video: нет HTTP API — задайте OUTSEE_API_KEY "
            "(VIDEO_PROVIDER=outsee) или GRSAI_API_KEY (VIDEO_PROVIDER=grsai). "
            "Chrome CDP больше не используется."
        )
    logger.info(
        "montage regen video #{} frame {} shot {} → API ({} симв.)",
        prep.project_id,
        prep.frame_number,
        prep.shot,
        len(prep.prompt_text),
    )
    from app.services.llm_override import bind_generation_llm

    gpt = get_gpt_client()
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
    with bind_generation_llm(None, node_type="image_prompts"):
        result = await generate_video_with_retries(
            _ApiOnlyOutseeStub(),  # type: ignore[arg-type]
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
