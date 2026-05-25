"""Полная логика xlsx-шагов ChatGPT — единый источник для TG-бота и воркера.

Telegram `_run_*_xlsx` и orchestrator steps вызывают одни и те же функции.
GPT-сессия (browser → new_conversation → ask_with_files → download) — в
`xlsx_gpt_flow`; здесь — подготовка файлов, валидация, backup/replace, sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services import xlsx_gpt_flow as xgf
from app.services.xlsx_versioning import backup_to_old, replace_with, validate_xlsx
from app.storage import for_project as _sheet_for_project


@dataclass
class XlsxRoundtripResult:
    """Результат GPT round-trip с xlsx."""

    reply_text: str
    downloaded_path: Path
    project_xlsx: Path
    backup_path: Path | None = None


def _ts() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _ensure_project_xlsx(project: Project) -> Path:
    proj_xlsx = project.data_dir / "project.xlsx"
    if proj_xlsx.exists():
        return proj_xlsx
    sheet = _sheet_for_project(project)
    proj_xlsx = sheet.ensure_initialized(
        project_id=project.id, slug=project.slug
    )
    if not proj_xlsx.exists():
        raise FileNotFoundError(f"project.xlsx не найден: {proj_xlsx}")
    return proj_xlsx


async def run_plan_xlsx(
    project: Project,
    *,
    topic: str | None = None,
    project_id: int | None = None,
) -> XlsxRoundtripResult:
    """Шаг «План»: prompt.md + project.xlsx → обновлённый xlsx."""
    proj_xlsx = _ensure_project_xlsx(project)
    actual_topic = topic if topic is not None else (project.topic or "")

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_plan_prompt_file(
        project, tmp_dir, topic=actual_topic, ts=ts
    )
    chat_msg = cx.chat_message(
        project, "plan", topic=actual_topic, prompt_file_name=prompt_file.name
    )
    downloaded = tmp_dir / f"plan_{ts}.xlsx"

    logger.info(
        "plan_xlsx: prompt_file={} ({} байт), xlsx={}, chat_len={}",
        prompt_file.name,
        prompt_file.stat().st_size,
        proj_xlsx,
        len(chat_msg),
    )

    async def _gpt() -> str:
        return await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx],
            downloaded,
            project_id=project_id or project.id,
            validate_xlsx_download=True,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "plan", _gpt)

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        raise RuntimeError(f"скачанный xlsx невалиден: {validation_err}")

    backup = backup_to_old(proj_xlsx)
    replace_with(proj_xlsx, downloaded)
    return XlsxRoundtripResult(
        reply_text=reply,
        downloaded_path=downloaded,
        project_xlsx=proj_xlsx,
        backup_path=backup,
    )


async def run_script_xlsx(
    project: Project,
    *,
    project_id: int | None = None,
) -> tuple[XlsxRoundtripResult, str]:
    """Шаг «Закадровый текст»: prompt.txt + project.xlsx → voiceover.txt."""
    proj_xlsx = _ensure_project_xlsx(project)
    voiceover_path = proj_xlsx.parent / "voiceover.txt"

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_script_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project, "script", prompt_file_name=prompt_file.name
    )
    downloaded = tmp_dir / f"voiceover_{ts}.txt"

    logger.info(
        "script_xlsx: prompt_file={} ({} байт), xlsx={}, chat_len={}",
        prompt_file.name,
        prompt_file.stat().st_size,
        proj_xlsx,
        len(chat_msg),
    )

    async def _gpt() -> str:
        return await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx],
            downloaded,
            project_id=project_id or project.id,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "script", _gpt)

    if not downloaded.exists() or downloaded.stat().st_size < 10:
        raise RuntimeError(
            f"скачанный txt пустой или повреждён: {downloaded}"
        )

    voiceover_text = downloaded.read_text(encoding="utf-8").strip()
    cx.save_voiceover_text(project, voiceover_path, voiceover_text)

    return (
        XlsxRoundtripResult(
            reply_text=reply,
            downloaded_path=downloaded,
            project_xlsx=proj_xlsx,
        ),
        voiceover_text,
    )


async def run_split_xlsx(
    project: Project,
    *,
    project_id: int | None = None,
) -> XlsxRoundtripResult:
    """Шаг «Разбивка»: prompt + project.xlsx + voiceover.txt → xlsx."""
    proj_xlsx = _ensure_project_xlsx(project)
    voiceover = proj_xlsx.parent / "voiceover.txt"
    if not voiceover.exists() and project.script_text:
        voiceover.write_text(project.script_text.strip(), encoding="utf-8")
    if not voiceover.exists():
        raise FileNotFoundError(
            "voiceover.txt не найден — сначала пройди шаг «Закадровый текст»"
        )

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_split_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project, "split", prompt_file_name=prompt_file.name
    )
    downloaded = tmp_dir / f"split_{ts}.xlsx"

    logger.info(
        "split_xlsx: prompt={}, xlsx={}, voiceover={}, chat_len={}",
        prompt_file.name,
        proj_xlsx.name,
        voiceover.name,
        len(chat_msg),
    )

    async def _gpt() -> str:
        return await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx, voiceover],
            downloaded,
            project_id=project_id or project.id,
            validate_xlsx_download=True,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "split", _gpt)

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        raise RuntimeError(f"скачанный xlsx невалиден: {validation_err}")

    backup = backup_to_old(proj_xlsx)
    replace_with(proj_xlsx, downloaded)
    return XlsxRoundtripResult(
        reply_text=reply,
        downloaded_path=downloaded,
        project_xlsx=proj_xlsx,
        backup_path=backup,
    )


async def run_img_pr_xlsx(
    project: Project,
    *,
    n_frames: int | None = None,
    project_id: int | None = None,
) -> XlsxRoundtripResult:
    """Шаг «Промты картинок»: prompt.md + project.xlsx → xlsx с image_prompt."""
    proj_xlsx = _ensure_project_xlsx(project)

    ts = _ts()
    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = cx.write_img_pr_prompt_file(project, tmp_dir, ts=ts)
    chat_msg = cx.chat_message(
        project,
        "img_pr",
        prompt_file_name=prompt_file.name,
        n_frames=n_frames or 0,
    )
    downloaded = tmp_dir / f"img_pr_{ts}.xlsx"

    logger.info(
        "img_pr_xlsx: prompt_file={} ({} байт), xlsx={}, chat_len={}",
        prompt_file.name,
        prompt_file.stat().st_size,
        proj_xlsx,
        len(chat_msg),
    )

    async def _gpt() -> str:
        return await xgf.telegram_style_ask_and_download(
            chat_msg,
            [prompt_file, proj_xlsx],
            downloaded,
            project_id=project_id or project.id,
            validate_xlsx_download=True,
        )

    reply = await xgf.run_under_xlsx_lock(project.id, "img_pr", _gpt)

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        raise RuntimeError(f"скачанный xlsx невалиден: {validation_err}")

    backup = backup_to_old(proj_xlsx)
    replace_with(proj_xlsx, downloaded)
    return XlsxRoundtripResult(
        reply_text=reply,
        downloaded_path=downloaded,
        project_xlsx=proj_xlsx,
        backup_path=backup,
    )


async def sync_after_plan(
    session: AsyncSession, project: Project, xlsx_path: Path
) -> None:
    await cx.sync_project_xlsx(session, project, xlsx_path, keep_fields=False)
    plan_text = (project.general_plan or "").strip()
    if len(plan_text) < 200:
        raise RuntimeError(
            "ChatGPT вернул пустой/слишком короткий план после xlsx-sync"
        )


async def sync_after_split(
    session: AsyncSession, project: Project, xlsx_path: Path
) -> None:
    await cx.sync_project_xlsx(
        session,
        project,
        xlsx_path,
        keep_fields=False,
        update_frames_voiceover=True,
    )


async def sync_after_img_pr(
    session: AsyncSession, project: Project, xlsx_path: Path
) -> None:
    await cx.sync_project_xlsx(session, project, xlsx_path, keep_fields=False)


def set_status_if_behind(
    project: Project, target: ProjectStatus
) -> None:
    """Ставит статус, если текущий «ниже» target (как в bot после xlsx)."""
    from app.telegram.menu import status_order as _ord

    if _ord(project.status) < _ord(target):
        project.status = target
