"""Применить правки панели монтажа: trim + очередь regen."""

from __future__ import annotations

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
            prep = await prepare_image_regen(
                session,
                project,
                frame_number,
                shot=shot,
                mode=mode,
                new_prompt=str(op.get("prompt") or ""),
                correction=str(op.get("correction") or op.get("prompt") or ""),
                board=board,
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
        new_path = await execute_image_regen(prep)
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                raise RuntimeError(f"проект #{project_id} не найден")
            return await finalize_image_regen(
                session, project, prep, new_path, board=board
            )

    new_path = await execute_video_regen(prep)
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
    total = len(ops)

    for idx, op in enumerate(ops):
        try:
            result = await _run_op_with_short_sessions(project.id, op, board)
            results.append(result)
            highlight = result.get("highlight")
            if highlight:
                add_highlight(board, str(highlight))
            if on_progress is not None:
                await on_progress(idx + 1, total, result)
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
            if on_progress is not None:
                await on_progress(idx + 1, total, {"ok": False, "error": msg})

    board["pending_ops"] = []
    touch_applied(board)
    set_montage_meta(project, board)
    await session.flush()

    return {
        "ok": not errors,
        "results": results,
        "errors": errors,
        "meta": public_board_meta(board),
    }
