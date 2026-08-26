"""Per-node LLM override for vibecode models (contextvar on a running step)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from loguru import logger


@dataclass(frozen=True)
class NodeLlmOverride:
    model_id: str
    channel: str
    kind: str  # text | image
    provider: str
    label: str


_current: ContextVar[NodeLlmOverride | None] = ContextVar(
    "node_llm_override", default=None
)


def current_override() -> NodeLlmOverride | None:
    return _current.get()


def current_text_model_id() -> str | None:
    ov = current_override()
    if ov and ov.kind == "text" and ov.model_id:
        return ov.model_id
    return None


@contextmanager
def use_override(choice: NodeLlmOverride | None) -> Iterator[NodeLlmOverride | None]:
    token: Token[NodeLlmOverride | None] = _current.set(choice)
    try:
        yield choice
    finally:
        _current.reset(token)


@contextmanager
def bind_project_llm(project: Any, status: Any | None = None) -> Iterator[NodeLlmOverride | None]:
    """Поставить модель с канвас-ноды текущего running-статуса."""
    from app.orchestrator.node_registry import RUNNING_TO_NODE_TYPE
    from app.services.excel_gpt_node import slot_from_running_status
    from app.services.vibecode_catalog import resolve_node_choice

    meta = getattr(project, "meta", None) if project is not None else None
    node_key: str | None = None
    node_type: str | None = None
    if status is not None and slot_from_running_status(status) is not None:
        node_type = "excel_gpt"
        if isinstance(meta, dict):
            node_key = str(meta.get("active_excel_gpt_node_key") or "").strip() or None
    elif status is not None:
        node_type = RUNNING_TO_NODE_TYPE.get(status)
        if node_type == "sd_agent":
            from app.services.scene_design import runner as sd_runner

            agent = sd_runner.get_only_agent(project)
            if agent:
                node_key = sd_runner.resolve_sd_node_key(project, agent)
        elif node_type == "sd_assemble":
            from app.services.scene_design import runner as sd_runner

            node_key = sd_runner.resolve_sd_node_key(project, "assemble")

    choice_raw = resolve_node_choice(meta, node_key=node_key, node_type=node_type)
    ov: NodeLlmOverride | None = None
    if choice_raw and choice_raw.get("kind") == "text":
        ov = NodeLlmOverride(
            model_id=str(choice_raw["id"]),
            channel=str(choice_raw.get("channel") or "stable"),
            kind="text",
            provider=str(choice_raw.get("provider") or "vibecode"),
            label=str(choice_raw.get("label") or choice_raw["id"]),
        )
        logger.info(
            "node_llm: #{} {} → {} ({})",
            getattr(project, "id", "?"),
            node_key or node_type or "?",
            ov.label,
            ov.model_id,
        )
    with use_override(ov) as bound:
        yield bound


def generation_text_override(
    project: Any | None = None,
    *,
    node_type: str = "image_prompts",
    node_key: str | None = None,
) -> NodeLlmOverride:
    """Текстовая генерация всегда vibecode, шапка kie/Kimi не перебивает."""
    from app.services.vibecode_catalog import (
        DEFAULT_TEXT_MODEL_ID,
        find_model,
        resolve_node_choice,
    )

    meta = getattr(project, "meta", None) if project is not None else None
    choice_raw = resolve_node_choice(meta, node_key=node_key, node_type=node_type)
    if not (choice_raw and choice_raw.get("kind") == "text"):
        choice_raw = find_model(DEFAULT_TEXT_MODEL_ID)
    if not choice_raw:
        choice_raw = {
            "id": DEFAULT_TEXT_MODEL_ID,
            "label": "GPT 5.6 Sol",
            "channel": "stable",
        }
    return NodeLlmOverride(
        model_id=str(choice_raw.get("id") or DEFAULT_TEXT_MODEL_ID),
        channel=str(choice_raw.get("channel") or "stable"),
        kind="text",
        provider="vibecode",
        label=str(choice_raw.get("label") or choice_raw.get("id") or DEFAULT_TEXT_MODEL_ID),
    )


@contextmanager
def bind_generation_llm(
    project: Any | None = None,
    *,
    node_type: str = "image_prompts",
    node_key: str | None = None,
) -> Iterator[NodeLlmOverride | None]:
    ov = generation_text_override(project, node_type=node_type, node_key=node_key)
    logger.info(
        "generation_llm: #{} {} → vibecode {} ({})",
        getattr(project, "id", "?") if project is not None else "?",
        node_key or node_type,
        ov.label,
        ov.model_id,
    )
    with use_override(ov) as bound:
        yield bound
