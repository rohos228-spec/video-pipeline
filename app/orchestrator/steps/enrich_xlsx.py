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

from app.models import Project, ProjectStatus
from app.services import chatgpt_xlsx as cx
from app.services import gpt_text_builder as gtb
from app.services import xlsx_gpt_flow as xgf
from app.services import xlsx_sync
from app.services.prompt_library import read_resolved_project_prompt
from app.services.xlsx_v8_import import has_v8_plan_sheet, import_v8_xlsx
from app.services.xlsx_versioning import validate_xlsx
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

    await session.refresh(project)

    # 1. Гарантируем существование xlsx (для свежих проектов).
    sheet = _sheet_for_project(project)
    xlsx_path: Path = sheet.ensure_initialized(
        project_id=project.id, slug=project.slug
    )
    if not xlsx_path.exists():
        raise RuntimeError(
            f"enrich_xlsx: project.xlsx не найден по пути {xlsx_path}"
        )

    # 2. Мастер-промт → файл; сопр. сообщение → короткий текст в чате.
    try:
        variant, src_path, master = read_resolved_project_prompt(project, step_code)
        logger.info(
            "[#{}] enrich_xlsx slot={}: активный промт variant={!r} "
            "path={} ({} симв) overrides={!r}",
            project.id,
            slot_idx,
            variant,
            src_path,
            len(master or ""),
            (getattr(project, "prompt_overrides", None) or {}).get(step_code),
        )
    except FileNotFoundError:
        variant = "default"
        src_path = None
        master = (
            f"# {step_code}\n\n"
            "Мастер-промт для этого слота ещё не настроен. "
            "Открой `prompts/05*_enrich_<i>/default.md` и опиши там, "
            "что именно ChatGPT должен изменить в приложенном xlsx."
        )
        logger.warning(
            "[#{}] enrich_xlsx slot={}: файл промта не найден, fallback текст",
            project.id,
            slot_idx,
        )

    accompanying = _get_accompanying_text(project, step_code)

    from app.services import chatgpt_xlsx as cx

    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = tmp_dir / f"prompt_{step_code}_{variant}.md"
    prompt_file.write_text((master or "").strip(), encoding="utf-8")

    # 3. Round-trip до 3 раз.
    from app.services.step_cancel import StepCancelledError, raise_if_cancelled

    last_err: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        raise_if_cancelled(project.id)
        # ChatGPT re-attach читает те же пути — файл мог быть удалён
        # (параллельный шаг / purge tmp) пока ждали Send.
        prompt_file.write_text((master or "").strip(), encoding="utf-8")
        logger.info(
            "[#{}] enrich_xlsx slot={} attempt {}/{}",
            project.id,
            slot_idx,
            attempt,
            _MAX_RETRIES,
        )
        try:
            async def _do() -> str:
                return await xgf.telegram_style_ask_and_download(
                    accompanying.strip(),
                    [prompt_file, xlsx_path],
                    xlsx_path,
                    ask_timeout=1200,
                    download_timeout=600,
                    project_id=project.id,
                    validate_xlsx_download=True,
                )

            reply = await xgf.run_under_xlsx_lock(
                project.id, step_code, _do
            )
            logger.info(
                "[#{}] enrich_xlsx: получен ответ len={} (try={})",
                project.id,
                len(reply or ""),
                attempt,
            )
            if not xlsx_path.exists() or xlsx_path.stat().st_size < 1024:
                raise RuntimeError(
                    f"скачанный xlsx пустой/слишком маленький "
                    f"({xlsx_path.stat().st_size if xlsx_path.exists() else 0} байт)"
                )
            logger.info(
                "[#{}] enrich_xlsx: xlsx обновлён ({} байт)",
                project.id,
                xlsx_path.stat().st_size,
            )
            break  # успех
        except Exception as e:  # noqa: BLE001
            last_err = e
            xlsx_ok = (
                xlsx_path.exists()
                and xlsx_path.stat().st_size >= 1024
                and validate_xlsx(xlsx_path) is None
            )
            if xlsx_ok:
                logger.warning(
                    "[#{}] enrich_xlsx slot={} GPT error after valid xlsx on disk "
                    "({} байт) — skip retry, use downloaded file: {}",
                    project.id,
                    slot_idx,
                    xlsx_path.stat().st_size,
                    e,
                )
                break
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
        is_v8 = has_v8_plan_sheet(wb)
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

    # 5. Ставим статус slot_<i>_ready (не откатываем более продвинутый шаг).
    from app.telegram.menu import status_order as _ord

    await session.refresh(project)
    cur = project.status
    meta = dict(project.meta or {})
    completed = [int(x) for x in (meta.get("enrich_completed_slots") or []) if str(x).isdigit()]
    if slot_idx not in completed:
        completed.append(slot_idx)
        completed.sort()
        meta["enrich_completed_slots"] = completed
    # xlsx на диске обновлён — сбросить кэш персонажей, чтобы Hero
    # перечитал лист «Персонажи» (иначе остаётся старый meta['excel_hero']).
    if "excel_hero" in meta:
        meta.pop("excel_hero")
        logger.info(
            "[#{}] enrich_xlsx slot={}: сброшен кэш excel_hero после "
            "обновления xlsx",
            project.id,
            slot_idx,
        )
    project.meta = meta

    if project.status is running_status or _ord(project.status) < _ord(ready_status):
        project.status = ready_status
    await session.flush()
    logger.info(
        "[#{}] enrich_xlsx slot={} → status={}",
        project.id,
        slot_idx,
        project.status.value,
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
