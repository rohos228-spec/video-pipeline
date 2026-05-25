"""Единая GPT/xlsx-сессия — используется `xlsx_step_runners` (bot + worker).

Telegram-бот и orchestrator вызывают `xlsx_step_runners`, который внутри
зовёт функции отсюда. Не дублируйте GPT-логику в шагах.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

from loguru import logger

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.services.xlsx_versioning import validate_xlsx

T = TypeVar("T")


async def telegram_style_ask_with_files(
    chat_msg: str,
    attachments: list[Path],
    *,
    timeout: float = 900,
    project_id: int | None = None,
) -> str:
    """browser_session → new_conversation → ask_with_files (как bot.py)."""
    for fp in attachments:
        if not fp.exists():
            raise FileNotFoundError(f"xlsx-gpt-flow: файл не найден {fp}")

    names = ", ".join(p.name for p in attachments)
    stripped = (chat_msg or "").strip()
    logger.info(
        "xlsx-gpt-flow: ask_with_files files=[{}] chat_len={}",
        names,
        len(stripped),
    )

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        await gpt.new_conversation()
        reply = await gpt.ask_with_files(
            stripped,
            attachments,
            timeout=timeout,
            project_id=project_id,
        )
        logger.info("xlsx-gpt-flow: GPT reply len={}", len(reply or ""))
        return reply


async def telegram_style_ask_and_download(
    chat_msg: str,
    attachments: list[Path],
    download_path: Path,
    *,
    ask_timeout: float = 900,
    download_timeout: float = 900,
    project_id: int | None = None,
    validate_xlsx_download: bool = False,
) -> str:
    """Как bot _run_plan_xlsx / _run_split_xlsx: ask → download в одной сессии."""
    for fp in attachments:
        if not fp.exists():
            raise FileNotFoundError(f"xlsx-gpt-flow: файл не найден {fp}")

    names = ", ".join(p.name for p in attachments)
    stripped = (chat_msg or "").strip()
    logger.info(
        "xlsx-gpt-flow: ask+download files=[{}] → {}",
        names,
        download_path.name,
    )

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        await gpt.new_conversation()
        reply = await gpt.ask_with_files(
            stripped,
            attachments,
            timeout=ask_timeout,
            project_id=project_id,
        )
        logger.info("xlsx-gpt-flow: GPT reply len={}", len(reply or ""))
        logger.info("xlsx-gpt-flow: скачиваю вложение из ответа → {}", download_path)
        await gpt.download_attachment_from_last_reply(
            download_path, timeout=download_timeout
        )

    if validate_xlsx_download:
        err = validate_xlsx(download_path)
        if err is not None:
            raise RuntimeError(f"скачанный xlsx невалиден: {err}")

    if download_path.suffix.lower() == ".txt":
        if not download_path.exists() or download_path.stat().st_size < 10:
            raise RuntimeError(
                f"скачанный txt пустой или повреждён: {download_path}"
            )

    return reply


async def run_under_xlsx_lock(
    project_id: int,
    step: str,
    fn: Callable[[], Awaitable[T]],
) -> T:
    """Per-(project, step) lock — как bot._run_xlsx_with_lock."""
    from app.services.xlsx_flow_locks import (
        register_xlsx_flow_task,
        unregister_xlsx_flow_task,
        xlsx_flow_active_set,
    )

    active = xlsx_flow_active_set()
    key = (project_id, step)
    active.add(key)
    task = asyncio.current_task()
    if task is not None:
        register_xlsx_flow_task(project_id, step, task)
    try:
        return await fn()
    finally:
        active.discard(key)
        unregister_xlsx_flow_task(project_id, step)
