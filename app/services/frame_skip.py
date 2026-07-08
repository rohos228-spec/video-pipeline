"""Skip frames on non-recoverable outsee errors (e.g. known public figure)."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, FrameStatus

KNOWN_FACE_SKIP = "known_face"


def frame_skip_reason(frame: Frame) -> str | None:
    reason = (frame.attrs or {}).get("skip_reason")
    return str(reason) if reason else None


def frame_was_known_face_skip(frame: Frame) -> bool:
    return frame_skip_reason(frame) == KNOWN_FACE_SKIP


async def mark_frame_known_face_skip(
    session: AsyncSession,
    frame: Frame,
    *,
    is_shot2: bool = False,
    sheet=None,
) -> None:
    from app.services.plan_shot2 import SHOT2_STATUS_ATTR

    attrs = dict(frame.attrs or {})
    attrs["skip_reason"] = KNOWN_FACE_SKIP
    if is_shot2:
        attrs[SHOT2_STATUS_ATTR] = "skipped"
    else:
        frame.status = FrameStatus.failed
    frame.attrs = attrs
    if sheet is not None:
        try:
            sheet.write_frame(
                frame.number,
                frame_status="skipped_known_face",
                last_error="outsee: known face",
            )
        except Exception:  # noqa: BLE001
            pass
    await session.flush()
