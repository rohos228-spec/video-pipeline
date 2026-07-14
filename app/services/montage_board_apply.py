"""Применить правки панели монтажа: trim + очередь regen."""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.montage_board_meta import (
    add_highlight,
    clear_highlights,
    montage_meta,
    public_board_meta,
    set_montage_meta,
    touch_applied,
)
from app.services.montage_board_regen import regen_scene_image, regen_scene_video


async def _run_op(
    session: AsyncSession,
    project: Project,
    op: dict[str, Any],
    board: dict[str, Any],
) -> dict[str, Any]:
    op_type = str(op.get("type") or "").strip()
    frame_number = int(op["frame_number"])
    shot = int(op.get("shot") or 1)

    if op_type == "image_regen":
        return await regen_scene_image(
            session, project, frame_number, shot=shot, mode="same_prompt", board=board
        )
    if op_type == "image_regen_prompt":
        return await regen_scene_image(
            session,
            project,
            frame_number,
            shot=shot,
            mode="edit_prompt",
            new_prompt=str(op.get("prompt") or ""),
            board=board,
        )
    if op_type == "image_regen_correction":
        return await regen_scene_image(
            session,
            project,
            frame_number,
            shot=shot,
            mode="correction",
            correction=str(op.get("correction") or op.get("prompt") or ""),
            board=board,
        )
    if op_type == "video_regen":
        return await regen_scene_video(
            session, project, frame_number, shot=shot, mode="same_prompt", board=board
        )
    if op_type == "video_regen_prompt":
        return await regen_scene_video(
            session,
            project,
            frame_number,
            shot=shot,
            mode="edit_prompt",
            new_prompt=str(op.get("prompt") or ""),
            board=board,
        )
    raise RuntimeError(f"неизвестная операция: {op_type}")


async def apply_montage_board(
    session: AsyncSession,
    project: Project,
    *,
    video_trims: dict[str, dict[str, float]] | None = None,
    pending_ops: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    board = montage_meta(project)
    if video_trims is not None:
        board["video_trims"] = video_trims
    ops = list(pending_ops or board.get("pending_ops") or [])
    clear_highlights(board)

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    for op in ops:
        try:
            result = await _run_op(session, project, op, board)
            results.append(result)
            highlight = result.get("highlight")
            if highlight:
                add_highlight(board, str(highlight))
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
