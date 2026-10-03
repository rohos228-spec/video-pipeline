from __future__ import annotations

from typing import Any, Mapping

_COMPLETE_FINISH = {"stop", "end_turn"}
_TRUNCATED_FINISH = {"length", "max_tokens", "max_output_tokens"}


def _finish_reason(raw: Mapping[str, Any] | None, finish_reason: str | None) -> str | None:
    if finish_reason:
        return str(finish_reason).lower()
    if not isinstance(raw, Mapping):
        return None
    top = raw.get("finish_reason")
    if top:
        return str(top).lower()
    choices = raw.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        value = choices[0].get("finish_reason")
        if value:
            return str(value).lower()
    return None


def response_complete(raw: Mapping[str, Any] | None, finish_reason: str | None = None) -> bool:
    raw = raw if isinstance(raw, Mapping) else {}
    if raw.get("sse_salvaged"):
        return False
    status = raw.get("status")
    if status is not None:
        return status == "completed"
    return _finish_reason(raw, finish_reason) in _COMPLETE_FINISH


def response_truncated(raw: Mapping[str, Any] | None, finish_reason: str | None = None) -> bool:
    raw = raw if isinstance(raw, Mapping) else {}
    status = raw.get("status")
    if status == "incomplete":
        return True
    return _finish_reason(raw, finish_reason) in _TRUNCATED_FINISH
