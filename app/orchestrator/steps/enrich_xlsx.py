"""Шаг «Доп работа с EXCEL» — generic xlsx round-trip с ChatGPT.

Параметризован по slot_idx (1..5). Каждый слот — отдельный
мастер-промт (в `prompts/05<a..e>_enrich_<i>/`) и отдельный gpt_text
override (через `Project.gpt_text_overrides["enrich_<i>"]`).

Поток:
  1. Берём текущий `data/videos/<slug>/project.xlsx`.
  2. Открываем НОВЫЙ чат ChatGPT (без истории прошлых шагов).
  3. Аплоадим xlsx как вложение + шлём промт «мастер + сопровождающий
     текст».
  4. Ждём ответ. Скачиваем приложенный к ответу обновлённый xlsx и
     сохраняем поверх исходного `project.xlsx`.
  5. Если ChatGPT не приложил файл — повторяем (новый чат) до 3 раз.
  6. После успеха — `xlsx_sync.reload_from_xlsx()` → данные в БД,
     `recompute_status()` поднимет статус. И принудительно ставим
     статус `enrich_<i>_ready`.
"""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.models import Project, ProjectStatus
from app.services import gpt_text_builder as gtb
from app.services import xlsx_sync
from app.services.prompt_library import get_project_prompt
from app.services.xlsx_v8_import import SHEET_PLAN_V8, import_v8_xlsx
from app.storage import for_project as _sheet_for_project

# Маппинг slot_idx (1..5) → (running_status, ready_status, step_code).
_SLOT_MAP: dict[int, tuple[ProjectStatus, ProjectStatus, str]] = {
    1: (ProjectStatus.enriching_1, ProjectStatus.enrich_1_ready, "enrich_1"),
    2: (ProjectStatus.enriching_2, ProjectStatus.enrich_2_ready, "enrich_2"),
    3: (ProjectStatus.enriching_3, ProjectStatus.enrich_3_ready, "enrich_3"),
    4: (ProjectStatus.enriching_4, ProjectStatus.enrich_4_ready, "enrich_4"),
    5: (ProjectStatus.enriching_5, ProjectStatus.enrich_5_ready, "enrich_5"),
}

# Сколько раз пробуем round-trip, если ChatGPT не приложил файл в ответе.
_MAX_RETRIES = 3


def _resolve_slot_idx(status: ProjectStatus) -> int | None:
    """Из running-статуса (enriching_N) достаём slot_idx."""
    for idx, (running, _ready, _code) in _SLOT_MAP.items():
        if status is running:
            return idx
    return None


def _get_accompanying_text(project: Project, step_code: str) -> str:
    """Возвращает сопровождающий текст для слота: override юзера или дефолт.

    Источник дефолта — `gpt_text_builder.ENRICH_DEFAULT_ACCOMPANYING_TEXT`
    (через `get_effective_text`). Юзер редактирует через
    «✏️ Сопр. сообщение» в picker'е → запись попадает в
    `Project.gpt_text_overrides[step_code]`.
    """
    return gtb.get_effective_text(project, step_code)


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    slot_idx = _resolve_slot_idx(project.status)
    if slot_idx is None:
        logger.warning(
            "[#{}] enrich_xlsx.run: статус {} не соответствует ни одному слоту",
            project.id,
            project.status.value,
        )
        return
    running_status, ready_status, step_code = _SLOT_MAP[slot_idx]
    logger.info(
        "[#{}] enrich_xlsx slot={} (code={}) starting", project.id, slot_idx, step_code
    )

    # 1. Гарантируем существование xlsx (для свежих проектов).
    sheet = _sheet_for_project(project)
    xlsx_path: Path = sheet.ensure_initialized(
        project_id=project.id, slug=project.slug
    )
    if not xlsx_path.exists():
        raise RuntimeError(
            f"enrich_xlsx: project.xlsx не найден по пути {xlsx_path}"
        )

    # 2. Собираем промт: мастер-промт → файл, сопр. сообщение → текст в чате.
    try:
        master = get_project_prompt(project, step_code)
    except FileNotFoundError:
        master = (
            f"# {step_code}\n\n"
            "Мастер-промт для этого слота ещё не настроен. "
            "Открой `prompts/05*_enrich_<i>/default.md` и опиши там, "
            "что именно ChatGPT должен изменить в приложенном xlsx."
        )
    accompanying = _get_accompanying_text(project, step_code)

    # Мастер-промт пишем во временный файл и прикрепляем к чату.
    # В само сообщение идёт только «сопр. сообщение».
    tmp_dir = xlsx_path.parent / "tmp_gpt"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = tmp_dir / f"prompt_{step_code}.md"
    prompt_file.write_text(master.strip(), encoding="utf-8")

    # 3. Round-trip до 3 раз.
    from app.services.step_cancel import StepCancelledError, raise_if_cancelled

    last_err: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        raise_if_cancelled(project.id)
        logger.info(
            "[#{}] enrich_xlsx slot={} attempt {}/{}",
            project.id,
            slot_idx,
            attempt,
            _MAX_RETRIES,
        )
        try:
            async with browser_session() as bs:
                gpt = ChatGPTBot(bs)
                await gpt.new_conversation()
                # Шлём prompt-файл + xlsx как вложения, сопр. текст — в чат.
                reply = await gpt.ask_with_files(
                    accompanying.strip(),
                    [prompt_file, xlsx_path],
                    timeout=1200,
                    project_id=project.id,
                )
                logger.info(
                    "[#{}] enrich_xlsx: получен ответ len={} (try={})",
                    project.id,
                    len(reply or ""),
                    attempt,
                )
                # Скачиваем приложенный xlsx ПОВЕРХ исходного.
                # download_attachment_from_last_reply бросит исключение,
                # если ChatGPT не приложил файл.
                target = await gpt.download_attachment_from_last_reply(
                    xlsx_path, timeout=600
                )
                if not target.exists() or target.stat().st_size < 1024:
                    raise RuntimeError(
                        f"скачанный xlsx пустой/слишком маленький "
                        f"({target.stat().st_size if target.exists() else 0} байт)"
                    )
                logger.info(
                    "[#{}] enrich_xlsx: xlsx обновлён ({} байт)",
                    project.id,
                    target.stat().st_size,
                )
                break  # успех
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning(
                "[#{}] enrich_xlsx slot={} attempt {}/{} FAILED: {}",
                project.id,
                slot_idx,
                attempt,
                _MAX_RETRIES,
                e,
            )
            if attempt >= _MAX_RETRIES:
                # Откатим статус назад — пайплайн сам поднимет из данных.
                project.status = running_status  # оставляем running, чтобы юзер ткнул retry
                await session.flush()
                raise RuntimeError(
                    f"enrich_xlsx slot={slot_idx}: 3 попытки failed, last err: {e}"
                ) from e
            continue

    # 4. Reload xlsx → БД. Сначала смотрим формат: если есть лист «план» —
    # это v8, читаем им (он подтягивает image_prompt/animation_prompt из
    # R45/R48). Иначе fallback на старый xlsx_sync (лист «Кадры», R29).
    #
    # ROOT FIX: раньше тут вызывался ТОЛЬКО `xlsx_sync.reload_from_xlsx`,
    # который ищет лист «Кадры» и на v8-xlsx тихо ничего не делал. В
    # результате enrich-слот возвращал xlsx с заполненными промтами, но
    # в БД они не попадали → шаг 7 «Картинки» ругался «нет image_prompt»
    # хотя в xlsx промты были.
    sync_info: dict | None = None
    try:
        from openpyxl import load_workbook
        wb = load_workbook(filename=str(xlsx_path), data_only=True, read_only=True)
        is_v8 = SHEET_PLAN_V8 in wb.sheetnames
        wb.close()
    except Exception as e:  # noqa: BLE001
        is_v8 = False
        logger.warning(
            "[#{}] enrich_xlsx slot={} cannot peek sheet names: {}",
            project.id, slot_idx, e,
        )

    if is_v8:
        try:
            sync_info = await import_v8_xlsx(
                session,
                project,
                xlsx_path,
                keep_fields=False,
                update_frames_voiceover=True,
            )
            logger.info(
                "[#{}] enrich_xlsx slot={} import_v8_xlsx: {}",
                project.id, slot_idx, sync_info,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] enrich_xlsx slot={} import_v8_xlsx failed: {}",
                project.id, slot_idx, e,
            )
    else:
        try:
            sync_info = await xlsx_sync.reload_from_xlsx(session, project, xlsx_path)
            logger.info(
                "[#{}] enrich_xlsx slot={} reload_from_xlsx: {}",
                project.id, slot_idx, sync_info,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] enrich_xlsx slot={} reload_from_xlsx failed: {}",
                project.id, slot_idx, e,
            )

    # 5. Ставим статус slot_<i>_ready (но не перебиваем более продвинутый).
    from app.telegram.menu import status_order as _ord

    cur = project.status
    if _ord(cur) < _ord(ready_status):
        project.status = ready_status
        await session.flush()
        logger.info(
            "[#{}] enrich_xlsx slot={} → status={}",
            project.id,
            slot_idx,
            ready_status.value,
        )

    # 6. Auto-chain — если юзер запустил «▶▶ Запустить все слоты подряд»,
    # `project.meta['enrich_auto_chain_to']` хранит целевой номер слота
    # (1..5). После завершения этого слота, если есть следующий — сами
    # переводим статус в `enriching_<i+1>`. Воркер на следующем тике
    # подхватит и продолжит. При достижении target — чистим флаг.
    meta = dict(project.meta or {})
    chain_to = meta.get("enrich_auto_chain_to")
    if isinstance(chain_to, int) and chain_to > slot_idx:
        next_slot = slot_idx + 1
        next_running = _SLOT_MAP[next_slot][0]
        project.status = next_running
        await session.flush()
        logger.info(
            "[#{}] enrich_xlsx auto-chain: {} → {} (target slot #{})",
            project.id,
            ready_status.value,
            next_running.value,
            chain_to,
        )
    elif chain_to is not None:
        # Цепочка дошла до target (или вышла за неё). Снимаем флаг,
        # чтобы при ручном повторном запуске одного слота не было
        # неожиданного авто-перехода.
        meta.pop("enrich_auto_chain_to", None)
        project.meta = meta
        await session.flush()
        logger.info(
            "[#{}] enrich_xlsx auto-chain complete at slot #{} "
            "(target was #{}) — cleared meta flag",
            project.id,
            slot_idx,
            chain_to,
        )

    _ = last_err  # keep ref to silence "unused" warning in some linters
