"""Статусы операций монтажного борда: чистые функции без БД."""

from __future__ import annotations

from typing import Any

# Закрытые статусы: правка применена или осознанно снята. Остальные — в pending_ops.
_CLOSED = ("ok", "skip")

# Операции с текстом пользователя: при пропуске платного кадра правку не теряем.
HELD_ON_PAID_SKIP = frozenset({"image_ai_change", "video_ai_change"})


def op_is_open(status: str | None) -> bool:
    """Операция ещё ждёт применения и видна в board["pending_ops"]."""
    return status not in _CLOSED


def op_status_after_result(
    result: dict[str, Any], ok: bool, op: dict[str, Any] | None = None
) -> str:
    """Статус после выполнения. Платный кадр пропущен: правка пользователя — "held", регенерация — "skip"."""
    if result.get("paid_frame_skipped"):
        op_type = str((op or {}).get("type") or "")
        return "held" if op_type in HELD_ON_PAID_SKIP else "skip"
    return "ok" if ok else "fail"
