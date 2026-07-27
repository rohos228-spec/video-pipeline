"""Единая GPT/xlsx-сессия — используется `xlsx_step_runners` (bot + worker).

Транспорт: HTTP API (`gpt_client.ApiGptClient`). Браузерный ChatGPT для
текста/xlsx отключён — паритет контракта ask → download → validate/normalize.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from loguru import logger

from app.services.xlsx_versioning import (
    normalize_xlsx_to_reference_layout,
    replace_with_backup,
    validate_xlsx,
)

T = TypeVar("T")

# Plan/split/enrich: большие xlsx — долгий ответ модели.
XLSX_GPT_TIMEOUT_S = 1800.0  # 30 мин


async def telegram_style_ask_with_files(
    chat_msg: str,
    attachments: list[Path],
    *,
    timeout: float = XLSX_GPT_TIMEOUT_S,
    project_id: int | None = None,
) -> str:
    """API: ask_with_files (как раньше bot.py через CDP)."""
    from app.services.gpt_client import get_gpt_client

    for fp in attachments:
        if not fp.exists():
            raise FileNotFoundError(f"xlsx-gpt-flow: файл не найден {fp}")

    names = ", ".join(p.name for p in attachments)
    stripped = (chat_msg or "").strip()
    logger.info(
        "xlsx-gpt-flow/api: ask_with_files files=[{}] chat_len={}",
        names,
        len(stripped),
    )

    gpt = get_gpt_client()
    await gpt.new_conversation()
    reply = await gpt.ask_with_files(
        stripped,
        attachments,
        timeout=timeout,
        project_id=project_id,
        expect_file_download=False,
    )
    logger.info("xlsx-gpt-flow/api: GPT reply len={}", len(reply or ""))
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
    allow_reply_text_fallback: bool = False,
) -> str:
    """Как bot _run_plan_xlsx / _run_split_xlsx: ask → materialize artifact."""
    from app.services.gpt_client import get_gpt_client

    for fp in attachments:
        if not fp.exists():
            raise FileNotFoundError(f"xlsx-gpt-flow: файл не найден {fp}")

    names = ", ".join(p.name for p in attachments)
    stripped = (chat_msg or "").strip()
    logger.info(
        "xlsx-gpt-flow/api: ask+download files=[{}] → {}",
        names,
        download_path.name,
    )

    gpt = get_gpt_client()
    await gpt.new_conversation()
    reply = await gpt.ask_with_files(
        stripped,
        attachments,
        timeout=ask_timeout,
        project_id=project_id,
        expect_file_download=True,
    )
    logger.info("xlsx-gpt-flow/api: GPT reply len={}", len(reply or ""))
    target = Path(download_path)
    # Никогда не пишем GPT-результат прямо в project.xlsx до валидации.
    dl_path = target
    if validate_xlsx_download and target.suffix.lower() == ".xlsx":
        dl_path = target.with_name(f".gpt_dl_{target.stem}.xlsx")
        if dl_path.exists():
            dl_path.unlink()
    logger.info("xlsx-gpt-flow/api: materialize → {}", dl_path.name)
    await gpt.download_attachment_from_last_reply(
        dl_path,
        timeout=download_timeout,
        fallback_text=reply,
        allow_reply_text_fallback=allow_reply_text_fallback,
    )

    if validate_xlsx_download:
        ref_xlsx = next(
            (p for p in attachments if p.suffix.lower() == ".xlsx"),
            None,
        )
        err = validate_xlsx(dl_path)
        if err is not None and ref_xlsx is not None:
            if normalize_xlsx_to_reference_layout(dl_path, ref_xlsx):
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
            logger.info("xlsx-gpt-flow/api: {} обновлён (с бэкапом)", target.name)

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
    """Per-project GPT lock + per-step active marker (для ⏹ / is_generation_active)."""
    from app.services.xlsx_flow_locks import (
        project_gpt_lock,
        register_xlsx_flow_task,
        unregister_xlsx_flow_task,
        xlsx_flow_active_set,
    )

    active = xlsx_flow_active_set()
    key = (project_id, step)
    task = asyncio.current_task()
    async with project_gpt_lock(project_id):
        active.add(key)
        if task is not None:
            register_xlsx_flow_task(project_id, step, task)
        try:
            return await fn()
        finally:
            active.discard(key)
            unregister_xlsx_flow_task(project_id, step)
