"""Видимая подмена модели.

Генерацию не останавливаем. Каждая подмена пишет warning, событие
трекера и строку, которую нода кладёт в свой отчёт.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

_events: list[dict[str, Any]] = []
_EVENT_CAP = 200


def substitution_events() -> list[dict[str, Any]]:
    return list(_events)


def clear_substitution_events() -> None:
    _events.clear()


def note_model_substitution(
    *,
    original_model: str,
    substituted_model: str,
    reason: str,
    node: str,
    run: str | int | None = None,
    project_id: int | None = None,
) -> str:
    """Зафиксировать подмену. Возвращает строку для отчёта ноды."""
    original = str(original_model or "").strip()
    substituted = str(substituted_model or "").strip()
    why = str(reason or "").strip()
    node_name = str(node or "").strip() or "pipeline"
    run_id = "" if run is None else str(run)
    line = f"подмена модели: {original} → {substituted} ({why})"
    event = {
        "event": "model_substitution",
        "original_model": original,
        "substituted_model": substituted,
        "reason": why,
        "node": node_name,
        "run": run_id,
        "line": line,
    }
    logger.warning(
        "подмена модели original={} substituted={} reason={} node={} run={}",
        original,
        substituted,
        why,
        node_name,
        run_id,
    )
    _events.append(event)
    if len(_events) > _EVENT_CAP:
        del _events[: len(_events) - _EVENT_CAP]
    try:
        from app.services.api_tracker_hook import record_api_call

        record_api_call(
            provider="pipeline",
            model=substituted or original or "unknown",
            call_type="model_substitution",
            status_code=200,
            project_source=(
                f"pipeline_project_{project_id}" if project_id else "pipeline"
            ),
            metadata={
                "event": "model_substitution",
                "original_model": original,
                "substituted_model": substituted,
                "reason": why,
                "node": node_name,
                "run": run_id,
            },
        )
    except Exception:  # noqa: BLE001
        logger.debug("model_substitution tracker skipped", exc_info=True)
    return line


def frame_card_with_substitution(
    attrs: dict[str, Any] | None,
    line: str,
) -> dict[str, Any]:
    """Пометка на карточке кадра (attrs), кадр не удаляется."""
    out = dict(attrs or {})
    out["подмена_модели"] = line
    return out
