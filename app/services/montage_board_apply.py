"""Применить правки панели монтажа: trim + очередь regen."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Project
from app.services.montage_board_meta import (
    add_highlight,
    clear_highlights,
    montage_meta,
    public_board_meta,
    set_montage_meta,
    touch_applied,
)
from app.services.montage_board_regen import (
    execute_image_regen,
    execute_video_regen,
    finalize_image_regen,
    finalize_video_regen,
    prepare_image_regen,
    prepare_video_regen,
)


ProgressCb = Callable[[int, int, dict[str, Any]], Awaitable[None]]


# Совпадает с порогами outsee-валидации — не финализируем stub/placeholder.
_READY_IMAGE_BYTES = 200_000
_READY_VIDEO_BYTES = 80_000


def _ready_local_asset(path: Path, *, min_bytes: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size >= min_bytes
    except OSError:
        return False


def _is_sqlite_locked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "database is locked" in msg or "database is busy" in msg


async def _finalize_image_with_retry(
    project_id: int,
    prep: Any,
    new_path: Path,
    board: dict[str, Any],
) -> dict[str, Any]:
    last: BaseException | None = None
    for attempt in range(1, 8):
        try:
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is None:
                    raise RuntimeError(f"проект #{project_id} не найден")
                return await finalize_image_regen(
                    session, project, prep, new_path, board=board
                )
        except Exception as exc:  # noqa: BLE001
            last = exc
            if _is_sqlite_locked(exc) and attempt < 7:
                wait = min(2.0 * attempt, 10.0)
                logger.warning(
                    "montage finalize image #{} F{} locked ({}/7), wait {:.1f}s",
                    project_id,
                    prep.frame_number,
                    attempt,
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            raise
    assert last is not None
    raise last


async def _finalize_video_with_retry(
    project_id: int,
    prep: Any,
    new_path: Path,
    board: dict[str, Any],
) -> dict[str, Any]:
    last: BaseException | None = None
    for attempt in range(1, 8):
        try:
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is None:
                    raise RuntimeError(f"проект #{project_id} не найден")
                return await finalize_video_regen(
                    session, project, prep, new_path, board=board
                )
        except Exception as exc:  # noqa: BLE001
            last = exc
            if _is_sqlite_locked(exc) and attempt < 7:
                wait = min(2.0 * attempt, 10.0)
                logger.warning(
                    "montage finalize video #{} F{} locked ({}/7), wait {:.1f}s",
                    project_id,
                    prep.frame_number,
                    attempt,
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            raise
    assert last is not None
    raise last


async def _run_op_with_short_sessions(
    project_id: int,
    op: dict[str, Any],
    board: dict[str, Any],
) -> dict[str, Any]:
    """Чтение БД → Outsee (без сессии) → запись результата."""
    op_type = str(op.get("type") or "").strip()
    frame_number = int(op["frame_number"])
    shot = int(op.get("shot") or 1)

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise RuntimeError(f"проект #{project_id} не найден")

        if op_type in ("image_regen", "image_regen_prompt", "image_regen_correction"):
            mode = "same_prompt"
            if op_type == "image_regen_prompt":
                mode = "edit_prompt"
            elif op_type == "image_regen_correction":
                mode = "correction"
            pinned = str(op.get("prompt") or "").strip()
            prep = await prepare_image_regen(
                session,
                project,
                frame_number,
                shot=shot,
                mode=mode,
                new_prompt=str(op.get("prompt") or ""),
                correction=str(op.get("correction") or op.get("prompt") or ""),
                board=board,
                pinned_prompt=pinned if op_type == "image_regen" and pinned else None,
            )
        elif op_type in ("video_regen", "video_regen_prompt"):
            mode = "edit_prompt" if op_type == "video_regen_prompt" else "same_prompt"
            prep = await prepare_video_regen(
                session,
                project,
                frame_number,
                shot=shot,
                mode=mode,
                new_prompt=str(op.get("prompt") or ""),
                board=board,
            )
        else:
            raise RuntimeError(f"неизвестная операция: {op_type}")

    if op_type.startswith("image"):
        try:
            new_path = await execute_image_regen(prep)
        except Exception as exc:  # noqa: BLE001
            if _ready_local_asset(prep.file_path, min_bytes=_READY_IMAGE_BYTES):
                logger.warning(
                    "montage apply #{} image frame {} shot {}: "
                    "execute failed but file ready — finalize: {}",
                    project_id,
                    frame_number,
                    shot,
                    exc,
                )
                new_path = prep.file_path
            else:
                raise
        return await _finalize_image_with_retry(project_id, prep, new_path, board)

    try:
        new_path = await execute_video_regen(prep)
    except Exception as exc:  # noqa: BLE001
        if _ready_local_asset(prep.file_path, min_bytes=_READY_VIDEO_BYTES):
            logger.warning(
                "montage apply #{} video frame {} shot {}: "
                "execute failed but file ready — finalize: {}",
                project_id,
                frame_number,
                shot,
                exc,
            )
            new_path = prep.file_path
        else:
            raise
    return await _finalize_video_with_retry(project_id, prep, new_path, board)


async def _persist_board_meta(project_id: int, board: dict[str, Any]) -> None:
    """Короткий write — не держим соединение на время Outsee."""
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise RuntimeError(f"проект #{project_id} не найден")
        set_montage_meta(project, board)


async def apply_montage_board_by_id(
    project_id: int,
    *,
    video_trims: dict[str, dict[str, float]] | None = None,
    pending_ops: list[dict[str, Any]] | None = None,
    on_progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """Apply без долгой ORM-сессии — для фонового job (video/image regen)."""
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise RuntimeError(f"проект #{project_id} не найден")
        board = montage_meta(project)
        if video_trims is not None:
            board["video_trims"] = video_trims
        ops = list(pending_ops or board.get("pending_ops") or [])
        clear_highlights(board)
        set_montage_meta(project, board)
    # session закрыта — дальше только короткие writes + Outsee без DB lock

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    remaining: list[dict[str, Any]] = []
    total = max(len(ops), 1)

    for idx, op in enumerate(ops):
        try:
            result = await _run_op_with_short_sessions(project_id, op, board)
            results.append(result)
            highlight = result.get("highlight")
            if highlight:
                add_highlight(board, str(highlight))
            board["pending_ops"] = list(remaining) + list(ops[idx + 1 :])
            await _persist_board_meta(project_id, board)
            if on_progress is not None:
                await on_progress(len(results), total, result)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            logger.warning(
                "montage apply #{} op {} failed: {}",
                project_id,
                op,
                msg,
            )
            errors.append(msg)
            results.append({"ok": False, "error": msg, "op": op})
            remaining.append(op)
            board["pending_ops"] = list(remaining) + list(ops[idx + 1 :])
            await _persist_board_meta(project_id, board)
            if on_progress is not None:
                await on_progress(len(results), total, {"ok": False, "error": msg})

    board["pending_ops"] = remaining
    touch_applied(board)
    await _persist_board_meta(project_id, board)
    return {
        "ok": not errors,
        "results": results,
        "errors": errors,
        "meta": public_board_meta(board),
    }


async def apply_montage_board(
    session: AsyncSession,
    project: Project,
    *,
    video_trims: dict[str, dict[str, float]] | None = None,
    pending_ops: list[dict[str, Any]] | None = None,
    on_progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """HTTP/trim path: короткие ops через by_id; session только для seed commit."""
    board = montage_meta(project)
    if video_trims is not None:
        board["video_trims"] = video_trims
    ops = list(pending_ops or board.get("pending_ops") or [])
    clear_highlights(board)
    set_montage_meta(project, board)
    await session.flush()
    await session.commit()

    if not ops:
        touch_applied(board)
        set_montage_meta(project, board)
        await session.flush()
        await session.commit()
        return {
            "ok": True,
            "results": [],
            "errors": [],
            "meta": public_board_meta(board),
        }

    # Есть regen — не держим HTTP/job session на Outsee.
    return await apply_montage_board_by_id(
        int(project.id),
        video_trims=None,  # уже записаны выше
        pending_ops=ops,
        on_progress=on_progress,
    )
