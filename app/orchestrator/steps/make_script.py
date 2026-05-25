"""Шаг 2: общий план → закадровый текст (xlsx-flow, как Telegram _run_script_xlsx)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLKind, Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services import xlsx_gpt_flow as xgf
from app.services.hitl import send_hitl_text
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.scripting:
        return
    logger.info("[#{}] make_script (xlsx-flow) starting", project.id)

    proj_xlsx = project.data_dir / "project.xlsx"
    if not proj_xlsx.exists():
        sheet = _sheet_for_project(project)
        proj_xlsx = sheet.ensure_initialized(
            project_id=project.id, slug=project.slug
        )
    if not proj_xlsx.exists():
        raise RuntimeError(f"make_script: project.xlsx не найден: {proj_xlsx}")

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_script_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project, "script", prompt_file_name=prompt_file.name
    )
    voiceover_path = proj_xlsx.parent / "voiceover.txt"
    downloaded = tmp_dir / f"voiceover_{ts}.txt"

    async def _do() -> str:
        return await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx],
            downloaded,
            project_id=project.id,
        )

    await xgf.run_under_xlsx_lock(project.id, "script", _do)

    voiceover_text = downloaded.read_text(encoding="utf-8").strip()
    cx.save_voiceover_text(project, voiceover_path, voiceover_text)

    if len(voiceover_text) < 200:
        raise RuntimeError("ChatGPT вернул пустой/слишком короткий сценарий")

    project.script_text = voiceover_text
    project.status = ProjectStatus.script_ready
    await session.flush()

    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet status write failed: {}", project.id, e)

    await send_hitl_text(
        bot,
        session,
        project,
        kind=HITLKind.approve_script,
        title=f"Закадровый текст #{project.id}",
        text=voiceover_text,
        payload={"step": "script"},
    )
