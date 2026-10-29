"""Единственная точка вызова модели для агента стиля.

Все специалисты ходят через ``ask()``: проверка бюджета → ``gpt_client``
→ журнал ``llm_calls.jsonl``. Тесты подменяют ``ask`` (monkeypatch) —
платных вызовов в тестах нет.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.style_agent.budget import LlmBudget
from app.services.style_analyzer import parse_json_object, strip_markdown_fences

DEFAULT_TIMEOUT = 420.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_log(log_path: Path | None, record: dict[str, Any]) -> None:
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("style_agent: не записать llm_calls.jsonl: {}", exc)


async def ask(
    text: str,
    *,
    files: list[Path] | None = None,
    system: str | None = None,
    budget: LlmBudget,
    label: str = "",
    project_id: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    log_path: Path | None = None,
) -> str:
    """Один запрос к текущей модели (``llm_override`` уже привязан снаружи)."""
    n = budget.reserve(label)
    from app.services.gpt_client import get_gpt_client

    client = get_gpt_client()
    started = time.monotonic()
    err: str | None = None
    raw = ""
    try:
        raw = await client.ask_with_files(
            text,
            list(files or []),
            system=system,
            timeout=timeout,
            max_retries=1,
            project_id=project_id,
        )
        return raw
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        raise
    finally:
        _append_log(
            log_path,
            {
                "n": n,
                "label": label,
                "at": _utc_now(),
                "files": [p.name for p in (files or [])],
                "prompt_chars": len(text or ""),
                "reply_chars": len(raw or ""),
                "elapsed_s": round(time.monotonic() - started, 2),
                "error": err,
            },
        )


async def ask_json(
    text: str,
    *,
    files: list[Path] | None = None,
    system: str | None = None,
    budget: LlmBudget,
    label: str = "",
    project_id: int | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    log_path: Path | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """``ask`` + разбор JSON-объекта (``style_analyzer.parse_json_object``)."""
    raw = await ask(
        text,
        files=files,
        system=system,
        budget=budget,
        label=label,
        project_id=project_id,
        timeout=timeout,
        log_path=log_path,
    )
    data = parse_json_object(strip_markdown_fences(raw or ""))
    return data, raw or ""
