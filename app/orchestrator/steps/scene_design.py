"""Ноды scene_design: мульти-агентная режиссура сцен (между split и hero).

Фаза 1 — ``run`` (статус scene_designing, ноды sd_agent ×5 на канвасе):
5 категорийных GPT-агентов параллельно (characters/world/style/camera/action)
→ срезы конвертируются в staging-ячейки (``scene_design_cells``, валидация
при записи, коммит частями) → статус scene_agents_ready.

Фаза 2 — ``run_assemble`` (статус scene_assembling, нода sd_assemble):
ячейки → хронологическая выкладка по закадру с привязкой кадров к сценам
→ финальный агент-сборщик → валидация → apply-ops (scene_registry + attrs
кадров) → scene_design_ready. В боевые таблицы пишет только сборка.

Выключена (SCENE_DESIGN_ENABLED=false и нет per-project override) —
обе фазы прозрачный pass-through.
"""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, ProjectStatus
from app.storage import for_project as _sheet_for_project

_MAX_ASSEMBLE_ATTEMPTS = 2


async def _load_frames(session: AsyncSession, project: Project) -> list[Frame]:
    return (
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )


def _sd_node_ids_by_marker(project: Project) -> dict[str, str]:
    """marker (characters/…/assemble) → node id на канвасе проекта."""
    from app.services.canvas_graph import canvas_graph_from_meta
    from app.services.excel_gpt_node import sd_agent_marker

    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta) or {}
    out: dict[str, str] = {}
    for n in cg.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        marker = sd_agent_marker(n)
        nid = str(n.get("id") or "").strip()
        if marker and nid:
            out[marker] = nid
    return out


def _write_sd_reply_file(project: Project, marker: str, payload: object) -> None:
    """Ответ агента/сборщика → excel_gpt_uploads/<нода>/ — вход для ноды проверки
    (files_from_source_node для excel_gpt сканирует именно эту папку)."""
    import json

    from app.services.excel_gpt_node import upload_dir

    nid = _sd_node_ids_by_marker(project).get(marker)
    if not nid:
        return
    try:
        udir = upload_dir(project, nid)
        udir.mkdir(parents=True, exist_ok=True)
        (udir / f"sd_{marker}_reply.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[#{}] scene_design: reply-файл для {} не записан: {}",
            project.id,
            marker,
            e,
        )


async def run(session: AsyncSession, project: Project, bot: Bot | None = None) -> None:
    """Фаза 1: категорийные агенты (параллельно) → staging-ячейки."""
    if project.status is not ProjectStatus.scene_designing:
        return
    from app.services import db_v2
    from app.services.scene_design import cells as sd_cells
    from app.services.scene_design import context_builder, runner

    logger.info("[#{}] scene_design agents phase starting", project.id)

    if not runner.scene_design_enabled(project):
        logger.info("[#{}] scene_design disabled — pass-through", project.id)
        project.status = ProjectStatus.scene_agents_ready
        await session.flush()
        await session.commit()
        return

    await db_v2.backfill_project_v2(session, project)
    frames = await _load_frames(session, project)
    if not frames:
        raise RuntimeError("scene_design: нет кадров — сначала split")
    if any(not fr.uuid for fr in frames):
        raise RuntimeError("scene_design: у кадров нет uuid — backfill не сработал")

    runner.mark_running(project)
    await session.flush()
    # Отпустить SQLite write-lock на время параллельных GPT-вызовов.
    await session.commit()

    try:
        context = context_builder.build_shared_context(project, frames)
        slices = await runner.run_category_agents(project, context)
        # Чекпоинты агентов уже в meta — зафиксировать до записи ячеек.
        await session.commit()

        # Срезы → staging-ячейки (валидация при записи, коммит частями).
        full_vo = context_builder.full_voiceover(project, frames)
        cell_stats: dict[str, dict[str, int]] = {}
        for agent_name, slice_data in slices.items():
            converted = sd_cells.slice_to_cells(project, agent_name, slice_data, full_vo)
            cell_stats[agent_name] = await sd_cells.store_cells(
                session, project, agent_name, converted
            )
            # Ответ агента на диск ноды — материал для её ноды проверки.
            _write_sd_reply_file(project, agent_name, slice_data)
        logger.info("[#{}] scene_design cells: {}", project.id, cell_stats)

        runner.mark_agents_done(project)
        project.status = ProjectStatus.scene_agents_ready
        await session.flush()
        await session.commit()
    except Exception as e:
        runner.mark_failed(project, str(e))
        import contextlib

        with contextlib.suppress(Exception):
            await session.commit()
        raise

    logger.info(
        "[#{}] scene_design agents done: cells={}",
        project.id,
        {k: v.get("stored") for k, v in cell_stats.items()},
    )

    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet scene_design write failed: {}", project.id, e)

    from app.services.agent_harness import harness_gate_or_raise

    await harness_gate_or_raise(session, project, step="scene_d")
    await session.commit()


async def run_assemble(
    session: AsyncSession, project: Project, bot: Bot | None = None
) -> None:
    """Фаза 2: финальный агент-сборщик → scene_registry + attrs кадров."""
    if project.status is not ProjectStatus.scene_assembling:
        return
    from app.services.scene_design import apply as sd_apply
    from app.services.scene_design import assembler as sd_assembler
    from app.services.scene_design import cells as sd_cells
    from app.services.scene_design import chronology as sd_chronology
    from app.services.scene_design import context_builder, runner

    logger.info("[#{}] scene_design assemble phase starting", project.id)

    if not runner.scene_design_enabled(project):
        logger.info("[#{}] scene_design disabled — pass-through", project.id)
        project.status = ProjectStatus.scene_design_ready
        await session.flush()
        await session.commit()
        return

    frames = await _load_frames(session, project)
    if not frames:
        raise RuntimeError("scene_asm: нет кадров — сначала split")
    if not runner.agents_all_done(project):
        missing = [
            name
            for name in ("characters", "world", "style", "camera", "action")
            if runner.load_checkpoint(project, name) is None
        ]
        raise RuntimeError(
            f"scene_asm: агенты ещё не отработали ({', '.join(missing)}) — "
            "сначала запустите ноды агентов (scene_d)"
        )

    runner.mark_assemble_running(project)
    await session.flush()
    await session.commit()

    try:
        full_vo = context_builder.full_voiceover(project, frames)

        # Ячейки → camera SET дробит VO-диапазон на кадры → сцены (≥VO).
        all_cells = await sd_cells.load_cells(session, project)
        assembly_input = sd_chronology.build_assembly_input(
            project, frames, all_cells, full_vo
        )
        from app.services.scene_design import camera_expand as sd_camera_expand

        frames, subdiv_report = await sd_camera_expand.subdivide_vo_frames_by_camera(
            session, project, frames, assembly_input, full_vo
        )
        await session.flush()
        logger.info("[#{}] camera_subdivide report: {}", project.id, subdiv_report)
        # shot_plan в assembly_input ещё от исходных VO — rebuild сцен после дроби.
        assembly_input = sd_camera_expand.rebuild_scenes_from_camera(
            frames, assembly_input, full_vo
        )
        logger.info(
            "[#{}] camera_expand report: {}",
            project.id,
            assembly_input.get("camera_expand_report"),
        )

        payload = None
        feedback: str | None = None
        for attempt in range(1, _MAX_ASSEMBLE_ATTEMPTS + 1):
            # Чанки по Frame-строкам — один жирный GPT-запрос ломает kie (524).
            candidate = await runner.run_assembler_chunked(
                project,
                frames,
                full_vo,
                assembly_input,
                feedback=feedback,
            )
            # Границы сцен — из camera_expand, не из GPT (ломает цитаты в чанках).
            candidate = sd_assembler.force_scenes_from_chrono(
                candidate, assembly_input
            )
            problems = sd_assembler.validate_payload(project, frames, candidate, full_vo)
            if not problems:
                payload = candidate
                break
            feedback = "\n".join(problems)
            logger.warning(
                "[#{}] scene_design assemble attempt {}/{} problems: {}",
                project.id,
                attempt,
                _MAX_ASSEMBLE_ATTEMPTS,
                problems,
            )
        if payload is None:
            raise RuntimeError(
                f"scene_design: сборка не прошла валидацию: {feedback}"
            )

        applied = await sd_apply.apply_scene_design(session, project, payload)
        # Пayload сборщика на диск ноды — материал для «Проверка: сборка сцен».
        _write_sd_reply_file(project, "assemble", payload)
        runner.mark_done(project, str(payload.get("report") or ""))
        project.status = ProjectStatus.scene_design_ready
        await session.flush()
        await session.commit()
    except Exception as e:
        runner.mark_failed(project, str(e))
        import contextlib

        with contextlib.suppress(Exception):
            await session.commit()
        raise

    logger.info(
        "[#{}] scene_design assemble done: ops={} characters={} scenes={}",
        project.id,
        applied.get("updated"),
        applied.get("characters"),
        applied.get("scenes"),
    )

    try:
        from app.services.storage_step_sync import sync_storage_after_step

        await sync_storage_after_step(
            session, project, "scene_design", log_prefix="scene_design"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] scene_design: storage sync failed: {}", project.id, e)

    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet scene_design write failed: {}", project.id, e)

    from app.services.agent_harness import harness_gate_or_raise

    await harness_gate_or_raise(session, project, step="scene_asm")
    await session.commit()
