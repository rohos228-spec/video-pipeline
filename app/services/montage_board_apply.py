"""Применить правки панели монтажа: trim + очередь regen."""

from __future__ import annotations

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
            # Промт с UI (то, что видит пользователь на доске) — приоритетнее
            # повторного чтения Excel, иначе уходит «другой» текст.
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
            # Outsee мог отдать файл, а пост-шаг упал — всё равно заменяем кадр.
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
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise RuntimeError(f"проект #{project_id} не найден")
            return await finalize_image_regen(
                session, project, prep, new_path, board=board
            )

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
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            raise RuntimeError(f"проект #{project_id} не найден")
        return await finalize_video_regen(
            session, project, prep, new_path, board=board
        )


async def apply_montage_board(
    session: AsyncSession,
    project: Project,
    *,
    video_trims: dict[str, dict[str, float]] | None = None,
    pending_ops: list[dict[str, Any]] | None = None,
    on_progress: ProgressCb | None = None,
) -> dict[str, Any]:
    board = montage_meta(project)
    if video_trims is not None:
        board["video_trims"] = video_trims
    ops = list(pending_ops or board.get("pending_ops") or [])
    clear_highlights(board)

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    remaining: list[dict[str, Any]] = []

    # Сначала — забрать уже готовые карточки из истории Outsee (без нового Generate).
    image_ops = [op for op in ops if str(op.get("type") or "").startswith("image")]
    if image_ops:
        try:
            from app.services.montage_outsee_recover import recover_before_regen_ops

            recovered = await recover_before_regen_ops(session, project, ops)
            for item in recovered.get("saved") or []:
                results.append(
                    {
                        "ok": True,
                        "kind": "image",
                        "recovered_from_outsee": True,
                        "frame_number": item.get("frame_number"),
                        "shot": item.get("shot"),
                        "path": item.get("path"),
                        "highlight": (
                            f"{item.get('frame_number')}:image{item.get('shot')}"
                        ),
                    }
                )
                hl = results[-1].get("highlight")
                if hl:
                    add_highlight(board, str(hl))
            ops = list(recovered.get("remaining_ops") or ops)
            if recovered.get("saved_count"):
                logger.info(
                    "montage apply #{}: recovered {} images from Outsee history "
                    "before Generate",
                    project.id,
                    recovered["saved_count"],
                )
                board["pending_ops"] = list(ops)
                set_montage_meta(project, board)
                await session.flush()
                if on_progress is not None:
                    await on_progress(
                        len(results),
                        max(len(ops) + len(results), 1),
                        {"ok": True, "recovered": recovered["saved_count"]},
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "montage apply #{}: Outsee history recover skipped: {}",
                project.id,
                exc,
            )

    total = len(ops) + len(results)

    for idx, op in enumerate(ops):
        try:
            result = await _run_op_with_short_sessions(project.id, op, board)
            results.append(result)
            highlight = result.get("highlight")
            if highlight:
                add_highlight(board, str(highlight))
            # Сужаем очередь по ходу — cancel/restart не вернёт уже сделанное.
            board["pending_ops"] = list(remaining) + list(ops[idx + 1 :])
            set_montage_meta(project, board)
            await session.flush()
            if on_progress is not None:
                await on_progress(len(results), max(total, 1), result)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            logger.warning(
                "montage apply #{} op {} failed: {}",
                project.id,
                op,
                msg,
            )
            errors.append(msg)
            results.append({"ok": False, "error": msg, "op": op})
            remaining.append(op)
            board["pending_ops"] = list(remaining) + list(ops[idx + 1 :])
            set_montage_meta(project, board)
            await session.flush()
            if on_progress is not None:
                await on_progress(
                    len(results), max(total, 1), {"ok": False, "error": msg}
                )

    # Успешные ops снимаем; упавшие оставляем — можно снова «Применить правки».
    board["pending_ops"] = remaining
    touch_applied(board)
    set_montage_meta(project, board)
    await session.flush()

    return {
        "ok": not errors,
        "results": results,
        "errors": errors,
        "meta": public_board_meta(board),
    }
