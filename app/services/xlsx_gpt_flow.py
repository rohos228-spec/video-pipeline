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
from app.services.xlsx_versioning import (
    replace_with_backup,
    validate_xlsx,
)

T = TypeVar("T")

# Plan/split/enrich: GPT с xlsx часто отвечает >15 мин.
XLSX_GPT_TIMEOUT_S = 1800.0  # 30 мин


async def telegram_style_ask_with_files(
    chat_msg: str,
    attachments: list[Path],
    *,
    timeout: float = XLSX_GPT_TIMEOUT_S,
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
    ask_timeout: float = XLSX_GPT_TIMEOUT_S,
    download_timeout: float = XLSX_GPT_TIMEOUT_S,
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
            expect_file_download=True,
        )
        logger.info("xlsx-gpt-flow: GPT reply len={}", len(reply or ""))
        target = Path(download_path)
        # Никогда не пишем GPT-скачивание прямо в project.xlsx — при сбое
        # остаётся битый zip и падает весь пайплайн (BadZipFile).
        dl_path = target
        if validate_xlsx_download and target.suffix.lower() == ".xlsx":
            dl_path = target.with_name(f".gpt_dl_{target.stem}.xlsx")
            if dl_path.exists():
                dl_path.unlink()
        logger.info("xlsx-gpt-flow: скачиваю вложение → {}", dl_path.name)
        await gpt.download_attachment_from_last_reply(
            dl_path,
            timeout=download_timeout,
            fallback_text=reply,
        )

    if validate_xlsx_download:
        err = validate_xlsx(dl_path)
        if err is not None:
            if dl_path != target and dl_path.exists():
                dl_path.unlink()
            raise RuntimeError(f"скачанный xlsx невалиден: {err}")
        if dl_path != target:
            replace_with_backup(target, dl_path)
            try:
                dl_path.unlink()
            except OSError:
                pass
            logger.info("xlsx-gpt-flow: project.xlsx обновлён (с бэкапом)")

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
