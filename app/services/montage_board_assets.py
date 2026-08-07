"""Файловые операции панели монтажа: upload / delete / archive / swap shots."""

from __future__ import annotations

import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Artifact, ArtifactKind, Frame, Project
from app.services.plan_shot2 import (
    SHOT2_PROMPT_ATTR,
    SHOT2_VIDEO_PROMPT_ATTR,
    effective_shot_from_artifact,
    find_shot1_image,
    find_shot2_image,
    shot2_file_pattern,
    shot2_video_file_pattern,
)


def _archive_dir(project: Project, sub: str) -> Path:
    d = project.data_dir / "old" / sub
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_file_busy_error(exc: BaseException) -> bool:
    """WinError 32 / errno busy — файл открыт превью Studio, Defender и т.п."""
    winerr = getattr(exc, "winerror", None)
    if winerr == 32:
        return True
    if getattr(exc, "errno", None) in {13, 16, 11}:  # EACCES / EBUSY / EAGAIN
        return True
    msg = str(exc).lower()
    return "winerror 32" in msg or "занят другим процессом" in msg or "being used by another" in msg


def _sidecar_companions(path: Path) -> list[Path]:
    """Соседние meta-файлы того же stem (``clip_001_ab.json`` рядом с ``.mp4``)."""
    out: list[Path] = []
    for ext in (".json",):
        side = path.with_suffix(ext)
        if side.is_file() and side.resolve() != path.resolve():
            out.append(side)
    return out


def archive_file(
    path: Path,
    project: Project,
    sub: str,
    *,
    with_sidecars: bool = True,
) -> Path | None:
    """Перенос в ``old/<sub>/``. При блокировке файла — retry, затем copy+unlink.

    После успеха уводит sidecar ``.json`` с тем же stem — иначе старые meta
    остаются рядом с новым ``clip_NNN_<hash>.mp4``.
    """
    if not path.is_file():
        return None
    sidecars = _sidecar_companions(path) if with_sidecars else []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = _archive_dir(project, sub) / f"{stamp}_{path.name}"
    last_err: OSError | None = None
    moved = False
    for attempt in range(8):
        try:
            shutil.move(str(path), str(dest))
            logger.info("montage: archived {} → {}", path.name, dest)
            moved = True
            break
        except OSError as exc:
            last_err = exc
            if not _is_file_busy_error(exc):
                raise
            time.sleep(0.12 * (attempt + 1))
    if not moved:
        # Fallback: копия в old/, unlink исходника (если всё ещё locked — оставляем оба).
        try:
            shutil.copy2(str(path), str(dest))
        except OSError:
            if last_err is not None:
                raise last_err
            raise
        try:
            path.unlink()
            logger.info("montage: archived {} → {} (copy+unlink)", path.name, dest)
            moved = True
        except OSError as unlink_exc:
            logger.warning(
                "montage: archived copy {} → {} (source still locked: {})",
                path.name,
                dest,
                unlink_exc,
            )
    if with_sidecars:
        for side in sidecars:
            archive_file(side, project, sub, with_sidecars=False)
        # json мог появиться/остаться по тому же stem после move
        for side in _sidecar_companions(path):
            archive_file(side, project, sub, with_sidecars=False)
    return dest


def _is_shot1_media_name(name: str) -> bool:
    return "_s2_" not in name


def purge_replaced_media(
    folder: Path,
    *,
    patterns: list[str],
    keep: Path,
    project: Project,
    sub: str,
    shot: int,
    also_json: bool = True,
) -> int:
    """Убрать из рабочей папки всё кроме ``keep`` (и его stem.json).

    Гарантирует: после regen старый ``clip_NNN_<oldhash>.mp4`` не лежит рядом
    с новым — уходит в ``old/`` вместе с sidecar json.
    """
    if not folder.is_dir():
        return 0
    keep_res = keep.resolve()
    keep_stem = keep.stem
    n = 0
    seen: set[Path] = set()
    globs = list(patterns)
    if also_json:
        for pat in list(patterns):
            if pat.endswith(".mp4"):
                globs.append(pat[:-4] + ".json")
            elif pat.endswith(".png"):
                globs.append(pat[:-4] + ".json")
    for g in globs:
        for p in list(folder.glob(g)):
            try:
                res = p.resolve()
            except OSError:
                continue
            if res in seen:
                continue
            seen.add(res)
            if shot == 1 and not _is_shot1_media_name(p.name):
                continue
            if res == keep_res:
                continue
            if p.suffix.lower() == ".json" and p.stem == keep_stem:
                continue
            archive_file(p, project, sub)
            n += 1
            if p.is_file():
                logger.error(
                    "montage: orphan still present after archive (locked?): {}",
                    p,
                )
    return n


async def _frame(session: AsyncSession, project_id: int, frame_number: int) -> Frame | None:
    return (
        await session.execute(
            select(Frame).where(
                Frame.project_id == project_id,
                Frame.number == frame_number,
            )
        )
    ).scalar_one_or_none()


def _assert_new_file_ready(path: Path, *, min_bytes: int = 64) -> None:
    if not path.is_file():
        raise RuntimeError(f"новый файл не создан: {path.name}")
    if path.stat().st_size < min_bytes:
        raise RuntimeError(f"новый файл пустой или слишком мал: {path.name}")


async def finalize_scene_image(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    new_path: Path,
) -> None:
    """После успешной генерации/upload: архив старых файлов, artifact на new_path."""
    _assert_new_file_ready(new_path)
    scenes = project.data_dir / "scenes"
    if shot == 2:
        patterns = [shot2_file_pattern(frame_number)]
    else:
        patterns = [f"frame_{frame_number:03d}_*.png"]
    purged = purge_replaced_media(
        scenes,
        patterns=patterns,
        keep=new_path,
        project=project,
        sub="scenes",
        shot=shot,
    )
    if purged:
        logger.info(
            "montage: finalize image #{} F{} s{} purged {} old file(s)",
            project.id,
            frame_number,
            shot,
            purged,
        )
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_image,
                )
            )
        ).scalars().all()
        for art in arts:
            meta_shot = (art.meta or {}).get("shot", 1)
            if (shot == 2 and meta_shot == 2) or (shot == 1 and meta_shot != 2):
                await session.delete(art)
        session.add(
            Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_image,
                uuid=uuid.uuid4().hex,
                path=str(new_path),
                meta={"shot": shot},
            )
        )
    await session.flush()


async def finalize_scene_video(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    new_path: Path,
) -> None:
    """После успешной генерации/upload: архив старых клипов, artifact на new_path."""
    _assert_new_file_ready(new_path, min_bytes=1024)
    videos = project.data_dir / "videos"
    if shot == 2:
        patterns = [shot2_video_file_pattern(frame_number)]
    else:
        patterns = [f"clip_{frame_number:03d}_*.mp4"]
    purged = purge_replaced_media(
        videos,
        patterns=patterns,
        keep=new_path,
        project=project,
        sub="videos",
        shot=shot,
    )
    if purged:
        logger.info(
            "montage: finalize video #{} F{} s{} purged {} old file(s)",
            project.id,
            frame_number,
            shot,
            purged,
        )
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
            )
        ).scalars().all()
        for art in arts:
            if effective_shot_from_artifact(art.meta, art.path or "") == shot:
                await session.delete(art)
        session.add(
            Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_video,
                uuid=uuid.uuid4().hex,
                path=str(new_path),
                meta={"shot": shot},
            )
        )
    await session.flush()


async def delete_scene_image(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
) -> bool:
    scenes = project.data_dir / "scenes"
    if shot == 2:
        pattern = shot2_file_pattern(frame_number)
    else:
        pattern = f"frame_{frame_number:03d}_*.png"
    deleted = False
    if scenes.is_dir():
        for p in list(scenes.glob(pattern)):
            if shot == 1 and "_s2_" in p.name:
                continue
            archive_file(p, project, "scenes")
            deleted = True
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_image,
                )
            )
        ).scalars().all()
        for art in arts:
            meta_shot = (art.meta or {}).get("shot", 1)
            if (shot == 2 and meta_shot == 2) or (shot == 1 and meta_shot != 2):
                await session.delete(art)
                deleted = True
    await session.flush()
    return deleted


async def delete_scene_video(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
) -> bool:
    videos = project.data_dir / "videos"
    if shot == 2:
        globs = [shot2_video_file_pattern(frame_number)]
    else:
        globs = [f"clip_{frame_number:03d}_*.mp4"]
    deleted = False
    if videos.is_dir():
        for g in globs:
            for p in list(videos.glob(g)):
                if shot == 1 and "_s2_" in p.name:
                    continue
                archive_file(p, project, "videos")
                deleted = True
    fr = await _frame(session, project.id, frame_number)
    if fr is not None:
        arts = (
            await session.execute(
                select(Artifact).where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
            )
        ).scalars().all()
        for art in arts:
            if effective_shot_from_artifact(art.meta, art.path or "") == shot:
                await session.delete(art)
                deleted = True
    await session.flush()
    return deleted


async def save_scene_image_upload(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    content: bytes,
    suffix: str,
) -> Path:
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    short = uuid.uuid4().hex[:8]
    if shot == 2:
        name = f"frame_{frame_number:03d}_s2_{short}{suffix}"
    else:
        name = f"frame_{frame_number:03d}_{short}{suffix}"
    dest = scenes / name
    dest.write_bytes(content)
    await finalize_scene_image(session, project, frame_number, shot=shot, new_path=dest)
    return dest


async def save_scene_video_upload(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    shot: int,
    content: bytes,
    suffix: str,
) -> Path:
    videos = project.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    short = uuid.uuid4().hex[:8]
    if shot == 2:
        name = f"clip_{frame_number:03d}_s2_{short}{suffix}"
    else:
        name = f"clip_{frame_number:03d}_{short}{suffix}"
    dest = videos / name
    dest.write_bytes(content)
    await finalize_scene_video(session, project, frame_number, shot=shot, new_path=dest)
    return dest


def _find_shot1_video(videos_dir: Path, frame_number: int) -> Path | None:
    if not videos_dir.is_dir():
        return None
    candidates = [
        p
        for p in videos_dir.glob(f"clip_{frame_number:03d}_*.mp4")
        if "_s2_" not in p.name
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _find_shot2_video(videos_dir: Path, frame_number: int) -> Path | None:
    if not videos_dir.is_dir():
        return None
    candidates = list(videos_dir.glob(shot2_video_file_pattern(frame_number)))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _swap_prompt_fields(fr: Frame) -> dict[str, str]:
    """Меняет местами промты shot1 ↔ shot2 на Frame."""
    attrs = dict(fr.attrs or {})
    img1 = (fr.image_prompt or "").strip()
    img2 = (attrs.get(SHOT2_PROMPT_ATTR) or "").strip()
    vid1 = (fr.animation_prompt or "").strip()
    vid2 = (attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
    fr.image_prompt = img2
    fr.animation_prompt = vid2
    if img1:
        attrs[SHOT2_PROMPT_ATTR] = img1
    else:
        attrs.pop(SHOT2_PROMPT_ATTR, None)
    if vid1:
        attrs[SHOT2_VIDEO_PROMPT_ATTR] = vid1
    else:
        attrs.pop(SHOT2_VIDEO_PROMPT_ATTR, None)
    fr.attrs = attrs
    flag_modified(fr, "attrs")
    return {
        "image_prompt_shot1": fr.image_prompt or "",
        "image_prompt_shot2": img1,
        "animation_prompt_shot1": fr.animation_prompt or "",
        "animation_prompt_shot2": vid1,
    }


def _swap_trim_keys(board_meta: dict[str, Any], frame_number: int) -> None:
    from app.services.montage_board_meta import trim_key

    trims = board_meta.get("video_trims")
    if not isinstance(trims, dict):
        return
    k1, k2 = trim_key(frame_number, 1), trim_key(frame_number, 2)
    t1, t2 = trims.get(k1), trims.get(k2)
    if t1 is None and t2 is None:
        return
    if t2 is None:
        trims.pop(k1, None)
    else:
        trims[k1] = t2
    if t1 is None:
        trims.pop(k2, None)
    else:
        trims[k2] = t1
    board_meta["video_trims"] = trims


async def swap_shot_media(
    session: AsyncSession,
    project: Project,
    frame_number: int,
    *,
    kind: Literal["image", "video", "both"] = "both",
) -> dict[str, Any]:
    """Поменять местами shot1 ↔ shot2 (файлы + промты + trim)."""
    result: dict[str, Any] = {
        "frame_number": frame_number,
        "kind": kind,
        "images_swapped": False,
        "videos_swapped": False,
        "prompts_swapped": False,
    }
    do_img = kind in ("image", "both")
    do_vid = kind in ("video", "both")

    if do_img:
        scenes = project.data_dir / "scenes"
        scenes.mkdir(parents=True, exist_ok=True)
        src1 = find_shot1_image(scenes, frame_number)
        src2 = find_shot2_image(scenes, frame_number)
        bytes1 = src1.read_bytes() if src1 is not None and src1.is_file() else None
        bytes2 = src2.read_bytes() if src2 is not None and src2.is_file() else None
        suf1 = src1.suffix if src1 is not None else ".png"
        suf2 = src2.suffix if src2 is not None else ".png"
        if bytes1 is not None or bytes2 is not None:
            # Архив старых + запись с новыми именами (чтобы не пересечься).
            await delete_scene_image(session, project, frame_number, shot=1)
            await delete_scene_image(session, project, frame_number, shot=2)
            if bytes2 is not None:
                await save_scene_image_upload(
                    session,
                    project,
                    frame_number,
                    shot=1,
                    content=bytes2,
                    suffix=suf2 or ".png",
                )
            if bytes1 is not None:
                await save_scene_image_upload(
                    session,
                    project,
                    frame_number,
                    shot=2,
                    content=bytes1,
                    suffix=suf1 or ".png",
                )
            result["images_swapped"] = True

    if do_vid:
        videos = project.data_dir / "videos"
        videos.mkdir(parents=True, exist_ok=True)
        src1 = _find_shot1_video(videos, frame_number)
        src2 = _find_shot2_video(videos, frame_number)
        bytes1 = src1.read_bytes() if src1 is not None and src1.is_file() else None
        bytes2 = src2.read_bytes() if src2 is not None and src2.is_file() else None
        suf1 = src1.suffix if src1 is not None else ".mp4"
        suf2 = src2.suffix if src2 is not None else ".mp4"
        if bytes1 is not None or bytes2 is not None:
            await delete_scene_video(session, project, frame_number, shot=1)
            await delete_scene_video(session, project, frame_number, shot=2)
            if bytes2 is not None:
                await save_scene_video_upload(
                    session,
                    project,
                    frame_number,
                    shot=1,
                    content=bytes2,
                    suffix=suf2 or ".mp4",
                )
            if bytes1 is not None:
                await save_scene_video_upload(
                    session,
                    project,
                    frame_number,
                    shot=2,
                    content=bytes1,
                    suffix=suf1 or ".mp4",
                )
            result["videos_swapped"] = True

    fr = await _frame(session, project.id, frame_number)
    if fr is not None and (do_img or do_vid):
        # Промты меняем вместе с соответствующим kind; при both — оба.
        if kind == "both":
            result["prompts"] = _swap_prompt_fields(fr)
            result["prompts_swapped"] = True
        elif kind == "image":
            attrs = dict(fr.attrs or {})
            img1 = (fr.image_prompt or "").strip()
            img2 = (attrs.get(SHOT2_PROMPT_ATTR) or "").strip()
            fr.image_prompt = img2
            if img1:
                attrs[SHOT2_PROMPT_ATTR] = img1
            else:
                attrs.pop(SHOT2_PROMPT_ATTR, None)
            fr.attrs = attrs
            flag_modified(fr, "attrs")
            result["prompts_swapped"] = True
        elif kind == "video":
            attrs = dict(fr.attrs or {})
            vid1 = (fr.animation_prompt or "").strip()
            vid2 = (attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
            fr.animation_prompt = vid2
            if vid1:
                attrs[SHOT2_VIDEO_PROMPT_ATTR] = vid1
            else:
                attrs.pop(SHOT2_VIDEO_PROMPT_ATTR, None)
            fr.attrs = attrs
            flag_modified(fr, "attrs")
            result["prompts_swapped"] = True

    if do_vid:
        from app.services.montage_board_meta import montage_meta, set_montage_meta

        board = montage_meta(project)
        _swap_trim_keys(board, frame_number)
        # Оба слота stale после swap — картинка могла не совпадать с клипом.
        stale = list(board.get("stale_videos") or [])
        for shot in (1, 2):
            key = f"{frame_number}:{shot}"
            if key not in stale:
                stale.append(key)
        board["stale_videos"] = stale
        set_montage_meta(project, board)

    if do_img and not do_vid:
        from app.services.montage_board_meta import mark_stale_videos, montage_meta, set_montage_meta

        board = montage_meta(project)
        mark_stale_videos(board, frame_number, shot=1)
        mark_stale_videos(board, frame_number, shot=2)
        set_montage_meta(project, board)

    await session.flush()
    if not result["images_swapped"] and not result["videos_swapped"] and not result["prompts_swapped"]:
        result["ok"] = False
        result["reason"] = "нечего менять"
    else:
        result["ok"] = True
    return result


def _find_shot_image(scenes_dir: Path, frame_number: int, shot: int) -> Path | None:
    if shot == 2:
        return find_shot2_image(scenes_dir, frame_number)
    return find_shot1_image(scenes_dir, frame_number)


def _find_shot_video(videos_dir: Path, frame_number: int, shot: int) -> Path | None:
    if shot == 2:
        return _find_shot2_video(videos_dir, frame_number)
    return _find_shot1_video(videos_dir, frame_number)


def _get_image_prompt(fr: Frame, shot: int) -> str:
    if shot == 2:
        return str((fr.attrs or {}).get(SHOT2_PROMPT_ATTR) or "").strip()
    return (fr.image_prompt or "").strip()


def _set_image_prompt(fr: Frame, shot: int, value: str) -> None:
    text = (value or "").strip()
    if shot == 2:
        attrs = dict(fr.attrs or {})
        if text:
            attrs[SHOT2_PROMPT_ATTR] = text
        else:
            attrs.pop(SHOT2_PROMPT_ATTR, None)
        fr.attrs = attrs
        flag_modified(fr, "attrs")
    else:
        fr.image_prompt = text or None


def _get_video_prompt(fr: Frame, shot: int) -> str:
    if shot == 2:
        return str((fr.attrs or {}).get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
    return (fr.animation_prompt or "").strip()


def _set_video_prompt(fr: Frame, shot: int, value: str) -> None:
    text = (value or "").strip()
    if shot == 2:
        attrs = dict(fr.attrs or {})
        if text:
            attrs[SHOT2_VIDEO_PROMPT_ATTR] = text
        else:
            attrs.pop(SHOT2_VIDEO_PROMPT_ATTR, None)
        fr.attrs = attrs
        flag_modified(fr, "attrs")
    else:
        fr.animation_prompt = text or None


def _swap_trim_between(
    board_meta: dict[str, Any],
    a_frame: int,
    a_shot: int,
    b_frame: int,
    b_shot: int,
) -> None:
    from app.services.montage_board_meta import trim_key

    trims = board_meta.get("video_trims")
    if not isinstance(trims, dict):
        return
    ka, kb = trim_key(a_frame, a_shot), trim_key(b_frame, b_shot)
    ta, tb = trims.get(ka), trims.get(kb)
    if ta is None and tb is None:
        return
    if tb is None:
        trims.pop(ka, None)
    else:
        trims[ka] = tb
    if ta is None:
        trims.pop(kb, None)
    else:
        trims[kb] = ta
    board_meta["video_trims"] = trims


async def move_scene_image(
    session: AsyncSession,
    project: Project,
    *,
    from_frame: int,
    from_shot: int,
    to_frame: int,
    to_shot: int,
) -> dict[str, Any]:
    """Перенос/обмен картинки между слотами (в т.ч. в пустую ячейку).

    Если цель пуста — move (источник очищается). Если занята — swap.
    Промты изображений едут вместе с файлами.
    """
    if from_shot not in (1, 2) or to_shot not in (1, 2):
        return {"ok": False, "reason": "shot должен быть 1 или 2"}
    if from_frame == to_frame and from_shot == to_shot:
        return {"ok": False, "reason": "тот же слот"}

    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    src_path = _find_shot_image(scenes, from_frame, from_shot)
    if src_path is None or not src_path.is_file():
        return {"ok": False, "reason": "в источнике нет картинки"}

    dst_path = _find_shot_image(scenes, to_frame, to_shot)
    src_bytes = src_path.read_bytes()
    src_suf = src_path.suffix or ".png"
    dst_bytes = (
        dst_path.read_bytes() if dst_path is not None and dst_path.is_file() else None
    )
    dst_suf = (dst_path.suffix if dst_path is not None else ".png") or ".png"
    mode = "swap" if dst_bytes is not None else "move"

    await delete_scene_image(session, project, from_frame, shot=from_shot)
    await delete_scene_image(session, project, to_frame, shot=to_shot)

    await save_scene_image_upload(
        session,
        project,
        to_frame,
        shot=to_shot,
        content=src_bytes,
        suffix=src_suf,
    )
    if dst_bytes is not None:
        await save_scene_image_upload(
            session,
            project,
            from_frame,
            shot=from_shot,
            content=dst_bytes,
            suffix=dst_suf,
        )

    fr_from = await _frame(session, project.id, from_frame)
    fr_to = (
        fr_from
        if from_frame == to_frame
        else await _frame(session, project.id, to_frame)
    )
    prompts_moved = False
    if fr_from is not None and fr_to is not None:
        p_from = _get_image_prompt(fr_from, from_shot)
        p_to = _get_image_prompt(fr_to, to_shot)
        _set_image_prompt(fr_to, to_shot, p_from)
        _set_image_prompt(fr_from, from_shot, p_to if mode == "swap" else "")
        prompts_moved = True

    from app.services.montage_board_meta import mark_stale_videos, montage_meta, set_montage_meta

    board = montage_meta(project)
    mark_stale_videos(board, from_frame, shot=from_shot)
    mark_stale_videos(board, to_frame, shot=to_shot)
    set_montage_meta(project, board)
    await session.flush()
    return {
        "ok": True,
        "mode": mode,
        "kind": "image",
        "from_frame": from_frame,
        "from_shot": from_shot,
        "to_frame": to_frame,
        "to_shot": to_shot,
        "prompts_moved": prompts_moved,
    }


async def move_scene_video(
    session: AsyncSession,
    project: Project,
    *,
    from_frame: int,
    from_shot: int,
    to_frame: int,
    to_shot: int,
) -> dict[str, Any]:
    """Перенос/обмен видео между слотами (любые кадры). Промты + trim едут вместе."""
    if from_shot not in (1, 2) or to_shot not in (1, 2):
        return {"ok": False, "reason": "shot должен быть 1 или 2"}
    if from_frame == to_frame and from_shot == to_shot:
        return {"ok": False, "reason": "тот же слот"}

    videos = project.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    src_path = _find_shot_video(videos, from_frame, from_shot)
    if src_path is None or not src_path.is_file():
        return {"ok": False, "reason": "в источнике нет видео"}

    dst_path = _find_shot_video(videos, to_frame, to_shot)
    src_bytes = src_path.read_bytes()
    src_suf = src_path.suffix or ".mp4"
    dst_bytes = (
        dst_path.read_bytes() if dst_path is not None and dst_path.is_file() else None
    )
    dst_suf = (dst_path.suffix if dst_path is not None else ".mp4") or ".mp4"
    mode = "swap" if dst_bytes is not None else "move"

    await delete_scene_video(session, project, from_frame, shot=from_shot)
    await delete_scene_video(session, project, to_frame, shot=to_shot)

    await save_scene_video_upload(
        session,
        project,
        to_frame,
        shot=to_shot,
        content=src_bytes,
        suffix=src_suf,
    )
    if dst_bytes is not None:
        await save_scene_video_upload(
            session,
            project,
            from_frame,
            shot=from_shot,
            content=dst_bytes,
            suffix=dst_suf,
        )

    fr_from = await _frame(session, project.id, from_frame)
    fr_to = (
        fr_from
        if from_frame == to_frame
        else await _frame(session, project.id, to_frame)
    )
    prompts_moved = False
    if fr_from is not None and fr_to is not None:
        p_from = _get_video_prompt(fr_from, from_shot)
        p_to = _get_video_prompt(fr_to, to_shot)
        _set_video_prompt(fr_to, to_shot, p_from)
        _set_video_prompt(fr_from, from_shot, p_to if mode == "swap" else "")
        prompts_moved = True

    from app.services.montage_board_meta import mark_stale_videos, montage_meta, set_montage_meta

    board = montage_meta(project)
    _swap_trim_between(board, from_frame, from_shot, to_frame, to_shot)
    mark_stale_videos(board, from_frame, shot=from_shot)
    mark_stale_videos(board, to_frame, shot=to_shot)
    set_montage_meta(project, board)
    await session.flush()
    return {
        "ok": True,
        "mode": mode,
        "kind": "video",
        "from_frame": from_frame,
        "from_shot": from_shot,
        "to_frame": to_frame,
        "to_shot": to_shot,
        "prompts_moved": prompts_moved,
    }


async def swap_media_slots(
    session: AsyncSession,
    project: Project,
    *,
    kind: Literal["image", "video"],
    a_frame: int,
    a_shot: int,
    b_frame: int,
    b_shot: int,
) -> dict[str, Any]:
    """Обмен двух слотов одного типа (картинка↔картинка или видео↔видео).

    Слоты могут быть из любых кадров. Если один пуст — перенос заполненного.
    """
    if a_shot not in (1, 2) or b_shot not in (1, 2):
        return {"ok": False, "reason": "shot должен быть 1 или 2"}
    if a_frame == b_frame and a_shot == b_shot:
        return {"ok": False, "reason": "тот же слот"}

    if kind == "image":
        scenes = project.data_dir / "scenes"
        a_path = _find_shot_image(scenes, a_frame, a_shot)
        b_path = _find_shot_image(scenes, b_frame, b_shot)
        a_has = a_path is not None and a_path.is_file()
        b_has = b_path is not None and b_path.is_file()
        if not a_has and not b_has:
            return {"ok": False, "reason": "оба слота пустые"}
        from_frame, from_shot = (a_frame, a_shot) if a_has else (b_frame, b_shot)
        to_frame, to_shot = (b_frame, b_shot) if a_has else (a_frame, a_shot)
        return await move_scene_image(
            session,
            project,
            from_frame=from_frame,
            from_shot=from_shot,
            to_frame=to_frame,
            to_shot=to_shot,
        )

    videos = project.data_dir / "videos"
    a_path = _find_shot_video(videos, a_frame, a_shot)
    b_path = _find_shot_video(videos, b_frame, b_shot)
    a_has = a_path is not None and a_path.is_file()
    b_has = b_path is not None and b_path.is_file()
    if not a_has and not b_has:
        return {"ok": False, "reason": "оба слота пустые"}
    from_frame, from_shot = (a_frame, a_shot) if a_has else (b_frame, b_shot)
    to_frame, to_shot = (b_frame, b_shot) if a_has else (a_frame, a_shot)
    return await move_scene_video(
        session,
        project,
        from_frame=from_frame,
        from_shot=from_shot,
        to_frame=to_frame,
        to_shot=to_shot,
    )
