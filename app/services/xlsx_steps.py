"""Шаги «План», «Закадровый текст», «Разбивка на блоки» через xlsx-flow.

В одиночной генерации эти шаги делаются через TG-handler'ы (`_run_plan_xlsx`,
`_run_script_xlsx`, `_run_split_xlsx` в `app/telegram/bot.py`): юзер тыкает
кнопку → бот открывает новый чат ChatGPT, прикладывает `project.xlsx` (+
`voiceover.txt` для split) + промт-файл, скачивает ответ и подменяет файл.

В массовой генерации тот же шаг раньше шёл через **другую** (легаси) ветку
кода — `app/orchestrator/steps/make_plan.py` и т.п., где в чат уходил только
текст. Это создавало расхождение: юзер настроил xlsx-промты под одиночный
flow, а массовый их вообще не использовал.

Этот модуль — общая реализация xlsx-flow, которую дёргают **оба** пути:
TG-handler (для одиночного) и orchestrator-step (для массового). На вход
шаги принимают `Project` (через AsyncSession) и `Bot`; вывод (статус /
ошибки / итоговый файл) идёт в TG-owner через `bot.send_message(...)`.

Никаких HITL — auto_advance / auto_review подхватывают новый статус
автоматически (`plan_ready` / `script_ready` / `frames_ready`).
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import Project, ProjectStatus
from app.services import gpt_text_builder as gtb
from app.services import prompt_library as plib
from app.services.prompt_library import get_project_prompt
from app.services.xlsx_versioning import (
    backup_to_old,
    replace_with,
    validate_xlsx,
)
from app.settings import settings
from app.storage import for_project as _sheet_for_project


def _ensure_project_xlsx(project: Project) -> Path:
    """Гарантирует наличие project.xlsx для проекта.

    `for_project()` создаёт файл из шаблона при первом обращении —
    нужно вызвать его явно, иначе xlsx-flow упадёт с RuntimeError на
    первом запуске массового sub'а.
    """
    _sheet_for_project(project)  # side-effect: ensure_initialized
    return project.data_dir / "project.xlsx"


async def _notify(bot: Bot, text: str, parse_mode: str | None = "HTML") -> None:
    """Шлёт служебное сообщение в TG-owner. Не падает при ошибке отправки."""
    try:
        await bot.send_message(
            settings.telegram_owner_chat_id, text, parse_mode=parse_mode
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("xlsx_steps: notify failed: {}", e)


async def _notify_doc(bot: Bot, path: Path, caption: str) -> None:
    """Шлёт документ в TG-owner. Не падает при ошибке отправки."""
    try:
        await bot.send_document(
            settings.telegram_owner_chat_id,
            FSInputFile(str(path)),
            caption=caption,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("xlsx_steps: notify_doc failed: {}", e)


# ---------------------------------------------------------------------------
# План — копия логики `_run_plan_xlsx` (TG callback) без msg-зависимости.
# ---------------------------------------------------------------------------


async def run_plan_xlsx_step(
    session: AsyncSession, project: Project, bot: Bot
) -> None:
    """Шаг «План» через xlsx-flow для массовой генерации.

    Делает то же, что `_run_plan_xlsx` в bot.py:
      1. Берёт `project.xlsx` и текущий выбранный промт.
      2. Открывает новый чат ChatGPT, прикладывает xlsx + промт-файл.
      3. Качает xlsx из ответа GPT, бэкапит старый, подменяет.
      4. Импортирует xlsx → БД (`import_v8_xlsx` / `reload_from_xlsx`).
      5. Статус → `plan_ready`. Шлёт результат в TG-owner.
    """
    if project.status is not ProjectStatus.planning:
        return
    logger.info("[#{}] make_plan (xlsx-flow) starting: '{}'", project.id, project.topic)

    proj_xlsx = _ensure_project_xlsx(project)
    if not proj_xlsx.exists():
        raise RuntimeError(f"project.xlsx не найден: {proj_xlsx}")

    overrides = getattr(project, "prompt_overrides", None) or {}
    batch_slug = getattr(project, "batch_slug", None)
    prompt_name = plib.resolve_project_prompt_name(
        overrides, "plan", batch_slug=batch_slug,
    )

    try:
        master = get_project_prompt(project, "plan")
    except FileNotFoundError:
        master = (
            "# plan\n\n"
            "Мастер-промт для шага «План» ещё не настроен. "
            "Открой prompts/01_plan/default.md и опиши там задачу."
        )

    topic = project.topic
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = proj_xlsx.parent / "tmp_gpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = out_dir / f"plan_{ts}.xlsx"

    prompt_file = out_dir / f"prompt_plan_{ts}.md"
    prompt_file.write_text(
        f"Тема ролика: {topic}\n\n{master.strip()}", encoding="utf-8"
    )

    accompanying = gtb.get_effective_text(
        project, "plan", topic=topic, prompt_file_name=prompt_file.name
    )

    await _notify(
        bot,
        f"▶ <b>План</b> (xlsx-flow, массовый)\n"
        f"Проект #{project.id} «{topic}»\n"
        f"Промт: <code>{prompt_name}</code>\n\n"
        "Открываю ChatGPT, прикрепляю xlsx + промт-файл, жду ответ.",
    )

    backup: Path | None = None
    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            reply = await gpt.ask_with_files(
                accompanying.strip(),
                [prompt_file, proj_xlsx],
                timeout=900,
            )
            logger.info(
                "plan_xlsx[#{}]: GPT reply len={} (prompt={})",
                project.id,
                len(reply or ""),
                prompt_name,
            )
            await gpt.download_attachment_from_last_reply(
                downloaded, timeout=900
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("plan_xlsx[#{}] failed: {}", project.id, e)
        await _notify(
            bot,
            f"❌ План (project #{project.id}): ChatGPT вернул ошибку: {e}",
        )
        raise

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        msg_err = f"ChatGPT прислал невалидный xlsx: {validation_err}"
        logger.warning("plan_xlsx[#{}]: {}", project.id, msg_err)
        await _notify(bot, f"❌ План (project #{project.id}): {msg_err}")
        raise RuntimeError(msg_err)

    try:
        backup = backup_to_old(proj_xlsx)
        replace_with(proj_xlsx, downloaded)
    except Exception as e:  # noqa: BLE001
        logger.exception("plan_xlsx[#{}] replace failed: {}", project.id, e)
        await _notify(bot, f"❌ Не смог подменить project.xlsx: {e}")
        raise

    from app.services.xlsx_sync import reload_from_xlsx
    from app.services.xlsx_v8_import import import_v8_xlsx

    try:
        info_v8 = await import_v8_xlsx(
            session, project, proj_xlsx, keep_fields=False
        )
        logger.info("plan_xlsx[#{}]: v8 import → {}", project.id, info_v8)
    except Exception as e:  # noqa: BLE001
        logger.warning("plan_xlsx[#{}]: v8 import failed: {}", project.id, e)
    try:
        info = await reload_from_xlsx(session, project, proj_xlsx)
        logger.info("plan_xlsx[#{}]: v7 reload → {}", project.id, info)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "plan_xlsx[#{}]: v7 reload_from_xlsx failed: {}", project.id, e
        )

    project.status = ProjectStatus.plan_ready
    await session.flush()

    backup_note = (
        f"\nПредыдущая версия: <code>old/{backup.name}</code>"
        if backup is not None
        else ""
    )
    await _notify(
        bot,
        f"✅ План готов (project #{project.id}). project.xlsx обновлён.{backup_note}",
    )
    await _notify_doc(
        bot,
        proj_xlsx,
        f"project.xlsx — план (массовый, promt «{prompt_name}»)",
    )


# ---------------------------------------------------------------------------
# Закадровый текст — копия логики `_run_script_xlsx`.
# ---------------------------------------------------------------------------


async def run_script_xlsx_step(
    session: AsyncSession, project: Project, bot: Bot
) -> None:
    """Шаг «Закадровый текст» через xlsx-flow для массовой генерации."""
    if project.status is not ProjectStatus.scripting:
        return
    logger.info("[#{}] make_script (xlsx-flow) starting", project.id)

    proj_xlsx = _ensure_project_xlsx(project)
    if not proj_xlsx.exists():
        raise RuntimeError(f"project.xlsx не найден: {proj_xlsx}")

    overrides = getattr(project, "prompt_overrides", None) or {}
    batch_slug = getattr(project, "batch_slug", None)
    prompt_name = plib.resolve_project_prompt_name(
        overrides, "script", batch_slug=batch_slug,
    )
    try:
        prompt_text = get_project_prompt(project, "script").strip()
    except FileNotFoundError as e:
        raise RuntimeError(f"Файл промта script не найден: {e}") from e

    topic = project.topic
    voiceover = proj_xlsx.parent / "voiceover.txt"
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = proj_xlsx.parent / "tmp_gpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = out_dir / f"voiceover_{ts}.txt"

    prompt_file = out_dir / f"prompt_script_{ts}.txt"
    prompt_file.write_text(
        f"# Инструкция для GPT (шаг 2 «Закадровый текст»)\n"
        f"# Тема ролика: «{topic}»\n\n"
        f"{prompt_text}\n",
        encoding="utf-8",
    )

    chat_msg = gtb.get_effective_text(
        project, "script", prompt_file_name=prompt_file.name
    )

    await _notify(
        bot,
        f"▶ <b>Закадровый текст</b> (xlsx-flow, массовый)\n"
        f"Проект #{project.id} «{topic}»\n"
        f"Промт: <code>{prompt_name}</code>\n\n"
        "Открываю ChatGPT, прикрепляю <code>prompt.txt</code> + "
        "<code>project.xlsx</code>, жду ответ.",
    )

    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            reply_text = await gpt.ask_with_files(
                chat_msg, [prompt_file, proj_xlsx], timeout=900
            )
            logger.info(
                "script_xlsx[#{}]: GPT reply len={} (prompt={})",
                project.id,
                len(reply_text or ""),
                prompt_name,
            )
            await gpt.download_attachment_from_last_reply(
                downloaded, timeout=900
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("script_xlsx[#{}] failed: {}", project.id, e)
        await _notify(
            bot,
            f"❌ Закадр. текст (project #{project.id}): ChatGPT вернул ошибку: {e}",
        )
        raise

    if not downloaded.exists() or downloaded.stat().st_size < 10:
        msg_err = f"Скачанный txt пустой или повреждён: {downloaded}"
        await _notify(bot, f"❌ Закадр. текст (project #{project.id}): {msg_err}")
        raise RuntimeError(msg_err)

    backup: Path | None = None
    try:
        if voiceover.exists():
            old_dir = voiceover.parent / "old"
            old_dir.mkdir(parents=True, exist_ok=True)
            backup = old_dir / f"{ts}_voiceover.txt"
            shutil.copy2(voiceover, backup)
        shutil.copy2(downloaded, voiceover)
    except Exception as e:  # noqa: BLE001
        logger.exception("script_xlsx[#{}] replace failed: {}", project.id, e)
        await _notify(bot, f"❌ Не смог записать voiceover.txt: {e}")
        raise

    voiceover_text = ""
    try:
        voiceover_text = voiceover.read_text(encoding="utf-8").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "script_xlsx[#{}]: не смог прочитать voiceover.txt: {}",
            project.id,
            e,
        )

    if voiceover_text:
        project.script_text = voiceover_text
        logger.info(
            "script_xlsx[#{}]: project.script_text сохранён ({} симв)",
            project.id,
            len(voiceover_text),
        )
    project.status = ProjectStatus.script_ready
    await session.flush()

    backup_note = (
        f"\nПредыдущая версия: <code>old/{backup.name}</code>"
        if backup is not None
        else ""
    )
    await _notify(
        bot,
        f"✅ Закадр. текст готов (project #{project.id}). "
        f"voiceover.txt — {voiceover.stat().st_size} байт.{backup_note}",
    )
    await _notify_doc(
        bot,
        voiceover,
        f"voiceover.txt — закадровый текст (массовый, promt «{prompt_name}»)",
    )


# ---------------------------------------------------------------------------
# Разбивка на блоки — копия логики `_run_split_xlsx`.
# ---------------------------------------------------------------------------


async def run_split_xlsx_step(
    session: AsyncSession, project: Project, bot: Bot
) -> None:
    """Шаг «Разбивка на блоки» через xlsx-flow для массовой генерации."""
    if project.status is not ProjectStatus.splitting:
        return
    logger.info("[#{}] split_frames (xlsx-flow) starting", project.id)

    proj_xlsx = _ensure_project_xlsx(project)
    if not proj_xlsx.exists():
        raise RuntimeError(f"project.xlsx не найден: {proj_xlsx}")
    voiceover = proj_xlsx.parent / "voiceover.txt"
    if not voiceover.exists():
        raise RuntimeError(
            f"voiceover.txt не найден: {voiceover}. "
            "Сначала пройди Шаг 2 «Закадровый текст»."
        )

    overrides = getattr(project, "prompt_overrides", None) or {}
    batch_slug = getattr(project, "batch_slug", None)
    prompt_name = plib.resolve_project_prompt_name(
        overrides, "split", batch_slug=batch_slug,
    )
    try:
        prompt_text = get_project_prompt(project, "split").strip()
    except FileNotFoundError as e:
        raise RuntimeError(f"Файл промта split не найден: {e}") from e

    topic = project.topic
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_dir = proj_xlsx.parent / "tmp_gpt"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded = out_dir / f"split_{ts}.xlsx"

    prompt_file = out_dir / f"prompt_split_{ts}.txt"
    prompt_file.write_text(
        f"# Инструкция для GPT (шаг 3 «Разбивка на блоки»)\n"
        f"# Тема ролика: «{topic}»\n\n"
        f"{prompt_text}\n",
        encoding="utf-8",
    )

    chat_msg = gtb.get_effective_text(
        project, "split", prompt_file_name=prompt_file.name
    )

    await _notify(
        bot,
        f"▶ <b>Разбивка на блоки</b> (xlsx-flow, массовый)\n"
        f"Проект #{project.id} «{topic}»\n"
        f"Промт: <code>{prompt_name}</code>\n\n"
        "Открываю ChatGPT, прикрепляю prompt.txt + project.xlsx + voiceover.txt, "
        "жду обновлённый xlsx.",
    )

    backup: Path | None = None
    try:
        async with browser_session() as bs:
            gpt = ChatGPTBot(bs)
            await gpt.new_conversation()
            reply = await gpt.ask_with_files(
                chat_msg,
                [prompt_file, proj_xlsx, voiceover],
                timeout=900,
            )
            logger.info(
                "split_xlsx[#{}]: GPT reply len={} (prompt={})",
                project.id,
                len(reply or ""),
                prompt_name,
            )
            await gpt.download_attachment_from_last_reply(
                downloaded, timeout=900
            )
    except Exception as e:  # noqa: BLE001
        logger.exception("split_xlsx[#{}] failed: {}", project.id, e)
        await _notify(
            bot,
            f"❌ Разбивка (project #{project.id}): ChatGPT вернул ошибку: {e}",
        )
        raise

    validation_err = validate_xlsx(downloaded)
    if validation_err is not None:
        msg_err = f"ChatGPT прислал невалидный xlsx: {validation_err}"
        logger.warning("split_xlsx[#{}]: {}", project.id, msg_err)
        await _notify(bot, f"❌ Разбивка (project #{project.id}): {msg_err}")
        raise RuntimeError(msg_err)

    try:
        backup = backup_to_old(proj_xlsx)
        replace_with(proj_xlsx, downloaded)
    except Exception as e:  # noqa: BLE001
        logger.exception("split_xlsx[#{}] replace failed: {}", project.id, e)
        await _notify(bot, f"❌ Не смог подменить project.xlsx: {e}")
        raise

    from app.services.xlsx_sync import reload_from_xlsx
    from app.services.xlsx_v8_import import import_v8_xlsx

    try:
        info_v8 = await import_v8_xlsx(
            session,
            project,
            proj_xlsx,
            keep_fields=False,
            update_frames_voiceover=True,
        )
        logger.info("split_xlsx[#{}]: v8 import → {}", project.id, info_v8)
    except Exception as e:  # noqa: BLE001
        logger.warning("split_xlsx[#{}]: v8 import failed: {}", project.id, e)
    try:
        info = await reload_from_xlsx(session, project, proj_xlsx)
        logger.info("split_xlsx[#{}]: v7 reload → {}", project.id, info)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "split_xlsx[#{}]: v7 reload_from_xlsx failed: {}", project.id, e
        )

    project.status = ProjectStatus.frames_ready
    await session.flush()

    backup_note = (
        f"\nПредыдущая версия: <code>old/{backup.name}</code>"
        if backup is not None
        else ""
    )
    await _notify(
        bot,
        f"✅ Разбивка готова (project #{project.id}). project.xlsx обновлён.{backup_note}",
    )
    await _notify_doc(
        bot,
        proj_xlsx,
        f"project.xlsx — разбивка (массовый, promt «{prompt_name}»)",
    )
