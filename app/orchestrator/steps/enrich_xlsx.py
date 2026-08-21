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
  6. Данные в БД пишет apply-ops; Excel → DB только явный Import.
     `recompute_status()` поднимет статус. И принудительно ставим
     статус `enrich_<i>_ready`.
"""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, ProjectStatus
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

_APPLY_OPS_HINT = (
    "# ЗАПИСЬ В ПРОЕКТ (предпочтительный формат)\n"
    "Верни ТОЛЬКО JSON apply-ops (без TSV, без `# Лист:`, без `@row=`):\n"
    '{"ops":[{"frame_uuid":"<uuid кадра>","fields":{...}}]}\n'
    "Поля по-человечески: закадр, промт_картинки, промт_видео, смысл, "
    "длительность, промт_картинки_2, промт_видео_2, персонажи; общий план: "
    '{"target":"project","fields":{"общий_план":"…"}}.\n'
    "Реестр персонажей (опционально в том же JSON): "
    '{"characters":[{"id":"c01","имя":"…","внешность":"…","одежда":"…",'
    '"характер":"…","правила":""}],'
    '"ops":[{"frame_uuid":"…","fields":{"персонажи":"c01"}}]}.\n'
    "Неизвестный uuid/поле — весь запрос отклонён. "
    "Адресация кадров (номер = uuid):\n"
)

_SCENE_GRAMMAR_APPLY_HINT = (
    "# SCENE GRAMMAR — ЗАПИСЬ (v1.6, постановка кадра)\n"
    "JSON {characters, scenes, ops, report}. "
    "Каждый shot: подробный фон + конкретное освещение + описание_кадра=камера. "
    "То же место → тот же фон/свет, меняется только камера. "
    "смысл_сцены=видимый факт (не «зритель не знает»). "
    "ops=[]. Запрет: клон акцента; однословный фон; 1 колонка=1 сцена.\n"
)

_CHARACTER_REGISTRY_APPLY_HINT = (
    "# CHARACTER REGISTRY — ЗАПИСЬ (DB SoT)\n"
    "Верни ТОЛЬКО JSON {characters, ops, report}. "
    "Вход = db_frames.json (закадр + текущие Entity). "
    "Пустой characters[] во входе = создай реестр с нуля по закадру. "
    "Запрет: error «реестр не найден»; Excel; TSV; # Лист:; проза вне JSON. "
    "ops: только поле персонажи по frame_uuid из входа.\n"
)

_SCENE_GRAMMAR_PROMPT_MARKERS = (
    "scene_grammar_unified_agent",
    "scene grammar unified",
)

_CHARACTER_REGISTRY_PROMPT_MARKERS = (
    "character_registry_database_agent",
    "агент по созданию персонажей",
    "агент заполнения реестра персонажей",
    "реестр персонаж",
)


def _is_scene_grammar_prompt(variant: str | None, master: str | None) -> bool:
    blob = f"{variant or ''}\n{(master or '')[:400]}".casefold()
    return any(m in blob for m in _SCENE_GRAMMAR_PROMPT_MARKERS)


def _is_character_registry_prompt(variant: str | None, master: str | None) -> bool:
    blob = f"{variant or ''}\n{(master or '')[:800]}".casefold()
    return any(m in blob for m in _CHARACTER_REGISTRY_PROMPT_MARKERS)


_SCRIPT_WRITER_PROMPT_MARKERS = (
    "script_writer",
    "сценарист закадра",
)
_FRAME_PROMPTS_MARKERS = (
    "frame_prompts",
    "промты кадров",
)
_QC_PROMPTS_MARKERS = (
    "prompts_qc",
    "qc промпт",
)


def _prompt_blob(variant: str | None, master: str | None) -> str:
    return f"{variant or ''}\n{(master or '')[:800]}".casefold()


def _is_script_writer_prompt(variant: str | None, master: str | None) -> bool:
    """Сценарист: закадр + разбивка, батчи по 9 ячеек VO, не shot-fill."""
    return any(m in _prompt_blob(variant, master) for m in _SCRIPT_WRITER_PROMPT_MARKERS)


def _is_frame_prompts_prompt(variant: str | None, master: str | None) -> bool:
    return any(m in _prompt_blob(variant, master) for m in _FRAME_PROMPTS_MARKERS)


def _is_qc_prompts_prompt(variant: str | None, master: str | None) -> bool:
    return any(m in _prompt_blob(variant, master) for m in _QC_PROMPTS_MARKERS)

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


def _persist_excel_gpt_reply_for_ui(
    project: Project,
    node_key: str | None,
    *,
    data_paths: list[Path],
    api_res: object,
) -> None:
    """Сохранить ответ GPT в meta/диск до raise — иначе UI без результата."""
    if not node_key:
        return
    try:
        from app.services.gpt_operator import save_operator_result

        reply = str(getattr(api_res, "reply_text", "") or "")
        outs = list(getattr(api_res, "output_paths", None) or [])
        save_operator_result(
            project,
            node_key,
            input_paths=list(data_paths or []),
            output_paths=outs,
            reply_text=reply,
            gate_status=getattr(api_res, "gate_status", None),
            analysis=(
                api_res.analysis.to_dict()  # type: ignore[attr-defined]
                if getattr(api_res, "analysis", None) is not None
                else None
            ),
        )
        if reply.strip():
            save_gpt_reply_text(project, node_key, reply)
    except Exception:  # noqa: BLE001
        logger.exception(
            "[#{}] enrich_xlsx: не удалось сохранить reply для UI (node={})",
            project.id,
            node_key,
        )


def _apply_enrich_ready_status(
    project: Project,
    *,
    running_status: ProjectStatus,
    ready_status: ProjectStatus,
) -> bool:
    """Ставит enrich_N_ready, не откатывая media/более поздний шаг.

    API-путь раньше всегда писал ready_status и затирал generating_videos,
    если video/run стартовал параллельно с долгим checkMode excel_gpt.
    """
    from app.telegram.menu import status_order as _ord

    cur = project.status
    if cur is running_status or _ord(cur) < _ord(ready_status):
        project.status = ready_status
        return True
    logger.warning(
        "[#{}] enrich_xlsx: skip status → {} (project already at {})",
        project.id,
        ready_status.value,
        cur.value if cur is not None else None,
    )
    return False


async def _harness_before_enrich_ready(
    session: AsyncSession, project: Project, *, check_mode: bool
) -> None:
    """NODE_SYSTEM: excel_gpt gate before enrich_N_ready / node done. Skip checkMode."""
    if check_mode:
        return
    from app.services.agent_harness import harness_gate_or_raise

    await harness_gate_or_raise(session, project, step="excel_gpt")


async def _after_excel_gpt_done(
    session: AsyncSession,
    project: Project,
    *,
    node_key: str | None,
    slot_idx: int,
    ready_status: ProjectStatus,
) -> None:
    """После готовности excel_gpt: sync storage; цепочка — только при auto_mode."""
    if node_key:
        try:
            from app.services.storage_node import sync_downstream_storage_from_node

            synced = sync_downstream_storage_from_node(project, node_key)
            for info in synced:
                skipped = info.get("skipped") or []
                logger.info(
                    "[#{}] enrich_xlsx: storage {} ← {} copied={} skipped={} errors={}",
                    project.id,
                    info.get("storageNode"),
                    node_key,
                    len(info.get("copied") or []),
                    len(skipped),
                    info.get("errors") or [],
                )
                if not (info.get("copied") or []) and skipped:
                    logger.warning(
                        "[#{}] enrich_xlsx: storage {} пусто после sync — причины: {}",
                        project.id,
                        info.get("storageNode"),
                        "; ".join(str(x) for x in skipped[:12]),
                    )
            if synced:
                from sqlalchemy.orm.attributes import flag_modified

                flag_modified(project, "meta")
                await session.flush()
        except Exception:  # noqa: BLE001
            logger.exception(
                "[#{}] enrich_xlsx: sync downstream storage failed node={}",
                project.id,
                node_key,
            )

    # checkMode после hero/scenes/videos: fail → db_patch + точечный переген → снова check.
    # Работает и без auto_mode (воркер подхватит generating_*).
    if node_key:
        try:
            from app.services.vision_check_loop import (
                maybe_start_vision_check_loop_after_check,
            )

            started = await maybe_start_vision_check_loop_after_check(
                session, project, node_key
            )
            if started:
                return
        except Exception:  # noqa: BLE001
            logger.exception(
                "[#{}] enrich_xlsx: vision_check_loop after {} failed",
                project.id,
                node_key,
            )

    # Без автопродвижения — остаёмся на enrich_N_ready, следующую ноду
    # пользователь запускает вручную ▶. Воркер сам вызовет maybe_auto_advance
    # только если auto_mode=True.
    if not getattr(project, "auto_mode", False):
        logger.info(
            "[#{}] enrich_xlsx: slot {} → {} — auto_mode выкл, без auto-chain/advance",
            project.id,
            slot_idx,
            ready_status.value,
        )
        return

    await _maybe_auto_chain_excel_gpt(
        session,
        project,
        slot_idx,
        ready_status,
        finished_key=node_key,
    )


async def _maybe_auto_chain_excel_gpt(
    session: AsyncSession,
    project: Project,
    slot_idx: int,
    ready_status: ProjectStatus,
    *,
    finished_key: str | None = None,
) -> None:
    """После enrich_N_ready: прыжок на следующий excel_gpt ТОЛЬКО по стрелкам.

    Если следующий work — script/hero/другое, не трогаем status (auto_advance
    пойдёт по графу). Orphan-слоты на канвасе без входящей стрелки не запускаем.

    Активную ноду берём по id со стрелки, не через resolve(next_slot) — иначе
    при коллизии slotIndex (6+ excel_gpt / лимит 5) стартует чужая нода
    (проверка пропускается, «персонажи» стартуют раньше).
    """
    from app.services.excel_gpt_node import (
        clear_excel_gpt_tail_completion,
        ensure_enrich_auto_chain_to,
        first_work_successor_from_excel_slot,
        is_excel_gpt_node_type,
    )

    finished_key = (finished_key or "").strip() or None
    succ = first_work_successor_from_excel_slot(
        project, slot_idx, from_key=finished_key
    )
    if succ is None:
        meta = dict(project.meta or {})
        if "enrich_auto_chain_to" in meta:
            meta.pop("enrich_auto_chain_to", None)
            project.meta = meta
            await session.flush()
        return
    next_key, typ, next_slot = succ
    if not is_excel_gpt_node_type(typ):
        # Стрелка ведёт не в excel_gpt — цепочку не форсим.
        meta = dict(project.meta or {})
        if "enrich_auto_chain_to" in meta:
            meta.pop("enrich_auto_chain_to", None)
            project.meta = meta
            await session.flush()
        logger.info(
            "[#{}] enrich_xlsx: после slot {} / {} следующий work={} — без auto-chain",
            project.id,
            slot_idx,
            finished_key or "?",
            typ or "?",
        )
        return
    if not next_key or next_key == (finished_key or ""):
        return
    if next_slot is None or next_slot < 1:
        next_slot = slot_idx + 1 if slot_idx < 5 else 5
    if next_slot not in _SLOT_MAP:
        return

    ensure_enrich_auto_chain_to(project, slot_idx, from_key=finished_key)
    clear_excel_gpt_tail_completion(project, next_slot)
    meta = dict(project.meta or {})
    chain_to = meta.get("enrich_auto_chain_to")
    next_running = _SLOT_MAP[next_slot][0]
    from app.services.run_sync import prepare_node_for_step_start

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
            explicit_ui_start=True,
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
    check_prompt_source = "upstream"
    source_prompt_keys: list[str] = []
    db_sot_check = False
    if node_key and resolved is not None:
        role_name = str(resolved.get("role") or "")
        check_mode = bool(resolved.get("checkMode") or op_cfg.get("checkMode"))
        check_fix = bool(resolved.get("checkFix", op_cfg.get("checkFix", True)))
        check_prompt_source = str(
            resolved.get("checkPromptSource")
            or op_cfg.get("checkPromptSource")
            or "upstream"
        ).strip().lower()
        if check_prompt_source not in ("upstream", "agent"):
            check_prompt_source = "upstream"
        branching_role = role_name in ("review", "gate", "compare") or check_mode
        if check_mode:
            from app.services.gpt_operator import (
                assemble_check_agent_prompt,
                assemble_check_master_prompt,
                collect_source_prompts,
                project_format_hint_for_check,
                sanitize_check_reviewer_notes,
            )

            reviewer_notes = sanitize_check_reviewer_notes(accompanying or "")
            from app.services.gpt_operator import resolve_check_report_format

            report_fmt, _ = resolve_check_report_format(project, node_key)
            from app.services.gpt_operator import append_vision_hint_for_upstream

            # Нет картинок на входе → проверка по DB, не по Excel/TSV.
            _img_ext = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
            _has_vision = any(
                Path(str(f.get("name") or f.get("path") or "")).suffix.lower()
                in _img_ext
                for f in (resolved.get("files") or [])
                if f.get("ok")
            )
            db_sot_check = not _has_vision

            if check_prompt_source == "agent":
                master, agent_step = assemble_check_agent_prompt(
                    project,
                    node_key,
                    check_fix=check_fix,
                    reviewer_notes=reviewer_notes,
                    report_format=report_fmt,
                    db_sot=db_sot_check,
                )
                master = append_vision_hint_for_upstream(project, node_key, master)
                source_prompt_keys = [f"agent:{agent_step}"] if agent_step else ["agent"]
                accompanying = ""
                hint = project_format_hint_for_check(project, node_key)
                if hint:
                    accompanying = hint
                logger.info(
                    "[#{}] enrich_xlsx node={!r}: checkMode agent={} "
                    "db_sot={} chars={}",
                    project.id,
                    node_key,
                    agent_step,
                    db_sot_check,
                    len(master),
                )
            else:
                sources = collect_source_prompts(project, node_key)
                ok_sources = [s for s in sources if s.get("ok")]
                if not ok_sources:
                    raise RuntimeError("нет исходного промта для проверки")
                source_prompt_keys = [str(s.get("nodeKey") or "") for s in ok_sources]
                # Короткий текст — только доп. указания; старый vp.check.v1 JSON выкидываем.
                master = assemble_check_master_prompt(
                    ok_sources,
                    check_fix=check_fix,
                    reviewer_notes=reviewer_notes,
                    report_format=report_fmt,
                    db_sot=db_sot_check,
                )
                master = append_vision_hint_for_upstream(project, node_key, master)
                accompanying = ""
                hint = project_format_hint_for_check(project, node_key)
                if hint:
                    accompanying = hint
                logger.info(
                    "[#{}] enrich_xlsx node={!r}: checkMode sources={} "
                    "db_sot={} chars={}",
                    project.id,
                    node_key,
                    source_prompt_keys,
                    db_sot_check,
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
        # project_file / scene_grammar / character_registry = DB SoT.
        output_mode_early = (
            "text" if check_mode else str(resolved.get("outputMode") or "text")
        )
        scene_grammar_early = (not check_mode) and _is_scene_grammar_prompt(
            variant, master
        )
        character_registry_early = (not check_mode) and _is_character_registry_prompt(
            variant, master
        )
        if (
            not data_paths
            and output_mode_early != "project_file"
            and not scene_grammar_early
            and not character_registry_early
            and not (check_mode and db_sot_check)
        ):
            raise RuntimeError("gpt-operator: нет существующих файлов на входе")

        if check_mode and node_key and db_sot_check:
            import json as _json

            from sqlalchemy import select as _select

            from app.models import Entity
            from app.services import db_v2

            await db_v2.backfill_project_v2(session, project)
            frames_chk = list(
                (
                    await session.execute(
                        _select(Frame)
                        .where(Frame.project_id == project.id)
                        .order_by(Frame.number)
                    )
                ).scalars().all()
            )
            ents = list(
                (
                    await session.execute(
                        _select(Entity).where(Entity.project_id == project.id)
                    )
                ).scalars().all()
            )
            meta = project.meta if isinstance(project.meta, dict) else {}
            from app.services.db_frames_context import build_excel_gpt_check_context

            db_check = build_excel_gpt_check_context(
                project_id=project.id,
                slug=project.slug,
                frames=frames_chk,
                characters=[
                    {
                        "id": e.code or e.name,
                        "имя": e.name,
                        "type": e.type,
                        "attrs": e.attrs or {},
                    }
                    for e in ents
                ],
                scene_registry=meta.get("scene_registry") or [],
            )
            ctx_dir = project.data_dir / "excel_gpt_uploads" / str(node_key)
            ctx_dir.mkdir(parents=True, exist_ok=True)
            db_check_path = ctx_dir / "db_check.json"
            db_check_path.write_text(
                _json.dumps(db_check, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Только DB. Excel/TSV со стрелок/inputSource выкидываем.
            data_paths = [
                db_check_path,
                *[
                    p
                    for p in data_paths
                    if p.suffix.lower()
                    not in {".xlsx", ".xlsm", ".xls", ".tsv", ".csv"}
                    and p.resolve() != db_check_path.resolve()
                ],
            ]
            accompanying = (
                f"{accompanying}\n\n"
                "# DB SoT CHECK\n"
                "Вложение db_check.json — кадры (uuid+attrs), scene_registry, "
                "characters. Excel не существует для этой проверки. "
                "Не пиши findings про TSV / project.xlsx / # Лист:."
            ).strip()
            logger.info(
                "[#{}] enrich_xlsx node={!r}: checkMode DB SoT — "
                "db_check.json frames={} scenes={} files={}",
                project.id,
                node_key,
                len(db_check["frames"]),
                len(db_check["scene_registry"] or []),
                [p.name for p in data_paths],
            )

        if check_mode and node_key:
            from app.services.vision_check_db import build_vision_db_snapshot
            from app.services.vision_check_loop import (
                filter_image_paths_for_recheck,
                get_vision_kind,
            )

            data_paths = filter_image_paths_for_recheck(project, data_paths)
            try:
                from app.services.gpt_operator import upstream_node_type_for_check

                up = upstream_node_type_for_check(project, node_key)
                kind_hint = get_vision_kind(project)
                if not kind_hint:
                    if up == "hero":
                        kind_hint = "hero"
                    elif up in ("images", "hitl_images", "image_prompts"):
                        kind_hint = "scenes"
                    elif up in ("videos", "hitl_videos", "animation_prompts"):
                        kind_hint = "videos"
                # videos/*.mp4 → сетка 3×2 (6 stills) для GPT vision
                if kind_hint == "videos" or up in (
                    "videos",
                    "hitl_videos",
                ):
                    from app.services.video_sheet import (
                        is_video_path,
                        materialize_video_sheets_for_check,
                    )

                    if any(is_video_path(p) for p in data_paths):
                        sheets_dir = project.data_dir / "tmp_video_sheets"
                        data_paths = await materialize_video_sheets_for_check(
                            data_paths, sheets_dir
                        )
                        kind_hint = "videos"
                        logger.info(
                            "[#{}] enrich_xlsx: video→sheet 3×2 → {} файлов",
                            project.id,
                            len(data_paths),
                        )
                db_snap = await build_vision_db_snapshot(
                    session,
                    project,
                    image_paths=data_paths,
                    kind=kind_hint,
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "[#{}] enrich_xlsx: vision DB snapshot failed",
                    project.id,
                )
                db_snap = ""
            if db_snap:
                accompanying = (
                    f"{accompanying}\n\n{db_snap}".strip()
                    if accompanying
                    else db_snap
                )

        role = str(resolved.get("role") or "assist")
        output_mode = "text" if check_mode else str(resolved.get("outputMode") or "text")
        scene_grammar = (not check_mode) and _is_scene_grammar_prompt(variant, master)
        character_registry = (not check_mode) and _is_character_registry_prompt(
            variant, master
        )
        if scene_grammar and output_mode != "project_file":
            logger.info(
                "[#{}] enrich_xlsx node={!r}: scene_grammar → force "
                "outputMode=project_file (было {!r})",
                project.id,
                node_key,
                output_mode,
            )
            output_mode = "project_file"
        if character_registry and output_mode != "project_file":
            logger.info(
                "[#{}] enrich_xlsx node={!r}: character_registry → force "
                "outputMode=project_file (было {!r})",
                project.id,
                node_key,
                output_mode,
            )
            output_mode = "project_file"
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
        expected_frame_uuids: list[str] = []
        db_ctx: dict | None = None
        ctx_path = None
        # DB SoT: для project_file просим apply-ops JSON вместо TSV (+ uuid-мап).
        if output_mode == "project_file" and not check_mode:
            import json as _json

            from sqlalchemy import select as _select

            from app.models import Entity
            from app.services import db_apply, db_v2
            from app.services.db_frames_context import build_excel_gpt_db_context
            from app.services.excel_characters import entity_cards_for_gpt

            await db_v2.backfill_project_v2(session, project)
            frames_for_map = list(
                (
                    await session.execute(
                        _select(Frame)
                        .where(Frame.project_id == project.id)
                        .order_by(Frame.number)
                    )
                ).scalars().all()
            )
            ents = list(
                (
                    await session.execute(
                        _select(Entity).where(Entity.project_id == project.id)
                    )
                ).scalars().all()
            )
            # Компактный снимок DB (SoT). Excel в GPT не отдаём вообще.
            # scene_grammar: attrs не тащим — пишем с нуля.
            # character_registry: voiceover + текущие персонажи кадра + Entity.
            # excel_gpt else: slim whitelist + короткий VO, без сырого attrs.
            if scene_grammar or character_registry:
                frame_rows: list[dict] = []
                for fr in frames_for_map:
                    if not fr.uuid:
                        continue
                    row: dict = {
                        "number": fr.number,
                        "uuid": fr.uuid,
                        "voiceover_text": fr.voiceover_text or "",
                        "meaning": fr.meaning or "",
                    }
                    if character_registry:
                        attrs = fr.attrs or {}
                        row["персонажи"] = str(
                            attrs.get("characters")
                            or attrs.get("персонажи")
                            or attrs.get("persons")
                            or ""
                        )
                    frame_rows.append(row)
                db_ctx: dict = {
                    "source": "db_v2",
                    "project_id": project.id,
                    "slug": project.slug,
                    "frames": frame_rows,
                    "characters": entity_cards_for_gpt(ents),
                }
            else:
                db_ctx = build_excel_gpt_db_context(
                    project_id=project.id,
                    slug=project.slug,
                    frames=frames_for_map,
                    characters=entity_cards_for_gpt(ents),
                )
            ctx_dir = project.data_dir / "excel_gpt_uploads" / str(node_key)
            ctx_dir.mkdir(parents=True, exist_ok=True)
            ctx_path = ctx_dir / "db_frames.json"
            ctx_path.write_text(
                _json.dumps(db_ctx, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            expected_frame_uuids = [
                str(row.get("uuid") or "").strip()
                for row in (db_ctx.get("frames") or [])
                if str(row.get("uuid") or "").strip()
            ]
            # Только DB-снимок. Никакого project.xlsx / check_report / мусора
            # со стрелок — иначе модель снова думает про Excel.
            data_paths = [ctx_path]
            logger.info(
                "[#{}] enrich_xlsx node={!r}: project_file/DB SoT — "
                "только db_frames.json frames={} characters={}",
                project.id,
                node_key,
                len(db_ctx["frames"]),
                len(db_ctx.get("characters") or []),
            )
            if scene_grammar:
                hint = _SCENE_GRAMMAR_APPLY_HINT
            elif character_registry:
                hint = _CHARACTER_REGISTRY_APPLY_HINT
            else:
                hint = _APPLY_OPS_HINT
            if scene_grammar:
                accompanying = (
                    f"{accompanying}\n\n{hint}\n"
                    "# DB SoT\n"
                    "db_frames.json — куски закадра (якоря границ). "
                    "Не плоди ops по числу uuid. Пиши characters + scenes "
                    "(полный паспорт + shots[]). ops=[] допустим. "
                    "Пайплайн сам привяжет сцены к кадрам по словам."
                ).strip()
                vo_chunks = [
                    (fr.voiceover_text or "").strip()
                    for fr in frames_for_map
                    if (fr.voiceover_text or "").strip()
                ]
                full_vo = (project.script_text or "").strip() or " ".join(vo_chunks)
                if full_vo:
                    accompanying = (
                        f"{accompanying}\n\n"
                        f"# ПОЛНЫЙ ЗАКАДР (для start_words/end_words)\n"
                        f"{full_vo}"
                    ).strip()
            elif character_registry:
                uuid_frames = [f for f in frames_for_map if f.uuid]
                mapping = ""
                if uuid_frames:
                    mapping = "\n".join(
                        f"кадр {f.number} = {f.uuid}" for f in uuid_frames
                    )
                accompanying = (
                    f"{accompanying}\n\n{hint}\n"
                    f"{mapping}\n"
                    "# DB SoT\n"
                    "db_frames.json: frames[].voiceover_text + персонажи, "
                    "characters[] = текущий Entity (может быть []). "
                    "Пустой реестр — норма: создай characters с нуля. "
                    "Пайплайн запишет JSON в Entity + attrs. Excel нет."
                ).strip()
                vo_chunks = [
                    (fr.voiceover_text or "").strip()
                    for fr in frames_for_map
                    if (fr.voiceover_text or "").strip()
                ]
                full_vo = (project.script_text or "").strip() or " ".join(vo_chunks)
                if full_vo:
                    accompanying = (
                        f"{accompanying}\n\n"
                        f"# ПОЛНЫЙ ЗАКАДР\n{full_vo}"
                    ).strip()
            else:
                uuid_frames = [f for f in frames_for_map if f.uuid]
                mapping = ""
                if uuid_frames:
                    mapping = "\n".join(
                        f"кадр {f.number} = {f.uuid}" for f in uuid_frames
                    )
                accompanying = (
                    f"{accompanying}\n\n{hint}{mapping}"
                ).strip()
                accompanying = (
                    f"{accompanying}\n\n"
                    "# DB SoT\n"
                    "Файл db_frames.json — кадры из базы (uuid + voiceover). "
                    "Пайплайн сам запишет твой JSON в базу. "
                    "Excel не используется. Отвечай только JSON apply-ops."
                ).strip()
        # Отпустить SQLite write-txn на время GPT (иначе UI: database is locked / 30с).
        await session.commit()
        check_streams_n = None
        if check_mode:
            from app.services.check_streams import get_check_streams

            check_streams_n = get_check_streams(project)
        if scene_grammar and output_mode == "project_file" and not check_mode:
            from app.services.scene_grammar_batches import run_scene_grammar_batched

            logger.info(
                "[#{}] enrich_xlsx node={!r}: scene_grammar batched run "
                "(1▶ → outline + shot-batches)",
                project.id,
                node_key,
            )
            api_res = await run_scene_grammar_batched(
                project_dir=project.data_dir,
                node_key=node_key,
                master_prompt=master or "",
                accompanying=accompanying,
                input_paths=data_paths,
                project_id=project.id,
            )
        elif (
            output_mode == "project_file"
            and not check_mode
            and not character_registry
            and isinstance(db_ctx, dict)
            and ctx_path is not None
        ):
            from app.services.apply_ops_batches import (
                PROMPT_UNITS_PER_BATCH,
                VO_PARALLEL_MAX,
                VO_STAGGER_SEC,
                VO_UNITS_PER_BATCH,
                run_apply_ops_batched,
            )
            from app.services import db_apply as _db_apply
            from app.services.node_write_contract import filter_ops_for_node

            nk = str(node_key or "")
            script_writer = _is_script_writer_prompt(variant, master) or nk.endswith(
                "_fw_script"
            )
            frame_prompts = _is_frame_prompts_prompt(variant, master) or nk.endswith(
                "_fw_frames"
            )
            qc_prompts = _is_qc_prompts_prompt(variant, master) or nk.endswith("_fw_qc")
            write_prompts = frame_prompts or qc_prompts
            keep_voiceover = script_writer
            apply_kind = (
                "excel_gpt_prompts" if write_prompts else "excel_gpt_no_prompts"
            )

            async def _apply_batch(payload: dict) -> None:
                from app.services.db_apply import FIELD_ALIASES

                ops = filter_ops_for_node(
                    list(payload.get("ops") or []),
                    node_kind=apply_kind,
                )
                # Shot-fill не должен затирать закадр/смысл, если
                # volume-continue подмешал чужую схему полей.
                # Сценарист как раз пишет закадр — не выкидываем.
                _drop = set() if keep_voiceover else {"voiceover_text", "meaning"}
                cleaned: list[dict] = []
                for op in ops:
                    fields = op.get("fields")
                    if not isinstance(fields, dict):
                        cleaned.append(op)
                        continue
                    kept = {}
                    for k, v in fields.items():
                        canon = FIELD_ALIASES.get(
                            str(k).strip().lower().replace(" ", "_"),
                            str(k),
                        )
                        if canon in _drop:
                            continue
                        kept[k] = v
                    if not kept:
                        continue
                    new_op = dict(op)
                    new_op["fields"] = kept
                    cleaned.append(new_op)
                ops = cleaned
                await _db_apply.apply_ops(
                    session,
                    project,
                    ops,
                    export_xlsx=bool(payload.get("export_xlsx", False)),
                    node_kind=apply_kind,
                )
                await session.commit()
                await session.refresh(project)

            if script_writer:
                batch_label = (
                    f"закадр по {VO_UNITS_PER_BATCH}, "
                    f"параллельно {VO_PARALLEL_MAX}, "
                    f"сдвиг {VO_STAGGER_SEC:g}с"
                )
            elif write_prompts:
                batch_label = (
                    f"промты по {PROMPT_UNITS_PER_BATCH}, "
                    f"параллельно {VO_PARALLEL_MAX}, "
                    f"сдвиг {VO_STAGGER_SEC:g}с"
                )
            else:
                batch_label = "dense shot fill"
            logger.info(
                "[#{}] enrich_xlsx node={!r}: apply-ops adaptive "
                "1→2→4 ({} )",
                project.id,
                node_key,
                batch_label,
            )
            api_res = await run_apply_ops_batched(
                project_dir=project.data_dir,
                node_key=node_key,
                role=role,
                output_mode=output_mode,
                prompt=master or "",
                accompanying=accompanying,
                db_ctx=db_ctx,
                ctx_path=ctx_path,
                project_id=project.id,
                dense=not write_prompts,
                apply_fn=_apply_batch,
                skip_if_field="image_prompt" if frame_prompts else None,
                chunk_size=(
                    VO_UNITS_PER_BATCH
                    if script_writer
                    else (PROMPT_UNITS_PER_BATCH if write_prompts else None)
                ),
                parallel_max=(
                    VO_PARALLEL_MAX if script_writer or write_prompts else None
                ),
                stagger_sec=(
                    VO_STAGGER_SEC if script_writer or write_prompts else None
                ),
                footer_kind=(
                    "vo"
                    if script_writer
                    else ("prompts" if write_prompts else None)
                ),
            )
        else:
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
                check_streams=check_streams_n,
                db_sot_check=db_sot_check,
            )
        await session.refresh(project)
        # После project_file / DB-check: apply-ops JSON → DB (SoT).
        if output_mode == "project_file" or (
            check_mode
            and check_fix
            and db_sot_check
            and getattr(api_res, "apply_ops", None)
        ):
            ops_data = getattr(api_res, "apply_ops", None)
            ops_list = (
                list(ops_data.get("ops") or [])
                if isinstance(ops_data, dict)
                else []
            )
            chars_list = (
                list(ops_data.get("characters") or [])
                if isinstance(ops_data, dict)
                else []
            )
            scenes_list = (
                list(ops_data.get("scenes") or [])
                if isinstance(ops_data, dict)
                else []
            )
            # check/report_only не пишут (или пишут DB-check без стрипа).
            # Shot-fill не пишет промты картинки/видео; frames/QC — пишут.
            apply_node_kind: str | None = None
            if ops_list and not check_mode:
                from app.services.node_write_contract import filter_ops_for_node

                nk = str(node_key or "")
                write_prompts = (
                    _is_frame_prompts_prompt(variant, master)
                    or _is_qc_prompts_prompt(variant, master)
                    or nk.endswith("_fw_frames")
                    or nk.endswith("_fw_qc")
                )
                apply_kind = (
                    "excel_gpt_prompts" if write_prompts else "excel_gpt_no_prompts"
                )
                n_fields_before = sum(
                    len(op.get("fields") or {})
                    for op in ops_list
                    if isinstance(op, dict)
                )
                ops_list = filter_ops_for_node(ops_list, node_kind=apply_kind)
                apply_node_kind = apply_kind
                n_fields_after = sum(
                    len(op.get("fields") or {})
                    for op in ops_list
                    if isinstance(op, dict)
                )
                dropped = n_fields_before - n_fields_after
                if dropped:
                    logger.info(
                        "[#{}] enrich_xlsx node={}: stripped {} fields ({})",
                        project.id,
                        node_key,
                        dropped,
                        apply_kind,
                    )
            if getattr(api_res, "applied_in_runner", False):
                logger.info(
                    "[#{}] enrich_xlsx node={}: apply-ops уже записан "
                    "по батчам ({} ops)",
                    project.id,
                    node_key,
                    len(ops_list),
                )
            elif ops_data and (ops_list or chars_list or scenes_list):
                from app.services import db_apply
                from app.services.node_write_contract import (
                    coverage_report,
                    skip_frame_coverage,
                )

                try:
                    applied = await db_apply.apply_ops(
                        session,
                        project,
                        ops_list,
                        characters=chars_list or None,
                        scenes=scenes_list or None,
                        export_xlsx=bool(ops_data.get("export_xlsx", False)),
                        node_kind=apply_node_kind,
                    )
                    await session.commit()
                    logger.info(
                        "[#{}] enrich_xlsx node={}: apply-ops записано "
                        "ops={} characters={} scenes={} (DB only)",
                        project.id,
                        node_key,
                        applied.get("updated"),
                        applied.get("characters"),
                        applied.get("scenes"),
                    )
                    skip_coverage = skip_frame_coverage(
                        scene_grammar=scene_grammar,
                        character_registry=character_registry,
                        ops_list=ops_list,
                        chars_list=chars_list,
                        scenes_list=scenes_list,
                    )
                    if expected_frame_uuids and not check_mode and not skip_coverage:
                        cov = coverage_report(
                            ops_list, expected_uuids=expected_frame_uuids
                        )
                        if cov.extra:
                            logger.warning(
                                "[#{}] enrich_xlsx node={}: extra frame_uuid {}",
                                project.id,
                                node_key,
                                cov.extra[:8],
                            )
                        if not cov.ok:
                            _persist_excel_gpt_reply_for_ui(
                                project,
                                node_key,
                                data_paths=data_paths,
                                api_res=api_res,
                            )
                            raise RuntimeError(
                                f"enrich_xlsx node={node_key}: покрытие "
                                f"{len(cov.matched)}/{len(expected_frame_uuids)} "
                                f"не N/N, missing={cov.missing}. "
                                "Нода не done."
                            )
                except db_apply.ApplyOpsError as e:
                    # Ответ модели уже на диске — сохраняем в meta, иначе UI
                    # показывает «нет результата» при отклонённых полях.
                    _persist_excel_gpt_reply_for_ui(
                        project,
                        node_key,
                        data_paths=data_paths,
                        api_res=api_res,
                    )
                    raise RuntimeError(
                        f"enrich_xlsx node={node_key}: apply-ops отклонён: {e}"
                    ) from None
            elif ops_data:
                detail = (
                    str(ops_data.get("error") or ops_data.get("report") or "")
                    .strip()
                    or "ops и characters пустые"
                )
                _persist_excel_gpt_reply_for_ui(
                    project,
                    node_key,
                    data_paths=data_paths,
                    api_res=api_res,
                )
                raise RuntimeError(
                    f"enrich_xlsx node={node_key}: apply-ops JSON пустой — {detail}. "
                    "Нужны непустые ops и/или characters (создай реестр, "
                    "не возвращай пустые списки)."
                )
            else:
                # DB SoT: без apply-ops шаг не принимаем (TSV/xlsx writeback отключён).
                _persist_excel_gpt_reply_for_ui(
                    project,
                    node_key,
                    data_paths=data_paths,
                    api_res=api_res,
                )
                raise RuntimeError(
                    f"enrich_xlsx node={node_key}: модель не вернула apply-ops JSON. "
                    "Нужен {\"ops\":[…]} и/или {\"characters\":[…]} — "
                    "запись через Excel/TSV больше не поддерживается."
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
        ready_status = _SLOT_MAP[slot_idx][1]
        running_status = _SLOT_MAP[slot_idx][0]
        # status мог уйти в generating_* пока шёл долгий API-check
        await session.refresh(project)
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
        await session.refresh(project)
        # Юзер мог ▶ другой шаг (split) пока GPT enrich ещё отвечал.
        if project.status is not running_status:
            logger.warning(
                "[#{}] enrich_xlsx slot={}: статус уже {} (ждали {}) — "
                "не пишем enrich ready (stale GPT)",
                project.id,
                slot_idx,
                project.status.value,
                running_status.value,
            )
            return
        await _harness_before_enrich_ready(session, project, check_mode=check_mode)
        _apply_enrich_ready_status(
            project,
            running_status=running_status,
            ready_status=ready_status,
        )
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
                from app.services.node_xlsx_snapshot import release_upload_display_source

                if release_upload_display_source(project, node_key):
                    flag_modified(project, "meta")
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
        await _after_excel_gpt_done(
            session,
            project,
            node_key=node_key,
            slot_idx=slot_idx,
            ready_status=ready_status,
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

    # Browser GPT тоже долгий — не держим SQLite write-lock.
    await session.commit()

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

    # 4. Excel → DB только через явный Import (не auto после enrich).
    if want_xlsx:
        logger.info(
            "[#{}] enrich_xlsx slot={}: skip auto Excel import "
            "(DB SoT; use Import button if needed)",
            project.id,
            slot_idx,
        )
    else:
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(project, "meta")
        await session.flush()

    # 5. Ставим статус slot_<i>_ready (не откатываем более продвинутый шаг).
    await session.refresh(project)
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

    await _harness_before_enrich_ready(session, project, check_mode=check_mode)
    _apply_enrich_ready_status(
        project,
        running_status=running_status,
        ready_status=ready_status,
    )
    await session.flush()
    try:
        from app.services.node_xlsx_snapshot import snapshot_and_bind_node_xlsx

        await snapshot_and_bind_node_xlsx(
            session,
            project,
            node_key=node_key,
            node_type="excel_gpt",
        )
        from app.services.node_xlsx_snapshot import release_upload_display_source
        from sqlalchemy.orm.attributes import flag_modified

        if node_key and release_upload_display_source(project, node_key):
            flag_modified(project, "meta")
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

    # 6. Storage sync + auto-chain / advance.
    await _after_excel_gpt_done(
        session,
        project,
        node_key=node_key,
        slot_idx=slot_idx,
        ready_status=ready_status,
    )

    _ = last_err  # keep ref to silence "unused" warning in some linters
