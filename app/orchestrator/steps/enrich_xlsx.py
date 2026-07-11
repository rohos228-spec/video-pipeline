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
  6. После успеха — `sync_project_xlsx()` → данные в БД,
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
from app.services.excel_gpt_node import (
    EXCEL_GPT_STEP_CODE,
    active_node_key,
    attachment_paths,
    display_attachment_name,
)
from app.services.prompt_library import read_resolved_project_prompt
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
    text = gtb.get_effective_text(project, EXCEL_GPT_STEP_CODE)
    if text.strip():
        return text
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
    running_status, ready_status, legacy_step_code = _SLOT_MAP[slot_idx]
    node_key = active_node_key(project)
    if not node_key:
        from app.orchestrator.graph.planner import load_graph_for_project
        from app.services.excel_gpt_node import EXCEL_GPT_NODE_TYPE, slot_index_from_node

        graph = await load_graph_for_project(session, project)
        for nid, n in graph._by_id.items():
            if str(n.get("type") or "") == EXCEL_GPT_NODE_TYPE and slot_index_from_node(n) == slot_idx:
                node_key = nid
                meta = dict(project.meta or {})
                meta["active_excel_gpt_node_key"] = nid
                project.meta = meta
                await session.flush()
                break
    prompt_step_code = EXCEL_GPT_STEP_CODE
    logger.info(
        "[#{}] enrich_xlsx slot={} node={} prompt={} starting",
        project.id,
        slot_idx,
        node_key,
        prompt_step_code,
    )

    await session.refresh(project)

    # 1. Гарантируем существование xlsx (для свежих проектов).
    sheet = _sheet_for_project(project)
    xlsx_path: Path = sheet.ensure_initialized(
        project_id=project.id, slug=project.slug
    )
    data_paths = attachment_paths(project, node_key)
    if not data_paths:
        raise RuntimeError(
            f"enrich_xlsx: нет файла для отправки "
            f"({display_attachment_name(project, node_key)})"
        )
    download_path = data_paths[0] if data_paths[0].suffix.lower() in {".xlsx", ".xls"} else xlsx_path
    if not download_path.exists():
        download_path = xlsx_path

    # 2. Мастер-промт → файл; сопр. сообщение → короткий текст в чате.
    try:
        variant, src_path, master = read_resolved_project_prompt(project, prompt_step_code)
        logger.info(
            "[#{}] enrich_xlsx slot={}: активный промт variant={!r} "
            "path={} ({} симв) overrides={!r}",
            project.id,
            slot_idx,
            variant,
            src_path,
            len(master or ""),
            (getattr(project, "prompt_overrides", None) or {}).get(prompt_step_code),
        )
    except FileNotFoundError:
        variant = "default"
        src_path = None
        master = (
            f"# {prompt_step_code}\n\n"
            "Мастер-промт для доп. работы с Excel ещё не настроен. "
            "Открой `prompts/05_excel_gpt/default.md` и опиши там, "
            "что именно ChatGPT должен изменить в приложенном файле."
        )
        logger.warning(
            "[#{}] enrich_xlsx slot={}: файл промта не найден, fallback текст",
            project.id,
            slot_idx,
        )

    accompanying = _get_accompanying_text(project, legacy_step_code)

    from app.services import chatgpt_xlsx as cx

    tmp_dir = cx.tmp_gpt_dir(project)
    prompt_file = tmp_dir / f"prompt_{prompt_step_code}_{variant}.md"
    prompt_file.write_text((master or "").strip(), encoding="utf-8")
    attach_files = [prompt_file, *data_paths]

    # 3. Round-trip до 3 раз.
    from app.services.step_cancel import StepCancelledError, raise_if_cancelled

    last_err: Exception | None = None
    xlsx_stat_before_run = cx.project_xlsx_stat(xlsx_path)

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
                    attach_files,
                    download_path,
                    ask_timeout=1200,
                    download_timeout=600,
                    project_id=project.id,
                    validate_xlsx_download=download_path.suffix.lower() in {".xlsx", ".xls"},
                )

            reply = await xgf.run_under_xlsx_lock(
                project.id, legacy_step_code, _do
            )
            logger.info(
                "[#{}] enrich_xlsx: получен ответ len={} (try={})",
                project.id,
                len(reply or ""),
                attempt,
            )
            if not download_path.exists() or download_path.stat().st_size < 1024:
                raise RuntimeError(
                    f"скачанный файл пустой/слишком маленький "
                    f"({download_path.stat().st_size if download_path.exists() else 0} байт)"
                )
            logger.info(
                "[#{}] enrich_xlsx: файл обновлён {} ({} байт)",
                project.id,
                download_path.name,
                download_path.stat().st_size,
            )
            break  # успех
        except Exception as e:  # noqa: BLE001
            last_err = e
            xlsx_ok = cx.should_accept_xlsx_after_gpt_error(
                xlsx_path, xlsx_stat_before_run, e
            )
            if xlsx_ok:
                logger.warning(
                    "[#{}] enrich_xlsx slot={} GPT error after fresh xlsx "
                    "({} байт) — skip retry, use downloaded file: {}",
                    project.id,
                    slot_idx,
                    xlsx_path.stat().st_size,
                    e,
                )
                break
            from app.bots.browser import _looks_like_cdp_connect_failure

            if (
                not xlsx_ok
                and xlsx_path.exists()
                and xlsx_path.stat().st_size >= 1024
                and validate_xlsx(xlsx_path) is None
                and _looks_like_cdp_connect_failure(e)
            ):
                logger.warning(
                    "[#{}] enrich_xlsx slot={}: CDP/Chrome не ответил — "
                    "старый project.xlsx на диске не считаем успехом: {}",
                    project.id,
                    slot_idx,
                    e,
                )
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

    # 4. Единый импорт xlsx → БД (v8 «план» + fallback v7 «Кадры»).
    from app.services.chatgpt_xlsx import sync_project_xlsx

    try:
        sync_target = (
            download_path
            if download_path.suffix.lower() in {".xlsx", ".xls"}
            else xlsx_path
        )
        sync_info = await sync_project_xlsx(
            session,
            project,
            sync_target,
            keep_fields=False,
            update_frames_voiceover=True,
        )
        logger.info(
            "[#{}] enrich_xlsx slot={} sync_project_xlsx: {}",
            project.id,
            slot_idx,
            sync_info,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] enrich_xlsx slot={} sync_project_xlsx failed: {}",
            project.id,
            slot_idx,
            e,
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
    if node_key:
        done_keys = [str(k) for k in (meta.get("excel_gpt_completed_keys") or [])]
        if node_key not in done_keys:
            done_keys.append(node_key)
            meta["excel_gpt_completed_keys"] = done_keys
        meta.pop("active_excel_gpt_node_key", None)
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
