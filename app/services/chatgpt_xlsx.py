"""Общие хелперы xlsx-flow для шагов ChatGPT.

Правило отправки (как в Telegram-боте):
  - Мастер-промт (выбранный variant с диска) → всегда во временный файл.
  - Excel и прочие вложения → файлами в одном сообщении.
  - Сопр. текст (`gpt_text_builder.get_effective_text`) → в композер ChatGPT.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.chatgpt import ChatGPTBot
from app.models import Project
from app.services import gpt_text_builder as gtb
from app.services.prompt_library import get_project_prompt
from app.services.xlsx_sync import reload_from_xlsx
from app.services.xlsx_v8_import import SHEET_PLAN_V8, import_v8_xlsx
from app.services.xlsx_versioning import backup_to_old, replace_with, validate_xlsx


def tmp_gpt_dir(project: Project) -> Path:
    d = project.data_dir / "tmp_gpt"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _timestamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _get_master_or_fallback(project: Project, step_code: str, fallback: str) -> str:
    try:
        return get_project_prompt(project, step_code).strip()
    except FileNotFoundError:
        return fallback


def write_plan_prompt_file(
    project: Project,
    tmp_dir: Path,
    *,
    topic: str | None = None,
    ts: str | None = None,
) -> Path:
    actual_topic = topic if topic is not None else (project.topic or "")
    master = _get_master_or_fallback(
        project,
        "plan",
        "# plan\n\nМастер-промт для шага «План» ещё не настроен.",
    )
    hero_hint = {
        "hero": (
            "Игнорируй автоматическое определение hero_needed, "
            "выставь hero_needed=true."
        ),
        "no_hero": (
            "Игнорируй автоматическое определение hero_needed, "
            "выставь hero_needed=false."
        ),
        "auto": "",
    }.get(project.hero_mode, "")
    extra = f"\n\nДополнительное указание: {hero_hint}" if hero_hint else ""
    prompt_file = tmp_dir / f"prompt_plan_{ts or _timestamp()}.md"
    prompt_file.write_text(
        f"Тема ролика: {actual_topic}\n\n{master}{extra}",
        encoding="utf-8",
    )
    return prompt_file


def write_script_prompt_file(
    project: Project, tmp_dir: Path, *, ts: str | None = None
) -> Path:
    topic = (project.topic or "").strip()
    prompt_text = _get_master_or_fallback(
        project,
        "script",
        "Мастер-промт для шага «Закадровый текст» ещё не настроен.",
    )
    prompt_file = tmp_dir / f"prompt_script_{ts or _timestamp()}.txt"
    prompt_file.write_text(
        f"# Инструкция для GPT (шаг 2 «Закадровый текст»)\n"
        f"# Тема ролика: «{topic}»\n\n"
        f"{prompt_text}\n",
        encoding="utf-8",
    )
    return prompt_file


def write_split_prompt_file(
    project: Project, tmp_dir: Path, *, ts: str | None = None
) -> Path:
    topic = (project.topic or "").strip()
    prompt_text = _get_master_or_fallback(
        project,
        "split",
        "Мастер-промт для шага «Разбивка на блоки» ещё не настроен.",
    )
    prompt_file = tmp_dir / f"prompt_split_{ts or _timestamp()}.txt"
    prompt_file.write_text(
        f"# Инструкция для GPT (шаг 3 «Разбивка на блоки»)\n"
        f"# Тема ролика: «{topic}»\n\n"
        f"{prompt_text}\n",
        encoding="utf-8",
    )
    return prompt_file


def write_img_pr_prompt_file(
    project: Project, tmp_dir: Path, *, ts: str | None = None
) -> Path:
    master = _get_master_or_fallback(
        project,
        "img_pr",
        "# img_pr\n\nМастер-промт для шага «Промты картинок» ещё не настроен.",
    )
    prompt_file = tmp_dir / f"prompt_img_pr_{ts or _timestamp()}.md"
    prompt_file.write_text(master, encoding="utf-8")
    return prompt_file


def chat_message(project: Project, step_code: str, **ctx) -> str:
    """Текст сообщения в ChatGPT (без мастер-промта)."""
    return gtb.get_effective_text(project, step_code, **ctx).strip()


async def ask_with_prompt_files(
    gpt: ChatGPTBot,
    chat_msg: str,
    attachments: list[Path],
    *,
    timeout: int = 900,
    project_id: int | None = None,
    step_code: str = "step",
) -> str:
    """Новый чат: вложения (промт + xlsx + …) + сопр. текст в композер."""
    await gpt.new_conversation()
    return await gpt.ask_with_files(
        chat_msg,
        attachments,
        timeout=timeout,
        project_id=project_id,
    )


async def download_and_replace_xlsx(
    gpt: ChatGPTBot,
    project_xlsx: Path,
    download_path: Path,
    *,
    timeout: int = 600,
) -> None:
    await gpt.download_attachment_from_last_reply(download_path, timeout=timeout)
    validation_err = validate_xlsx(download_path)
    if validation_err is not None:
        raise RuntimeError(f"скачанный xlsx невалиден: {validation_err}")
    backup_to_old(project_xlsx)
    replace_with(project_xlsx, download_path)


async def download_text_attachment(
    gpt: ChatGPTBot,
    target: Path,
    *,
    timeout: int = 900,
) -> str:
    await gpt.download_attachment_from_last_reply(target, timeout=timeout)
    if not target.exists() or target.stat().st_size < 10:
        raise RuntimeError(f"скачанный текстовый файл пустой: {target}")
    return target.read_text(encoding="utf-8").strip()


async def sync_project_xlsx(
    session: AsyncSession,
    project: Project,
    xlsx_path: Path,
    *,
    keep_fields: bool = False,
    update_frames_voiceover: bool = False,
) -> dict | None:
    """Импортирует project.xlsx в БД (v8 + fallback v7)."""
    sync_info: dict | None = None
    try:
        from openpyxl import load_workbook

        wb = load_workbook(
            filename=str(xlsx_path), data_only=True, read_only=True
        )
        is_v8 = SHEET_PLAN_V8 in wb.sheetnames
        wb.close()
    except Exception as e:  # noqa: BLE001
        is_v8 = False
        logger.warning(
            "[#{}] sync_project_xlsx: cannot peek sheet names: {}",
            project.id,
            e,
        )

    if is_v8:
        try:
            sync_info = await import_v8_xlsx(
                session,
                project,
                xlsx_path,
                keep_fields=keep_fields,
                update_frames_voiceover=update_frames_voiceover,
            )
            logger.info("[#{}] sync_project_xlsx v8: {}", project.id, sync_info)
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] sync_project_xlsx v8 failed: {}", project.id, e
            )
    try:
        info_v7 = await reload_from_xlsx(session, project, xlsx_path)
        logger.info("[#{}] sync_project_xlsx v7: {}", project.id, info_v7)
        if sync_info is None:
            sync_info = info_v7
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] sync_project_xlsx v7 failed: {}", project.id, e)
    return sync_info


def save_voiceover_text(project: Project, voiceover_path: Path, text: str) -> None:
    """Сохраняет voiceover.txt с бэкапом предыдущей версии."""
    voiceover_path.parent.mkdir(parents=True, exist_ok=True)
    if voiceover_path.exists():
        old_dir = voiceover_path.parent / "old"
        old_dir.mkdir(parents=True, exist_ok=True)
        backup = old_dir / f"{_timestamp()}_voiceover.txt"
        shutil.copy2(voiceover_path, backup)
    voiceover_path.write_text(text, encoding="utf-8")
