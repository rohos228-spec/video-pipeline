"""Запуск шагов пайплайна без Telegram (из веб-API)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from loguru import logger

from app.models import Project, ProjectStatus
from app.services.mass_factory import assert_not_factory_template_for_generation
from app.services.disabled_nodes import is_step_disabled
from app.orchestrator.graph.planner import assert_step_allowed_by_graph
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


async def start_step(
    session: AsyncSession,
    project: Project,
    step_code: str,
    *,
    node_key: str | None = None,
    skip_queue_guard: bool = False,
) -> ProjectStatus:
    """Перевести проект в running-статус шага — воркер подхватит."""
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
    if is_step_disabled(project, step_code):
        label = step_code.replace("_", " ")
        raise ValueError(f"шаг «{label}» отключён в графе — включите ноду или выберите другой шаг")
    assert_not_factory_template_for_generation(project)
    await assert_step_allowed_by_graph(session, project, step_code)
    if is_running_status(project.status) and project.status is not step.running_status:
        other = step_by_running_status(project.status)
        other_title = other.title if other is not None else project.status.value
        raise ValueError(
            f"сейчас выполняется «{other_title}» ({project.status.value}). "
            "Остановите ⏹ или дождитесь завершения."
        )

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
        project.meta = meta
    project.status = running_status
    project.updated_at = datetime.utcnow()
    await session.flush()
    return project.status
