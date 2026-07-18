"""Запуск шагов пайплайна без Telegram (из веб-API)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from loguru import logger

from app.models import Project, ProjectStatus
from app.services.mass_factory import assert_not_factory_template_for_generation
from app.services.reset_step import clear_step_outputs_for_rerun, _WRAPPER_TO_CODES
from app.services.chatgpt_xlsx import purge_tmp_gpt_for_step
from app.services.chatgpt_xlsx import sync_project_xlsx
from app.services.step_cancel import clear_stop
from app.services.project_state import is_running_status
from app.telegram.menu import step_by_code, step_by_running_status


def list_step_codes() -> list[dict[str, str]]:
    """Краткий каталог шагов для UI."""
    from app.telegram.menu import steps_for

    out: list[dict[str, str]] = []
    for st in steps_for(None):
        out.append(
            {
                "code": st.code,
                "label": st.title,
                "running_status": st.running_status.value,
                "ready_status": st.ready_status.value,
            }
        )
    return out


async def _preempt_running_for_manual_start(
    session: AsyncSession,
    project: Project,
    target_running: ProjectStatus,
) -> None:
    """Остановить текущий running-шаг без user_stop — сразу стартуем другой."""
    if not is_running_status(project.status) or project.status is target_running:
        return
    from app.services.project_control import clear_user_stop_gate
    from app.services.run_sync import stop_active_running_node
    from app.services.step_cancel import clear_stop, request_stop
    from app.services.xlsx_flow_locks import clear_xlsx_flow_locks

    prev = project.status
    request_stop(project.id)
    clear_xlsx_flow_locks(project.id)
    await stop_active_running_node(session, project)
    clear_stop(project.id)
    clear_user_stop_gate(project)
    step = step_by_running_status(prev)
    if step is not None and step.requires is not None:
        project.status = step.requires
    await session.flush()
    logger.info(
        "[#{}] start_step: preempt {} → {} перед ручным стартом {}",
        project.id,
        prev.value,
        project.status.value,
        target_running.value,
    )


async def start_step(
    session: AsyncSession,
    project: Project,
    step_code: str,
    *,
    node_key: str | None = None,
    skip_queue_guard: bool = False,
    require_node_fsm: bool = False,
    explicit_ui_start: bool = False,
) -> ProjectStatus:
    """Перевести проект в running-статус шага — воркер подхватит."""
    # Studio/явный UI: очередь и «уже running» не блокируют — preempt + старт.
    if explicit_ui_start:
        skip_queue_guard = True
        from app.services.project_control import (
            clear_auto_await_manual_start,
            clear_mass_family_halt,
            clear_user_stop_gate,
        )
        from app.services.mass_factory import mass_parent_id
        from app.services.sidebar_layout import clear_gen_queue_halted

        clear_user_stop_gate(project)
        clear_auto_await_manual_start(project)
        # Явный ▶ снимает глобальный halt очереди и family-halt родителя.
        clear_gen_queue_halted(reason=f"start_step #{project.id}")
        parent_id = mass_parent_id(project)
        if parent_id is not None:
            parent = await session.get(Project, parent_id)
            if parent is not None:
                clear_mass_family_halt(parent)
    else:
        # Любой старт шага (очередь / цепочка) тоже снимает «ждать ▶».
        from app.services.project_control import clear_auto_await_manual_start

        clear_auto_await_manual_start(project)
    if not skip_queue_guard:
        from app.services.gen_queue import assert_can_start_in_queue

        await assert_can_start_in_queue(session, project)
    step = step_by_code(step_code)
    if step is None and step_code == "excel_gpt":
        from app.telegram.menu import StepDef

        step = StepDef(
            -1,
            "excel_gpt",
            "Доп работа с Excel",
            ProjectStatus.enriching_1,
            ProjectStatus.enrich_1_ready,
            ProjectStatus.hero_ready,
        )
    if step is None:
        raise ValueError(f"unknown step code: {step_code}")
    assert_not_factory_template_for_generation(project)

    if is_running_status(project.status) and project.status is not step.running_status:
        cur_step = step_by_running_status(project.status)
        same_family = cur_step is not None and cur_step.code == step_code
        if step_code == "excel_gpt":
            from app.services.excel_gpt_node import slot_from_running_status

            same_family = slot_from_running_status(project.status) is not None
        if explicit_ui_start and not same_family:
            await _preempt_running_for_manual_start(
                session, project, step.running_status
            )
        elif not explicit_ui_start:
            other_title = cur_step.title if cur_step is not None else project.status.value
            raise ValueError(
                f"сейчас выполняется «{other_title}» ({project.status.value}). "
                "Остановите ⏹ или дождитесь завершения."
            )

    # Ручной старт ранних шагов: сброс stale enrich/split meta + NodeRun
    # downstream → pending. Иначе канвас показывает ✅ на script/split и
    # recompute прыгает plan_ready → frames_ready.
    if explicit_ui_start and step_code in ("plan", "script", "split"):
        from app.services.project_state import clear_pipeline_progress_meta
        from app.services.run_sync import reset_nodes_from_step

        cleared = clear_pipeline_progress_meta(project)
        if cleared:
            logger.info(
                "[#{}] start_step {}: cleared progress meta {}",
                project.id,
                step_code,
                cleared,
            )
        await reset_nodes_from_step(session, project.id, step_code)
        logger.info(
            "[#{}] start_step {}: reset NodeRuns from {} → pending",
            project.id,
            step_code,
            step_code,
        )

    # Ручной старт: порядок нод и data-guard не блокируют запуск.

    if step_code == "anim_pr":
        from sqlalchemy import select

        from app.models import Frame
        from app.services.animation_prompt_gpt import (
            count_animation_prompt_stats,
            scan_missing_animation_prompts,
            sync_animation_prompts_from_xlsx,
        )

        synced = await sync_animation_prompts_from_xlsx(session, project)
        frames = (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )
        ).scalars().all()
        missing = scan_missing_animation_prompts(project, frames)
        if not missing:
            from app.services.project_state import compute_actual_status

            ready, xlsx_filled, with_image = count_animation_prompt_stats(
                project, frames
            )
            project.status = await compute_actual_status(session, project)
            project.updated_at = datetime.utcnow()
            await session.flush()
            logger.info(
                "[#{}] start_step anim_pr: пропуск — нечего генерировать "
                "(synced={}, plan R48={}, картинок={}, status={})",
                project.id,
                synced,
                xlsx_filled,
                with_image,
                project.status.value,
            )
            return project.status

    clear_stop(project.id)
    from app.services.step_failure_policy import clear_failure_backoff_for_manual_start

    if clear_failure_backoff_for_manual_start(
        project, running_key=step.running_status.value
    ):
        logger.info(
            "[#{}] start_step {}: снята пауза после ошибок (ручной запуск)",
            project.id,
            step_code,
        )

    proj_xlsx = project.data_dir / "project.xlsx"
    if proj_xlsx.exists():
        try:
            info = await sync_project_xlsx(session, project, proj_xlsx)
            logger.info(
                "[#{}] start_step {}: synced project.xlsx into DB: {}",
                project.id,
                step_code,
                info,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[#{}] start_step {}: sync_project_xlsx failed: {}",
                project.id,
                step_code,
                e,
            )

    for code in _WRAPPER_TO_CODES.get(step_code, [step_code]):
        purge_tmp_gpt_for_step(project, code)

    meta = dict(project.meta or {})
    cleared: list[str] = []
    if meta.pop("user_stop", None) is not None:
        cleared.append("user_stop")
    if meta.pop("mass_lane_user_stop", None) is not None:
        cleared.append("mass_lane_user_stop")
    if cleared:
        project.meta = meta
        logger.info(
            "[#{}] start_step {}: cleared {}",
            project.id,
            step_code,
            ", ".join(cleared),
        )
    try:
        wiped = await clear_step_outputs_for_rerun(session, project, step_code)
        if wiped:
            logger.info(
                "[#{}] start_step {}: очищены выходы шага перед запуском: {}",
                project.id,
                step_code,
                list(wiped.keys()),
            )
    except Exception as e:  # noqa: BLE001
        logger.exception(
            "[#{}] start_step {}: не удалось очистить выходы шага: {}",
            project.id,
            step_code,
            e,
        )
    running_status = step.running_status
    if step_code == "excel_gpt":
        from app.orchestrator.graph.planner import load_graph_for_project
        from app.services.excel_gpt_node import (
            EXCEL_GPT_NODE_TYPE,
            running_status_for_slot,
            slot_index_from_node,
        )

        meta = dict(project.meta or {})
        graph = await load_graph_for_project(session, project)
        nk = str(node_key or meta.get("active_excel_gpt_node_key") or "")
        if not nk:
            excel_keys = [
                k
                for k, n in graph._by_id.items()
                if str(n.get("type") or "") == EXCEL_GPT_NODE_TYPE
            ]
            if len(excel_keys) == 1:
                nk = excel_keys[0]
            else:
                raise ValueError(
                    "excel_gpt: выберите ноду на канвасе и запустите шаг из меню V "
                    f"(в графе excel_gpt нод: {len(excel_keys)})"
                )
        node = graph._by_id.get(nk)
        if node is None or str(node.get("type") or "") != EXCEL_GPT_NODE_TYPE:
            if "active_excel_gpt_node_key" in meta:
                meta.pop("active_excel_gpt_node_key", None)
                project.meta = meta
                await session.flush()
            raise ValueError(
                f"excel_gpt: нода {nk!r} не найдена в графе — сохраните workflow"
            )
        running_status = running_status_for_slot(slot_index_from_node(node))
        meta["active_excel_gpt_node_key"] = nk
        # Цепочка до последней excel_gpt + сброс «готово» у хвоста —
        # иначе already-done 3-я нода пропускается и не перегенерируется.
        from app.services.excel_gpt_node import (
            clear_excel_gpt_tail_completion,
            ensure_enrich_auto_chain_to,
        )

        started_slot = slot_index_from_node(node)
        project.meta = meta
        cleared = clear_excel_gpt_tail_completion(project, started_slot)
        chain_to = ensure_enrich_auto_chain_to(project, started_slot)
        if cleared.get("slots_cleared") or cleared.get("keys_cleared"):
            logger.info(
                "[#{}] start_step excel_gpt: cleared done for slots>={} "
                "slots={} keys={}",
                project.id,
                started_slot,
                cleared.get("slots_cleared"),
                cleared.get("keys_cleared"),
            )
        if chain_to is not None:
            logger.info(
                "[#{}] start_step excel_gpt: enrich_auto_chain_to={} "
                "(from slot {}, node={})",
                project.id,
                chain_to,
                started_slot,
                nk,
            )
        # NodeRun done → pending для хвоста, иначе UI/FSM думают «уже готово».
        try:
            from app.models import NodeRun, NodeRunStatus, WorkflowRun
            from app.services.node_status_machine import reset_node_to_pending
            from sqlalchemy import select
            from sqlalchemy.orm import selectinload

            run = (
                await session.execute(
                    select(WorkflowRun)
                    .where(WorkflowRun.project_id == project.id)
                    .options(selectinload(WorkflowRun.node_runs))
                )
            ).scalar_one_or_none()
            if run is not None:
                keys_reset = set(cleared.get("keys_cleared") or [])
                if nk:
                    keys_reset.add(nk)
                for nr in run.node_runs:
                    if nr.node_key not in keys_reset:
                        continue
                    if nr.status in (
                        NodeRunStatus.done,
                        NodeRunStatus.failed,
                        NodeRunStatus.waiting_hitl,
                        NodeRunStatus.skipped,
                    ):
                        reset_node_to_pending(
                            nr, project_id=project.id, initiator="ui_restart"
                        )
        except Exception:  # noqa: BLE001
            logger.debug(
                "[#{}] start_step excel_gpt: NodeRun reset skipped",
                project.id,
                exc_info=True,
            )
        try:
            from app.services.step_data_guard import can_enter_running

            ok, reason, _fix = await can_enter_running(
                session, project, running_status
            )
            if not ok:
                logger.warning(
                    "[#{}] start_step excel_gpt: data-guard soft — {} (status={})",
                    project.id,
                    reason,
                    project.status.value,
                )
        except Exception:  # noqa: BLE001
            logger.debug(
                "[#{}] start_step excel_gpt: data-guard skipped",
                project.id,
                exc_info=True,
            )
    from app.services.run_sync import prepare_node_for_step_start

    prepare_key = node_key
    if step_code == "excel_gpt":
        meta = project.meta if isinstance(project.meta, dict) else {}
        prepare_key = str(
            node_key or meta.get("active_excel_gpt_node_key") or ""
        ) or None

    await prepare_node_for_step_start(
        session,
        project,
        step_code,
        node_key=prepare_key,
        strict=require_node_fsm,
        explicit_ui_start=explicit_ui_start,
    )
    project.status = running_status
    project.updated_at = datetime.utcnow()
    await session.flush()
    return project.status
