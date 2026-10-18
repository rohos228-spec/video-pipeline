"""Статусы операций монтажного борда: чистые функции без БД."""

from __future__ import annotations

from typing import Any

# Закрытые статусы: правка применена или осознанно снята. Остальные — в pending_ops.
_CLOSED = ("ok", "skip")


def op_is_open(status: str | None) -> bool:
    """Операция ещё ждёт применения и видна в board["pending_ops"]."""
    return status not in _CLOSED


def op_status_after_result(result: dict[str, Any], ok: bool) -> str:
    """Статус после выполнения. Пропуск платного кадра — "held": правка не теряется."""
    if result.get("paid_frame_skipped"):
        return "held"
    return "ok" if ok else "fail"
