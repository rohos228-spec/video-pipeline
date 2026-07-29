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
    expects_xlsx_result,
    save_gpt_reply_text,
    work_mode,
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
    from app.services.excel_gpt_node import resolve_excel_gpt_node_key_for_slot

    node_key = active_node_key(project)
    if not node_key:
        node_key = resolve_excel_gpt_node_key_for_slot(project, slot_idx)
    if not node_key:
        from app.orchestrator.graph.planner import load_graph_for_project
        from app.services.excel_gpt_node import EXCEL_GPT_NODE_TYPE, slot_index_from_node

        graph = await load_graph_for_project(session, project)
        for nid, n in graph._by_id.items():
            if str(n.get("type") or "") == EXCEL_GPT_NODE_TYPE and slot_index_from_node(n) == slot_idx:
                node_key = nid
                break
    if node_key:
        meta = dict(project.meta or {})
        if meta.get("active_excel_gpt_node_key") != node_key:
            meta["active_excel_gpt_node_key"] = node_key
            project.meta = meta
            await session.flush()
    prompt_step_code = EXCEL_GPT_STEP_CODE
    # Слот «main» в Node Studio → meta.prompt_slot_variants[node_key]["main"].
    # Без node_key все excel_gpt брали один prompt_overrides["excel_gpt"].
    prompt_slot_id = "main"
    logger.info(
        "[#{}] enrich_xlsx slot={} node={} prompt={} starting",
        project.id,
        slot_idx,
        node_key,
        prompt_step_code,
    )

    await session.refresh(project)

    sheet = _sheet_for_project(project)
    xlsx_path: Path = sheet.ensure_initialized(
        project_id=project.id, slug=project.slug
    )

    from app.services.gpt_operator import (
        operator_config,
        resolve_operator,
        save_operator_result,
    )

    op_cfg = operator_config(project, node_key) if node_key else {}
    resolved = resolve_operator(project, node_key) if node_key else None
    explicit = str(op_cfg.get("transport") or "").strip().lower()
    if explicit == "browser":
        raise RuntimeError(
            "enrich_xlsx: transport=browser отключён. "
            "Используй transport=api (GPT HTTP). Убери browser в настройках ноды."
        )
    transport = "api"

    try:
        variant, src_path, master, prompt_source = read_resolved_project_prompt(
            project,
            prompt_step_code,
            node_key=node_key,
            slot_id=prompt_slot_id if node_key else None,
        )
    except FileNotFoundError:
        variant = "default"
        src_path = None
        prompt_source = "default"
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
    else:
        logger.info(
            "[#{}] enrich_xlsx slot={} node={!r}: активный промт variant={!r} "
            "source={} path={} ({} симв) overrides={!r}",
            project.id,
            slot_idx,
            node_key,
            variant,
            prompt_source,
            src_path,
            len(master or ""),
            (getattr(project, "prompt_overrides", None) or {}).get(prompt_step_code),
        )

    accompanying = _get_accompanying_text(project, legacy_step_code)

    # Проверочные роли / тумблер «Проверка».
    branching_role = False
    check_mode = False
    check_fix = True
    source_prompt_keys: list[str] = []
    if node_key and resolved is not None:
        role_name = str(resolved.get("role") or "")
        check_mode = bool(resolved.get("checkMode") or op_cfg.get("checkMode"))
        check_fix = bool(resolved.get("checkFix", op_cfg.get("checkFix", True)))
        branching_role = role_name in ("review", "gate", "compare") or check_mode
        if check_mode:
            from app.services.gpt_operator import (
                assemble_check_master_prompt,
                collect_source_prompts,
                project_format_hint_for_check,
                sanitize_check_reviewer_notes,
            )

            sources = collect_source_prompts(project, node_key)
            ok_sources = [s for s in sources if s.get("ok")]
            if not ok_sources:
                raise RuntimeError("нет исходного промта для проверки")
            source_prompt_keys = [str(s.get("nodeKey") or "") for s in ok_sources]
            # Короткий текст — только доп. указания; старый vp.check.v1 JSON выкидываем.
            reviewer_notes = sanitize_check_reviewer_notes(accompanying or "")
            master = assemble_check_master_prompt(
                ok_sources,
                check_fix=check_fix,
                reviewer_notes=reviewer_notes,
            )
            accompanying = ""
            hint = project_format_hint_for_check(project, node_key)
            if hint:
                accompanying = hint
            logger.info(
                "[#{}] enrich_xlsx node={!r}: checkMode sources={} chars={}",
                project.id,
                node_key,
                source_prompt_keys,
                len(master),
            )
        elif branching_role:
            from app.services.check_analysis import append_response_footer
            from app.services.gpt_operator import (
                default_check_prompt_for_node,
                project_format_hint_for_check,
            )

            # Нет кастомного промта у ноды (source=default) → берём
            # универсальный агент-проверки по типу вышестоящей рабочей ноды.
            if str(prompt_source or "") == "default" or not (master or "").strip():
                agent = default_check_prompt_for_node(project, node_key)
                if agent:
                    master = agent
                    logger.info(
                        "[#{}] enrich_xlsx node={!r}: универсальный агент-проверки "
                        "по вышестоящей ноде ({} симв)",
                        project.id,
                        node_key,
                        len(master),
                    )

            # Целевой формат проекта в контекст — не статично, из полей проекта.
            hint = project_format_hint_for_check(project, node_key)
            if hint and hint not in (accompanying or ""):
                accompanying = (
                    f"{accompanying}\n\n{hint}".strip() if accompanying else hint
                )

            master = append_response_footer(master or "")

    # ── API-транспорт (единственный): без браузера/CDP ─────────────────
    if node_key and resolved is not None:
        from sqlalchemy.orm.attributes import flag_modified

        from app.services.gpt_operator_client import run_operator_api
        from app.services.step_cancel import raise_if_cancelled

        if not resolved.get("canRun"):
            errs = "; ".join(resolved.get("errors") or ["входы не согласованы"])
            raise RuntimeError(f"gpt-operator resolve: {errs}")

        data_paths = [
            Path(str(f["path"]))
            for f in (resolved.get("files") or [])
            if f.get("ok") and f.get("path")
        ]
        if not data_paths:
            raise RuntimeError("gpt-operator: нет существующих файлов на входе")

        role = str(resolved.get("role") or "assist")
        output_mode = "text" if check_mode else str(resolved.get("outputMode") or "text")
        logger.info(
            "[#{}] enrich_xlsx API transport slot={} role={} checkMode={} "
            "output={} files={}",
            project.id,
            slot_idx,
            role,
            check_mode,
            output_mode,
            [p.name for p in data_paths],
        )
        raise_if_cancelled(project.id)
        api_res = await run_operator_api(
            project_dir=project.data_dir,
            node_key=node_key,
            role=role,
            output_mode=output_mode,
            prompt=master or "",
            accompanying=accompanying,
            input_paths=data_paths,
            check_mode=check_mode,
            check_fix=check_fix,
            source_prompt_keys=source_prompt_keys,
        )
        # После project_file writeback — подтянуть xlsx → DB.
        if output_mode == "project_file":
            wrote = any(
                p.name == "project.xlsx" and p.exists() for p in api_res.output_paths
            )
            if wrote:
                try:
                    from app.services.chatgpt_xlsx import sync_project_xlsx

                    await sync_project_xlsx(
                        session, project, project.data_dir / "project.xlsx",
                        keep_fields=False,
                    )
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "[#{}] enrich_xlsx API: sync_project_xlsx after writeback failed",
                        project.id,
                    )
        save_operator_result(
            project,
            node_key,
            input_paths=data_paths,
            output_paths=list(api_res.output_paths),
            reply_text=api_res.reply_text,
            gate_status=api_res.gate_status,
            analysis=(
                api_res.analysis.to_dict() if getattr(api_res, "analysis", None) else None
            ),
        )
        meta = dict(project.meta or {})
        completed = [
            int(x) for x in (meta.get("enrich_completed_slots") or []) if str(x).isdigit()
        ]
        if slot_idx not in completed:
            completed.append(slot_idx)
            completed.sort()
            meta["enrich_completed_slots"] = completed
        done_keys = [str(k) for k in (meta.get("excel_gpt_completed_keys") or [])]
        if node_key not in done_keys:
            done_keys.append(node_key)
            meta["excel_gpt_completed_keys"] = done_keys
        meta.pop("active_excel_gpt_node_key", None)
        project.meta = meta
        flag_modified(project, "meta")
        ready_status = _SLOT_MAP[slot_idx][1]
        project.status = ready_status
        await session.flush()
        if output_mode == "project_file" and any(
            p.name == "project.xlsx" and p.exists() for p in api_res.output_paths
        ):
            try:
                from app.services.node_xlsx_snapshot import snapshot_and_bind_node_xlsx

                await snapshot_and_bind_node_xlsx(
                    session,
                    project,
                    node_key=node_key,
                    node_type="excel_gpt",
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[#{}] enrich_xlsx API slot={} xlsx snapshot bind failed: {}",
                    project.id,
                    slot_idx,
                    e,
                )
        try:
            from app.services.run_sync import complete_excel_gpt_node_by_key

            await complete_excel_gpt_node_by_key(
                session, project, node_key, enrich_slot=slot_idx
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[#{}] enrich_xlsx API: complete_excel_gpt_node_by_key failed",
                project.id,
            )
        logger.info(
            "[#{}] enrich_xlsx API done → {} gate={} files={}",
            project.id,
            ready_status.value,
            api_res.gate_status,
            [p.name for p in api_res.output_paths],
        )
        return

    # ── Legacy browser path (только transport=browser) ────────────────
    data_paths = attachment_paths(project, node_key)
    if not data_paths:
        raise RuntimeError(
            f"enrich_xlsx: нет файла для отправки "
            f"({display_attachment_name(project, node_key)})"
        )
    want_xlsx = expects_xlsx_result(project, node_key)
    mode = work_mode(project, node_key)
    download_path = data_paths[0] if data_paths[0].suffix.lower() in {".xlsx", ".xls"} else xlsx_path
    if not download_path.exists():
        download_path = xlsx_path
    logger.info(
        "[#{}] enrich_xlsx slot={} mode={} want_xlsx={} attach={} (browser)",
        project.id,
        slot_idx,
        mode,
        want_xlsx,
        [p.name for p in data_paths],
    )

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
            if want_xlsx:

                async def _do() -> str:
                    return await xgf.telegram_style_ask_and_download(
                        accompanying.strip(),
                        attach_files,
                        download_path,
                        ask_timeout=1200,
                        download_timeout=600,
                        project_id=project.id,
                        validate_xlsx_download=download_path.suffix.lower()
                        in {".xlsx", ".xls"},
                    )

            else:

                async def _do() -> str:
                    return await xgf.telegram_style_ask_with_files(
                        (accompanying or "").strip()
                        or "Выполни инструкцию из приложенного промта.",
                        attach_files,
                        timeout=1200,
                        project_id=project.id,
                    )

            reply = await xgf.run_under_xlsx_lock(
                project.id, legacy_step_code, _do
            )
            logger.info(
                "[#{}] enrich_xlsx: получен ответ len={} (try={}, want_xlsx={})",
                project.id,
                len(reply or ""),
                attempt,
                want_xlsx,
            )
            if want_xlsx:
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
            else:
                if not (reply or "").strip():
                    raise RuntimeError("GPT вернул пустой ответ (режим без xlsx)")
                saved = save_gpt_reply_text(project, node_key, reply or "")
                logger.info(
                    "[#{}] enrich_xlsx: текстовый ответ сохранён → {}",
                    project.id,
                    saved.name if saved else "?",
                )
            break  # успех
        except Exception as e:  # noqa: BLE001
            last_err = e
            if not want_xlsx:
                logger.warning(
                    "[#{}] enrich_xlsx slot={} text-mode attempt {}/{} FAILED: {}",
                    project.id,
                    slot_idx,
                    attempt,
                    _MAX_RETRIES,
                    e,
                )
                if attempt >= _MAX_RETRIES:
                    project.status = running_status
                    await session.flush()
                    raise RuntimeError(
                        f"enrich_xlsx slot={slot_idx}: 3 попытки failed, last err: {e}"
                    ) from e
                continue
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

    # 3b. Проверочная роль: разобрать ответ → analysis.json + gateStatus
    # (иначе ОК/НЕ ОК на канвасе не откроются после browser-run).
    if branching_role and node_key:
        from app.services.gpt_operator import apply_check_reply

        reply_text = locals().get("reply") or ""
        if not str(reply_text).strip():
            from app.services.excel_gpt_node import upload_dir as _udir

            rp = _udir(project, node_key) / "gpt_reply.txt"
            if rp.is_file():
                reply_text = rp.read_text(encoding="utf-8")
        try:
            apply_check_reply(
                project,
                node_key,
                str(reply_text or ""),
                input_paths=list(data_paths),
                extra_output_paths=(
                    [download_path]
                    if want_xlsx and download_path.exists()
                    else None
                ),
            )
            logger.info(
                "[#{}] enrich_xlsx browser check: gateStatus записан для {}",
                project.id,
                node_key,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[#{}] enrich_xlsx browser check: не удалось сохранить analysis",
                project.id,
            )

    # 4. Единый импорт xlsx → БД (только если ждали обновлённый Excel).
    if want_xlsx:
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
    else:
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(project, "meta")
        await session.flush()

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
    try:
        from app.services.node_xlsx_snapshot import snapshot_and_bind_node_xlsx

        await snapshot_and_bind_node_xlsx(
            session,
            project,
            node_key=node_key,
            node_type="excel_gpt",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] enrich_xlsx slot={} xlsx snapshot bind failed: {}",
            project.id,
            slot_idx,
            e,
        )
    logger.info(
        "[#{}] enrich_xlsx slot={} → status={}",
        project.id,
        slot_idx,
        project.status.value,
    )

    # NodeRun текущей ноды → done ДО переключения active_key на следующий слот.
    # Иначе complete_active_node / UI видят «в работе» на слоте 1, а слот 3
    # ошибочно done без применённого xlsx.
    try:
        from app.services.run_sync import complete_excel_gpt_node_by_key

        await complete_excel_gpt_node_by_key(
            session, project, node_key, enrich_slot=slot_idx
        )
    except Exception:  # noqa: BLE001
        logger.debug(
            "[#{}] enrich_xlsx: complete NodeRun slot={} failed",
            project.id,
            slot_idx,
            exc_info=True,
        )

    # 6. Auto-chain до хвоста excel_gpt на канвасе.
    # Ручной ▶ одной ноды раньше не ставил enrich_auto_chain_to → после
    # enrich_2_ready пайплайн зависал (auto_advance режется gen_queue).
    # Снимаем «готово» со следующих слотов — даже done ноды перегенерируются.
    meta = dict(project.meta or {})
    chain_to = meta.get("enrich_auto_chain_to")
    if not isinstance(chain_to, int):
        from app.services.excel_gpt_node import (
            clear_excel_gpt_tail_completion,
            ensure_enrich_auto_chain_to,
        )

        clear_excel_gpt_tail_completion(project, slot_idx + 1)
        inferred = ensure_enrich_auto_chain_to(project, slot_idx)
        meta = dict(project.meta or {})
        chain_to = inferred if inferred is not None else meta.get("enrich_auto_chain_to")
        if inferred is not None:
            logger.info(
                "[#{}] enrich_xlsx: inferred enrich_auto_chain_to={} after slot {}",
                project.id,
                inferred,
                slot_idx,
            )
    elif isinstance(chain_to, int) and chain_to > slot_idx:
        from app.services.excel_gpt_node import clear_excel_gpt_tail_completion

        clear_excel_gpt_tail_completion(project, slot_idx + 1)
    if isinstance(chain_to, int) and chain_to > slot_idx:
        next_slot = slot_idx + 1
        next_running = _SLOT_MAP[next_slot][0]
        from app.services.excel_gpt_node import resolve_excel_gpt_node_key_for_slot
        from app.services.run_sync import prepare_node_for_step_start

        next_key = resolve_excel_gpt_node_key_for_slot(project, next_slot)
        if next_key:
            meta = dict(project.meta or {})
            meta["active_excel_gpt_node_key"] = next_key
            project.meta = meta
            try:
                await prepare_node_for_step_start(
                    session,
                    project,
                    EXCEL_GPT_STEP_CODE,
                    node_key=next_key,
                    enrich_slot=next_slot,
                    strict=False,
                    explicit_ui_start=True,  # done → pending → running
                )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "[#{}] enrich_xlsx auto-chain: prepare NodeRun {} failed",
                    project.id,
                    next_key,
                    exc_info=True,
                )
        project.status = next_running
        await session.flush()
        logger.info(
            "[#{}] enrich_xlsx auto-chain: {} → {} (next slot #{}, chain_to={}, node={})",
            project.id,
            ready_status.value,
            next_running.value,
            next_slot,
            chain_to,
            next_key,
        )
    elif chain_to is not None:
        # Цепочка дошла до target (или вышла за неё). Снимаем флаг,
        # чтобы при ручном повторном запуске одного слота не было
        # неожиданного авто-перехода.
        meta = dict(project.meta or {})
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
