"""Ноды scene_design: мульти-агентная режиссура сцен (между split и hero).

Фаза 1 — ``run`` (статус scene_designing, ноды sd_agent на канвасе):
категорийные GPT-агенты (characters/world/camera/action; style удалён)
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

    only_agent = runner.get_only_agent(project)
    try:
        # Волна 0: черновик→редактор скелета (до параллельных категорийных).
        from app.services.scene_design import skeleton as sd_skeleton

        if (
            sd_skeleton.skeleton_pipeline_enabled(project)
            and (not only_agent or only_agent == "skeleton")
        ):
            await sd_skeleton.run_skeleton(session, project, frames)
            await session.commit()

        context = context_builder.build_shared_context(project, frames)
        slices = await runner.run_category_agents(
            project, context, frames=frames, only_agent=only_agent
        )
        # Чекпоинты агентов уже в meta — зафиксировать до записи ячеек.
        await session.commit()

        # Срезы → staging-ячейки (валидация при записи, коммит частями).
        # В only_agent режиме пишем только целевого (остальные — старые чекпоинты).
        full_vo = context_builder.full_voiceover(project, frames)
        cell_stats: dict[str, dict[str, int]] = {}
        to_store = (
            {only_agent: slices[only_agent]}
            if only_agent and only_agent in slices
            else slices
        )
        for agent_name, slice_data in to_store.items():
            converted = sd_cells.slice_to_cells(project, agent_name, slice_data, full_vo)
            cell_stats[agent_name] = await sd_cells.store_cells(
                session, project, agent_name, converted
            )
            # Ответ агента на диск ноды — материал для её ноды проверки.
            _write_sd_reply_file(project, agent_name, slice_data)
        logger.info("[#{}] scene_design cells: {}", project.id, cell_stats)

        if runner.agents_all_done(project):
            runner.mark_agents_done(project)
            project.status = ProjectStatus.scene_agents_ready
        else:
            # Точечный ▶: веер ещё не полный — назад на frames_ready,
            # чтобы можно было запустить следующую ноду. only_agent держим
            # до complete_active_node (пометит только эту ноду done).
            project.status = ProjectStatus.frames_ready
            logger.info(
                "[#{}] scene_design: only_agent={} готов, веер неполный → frames_ready",
                project.id,
                only_agent,
            )
        await session.flush()
        await session.commit()
    except Exception as e:
        runner.mark_failed(project, str(e))
        import contextlib

        with contextlib.suppress(Exception):
            await session.commit()
        raise

    logger.info(
        "[#{}] scene_design agents done: cells={} status={}",
        project.id,
        {k: v.get("stored") for k, v in cell_stats.items()},
        project.status.value,
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
            for name in ("characters", "world", "camera", "action")
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
        # Держим script_text = SoT кадров (иначе UI/старые пути видят урезанный текст).
        if full_vo and (project.script_text or "").strip() != full_vo:
            project.script_text = full_vo
            await session.flush()

        # Ячейки → camera SET дробит VO-диапазон на кадры → сцены (≥VO).
        backfill = await sd_cells.backfill_from_checkpoints(
            session, project, full_vo
        )
        if backfill:
            logger.info(
                "[#{}] scene_asm: cells backfill from checkpoints {}",
                project.id,
                {k: v.get("stored") for k, v in backfill.items()},
            )
        all_cells = await sd_cells.load_cells(session, project)
        assembly_input = sd_chronology.build_assembly_input(
            project, frames, all_cells, full_vo
        )
        if not assembly_input.get("characters"):
            from app.services.scene_design.apply import (
                characters_from_payload_or_checkpoint,
            )

            assembly_input["characters"] = characters_from_payload_or_checkpoint(
                project, {}
            )
        from app.services.scene_design import camera_expand as sd_camera_expand

        # Action-цепи ДО rebuild (rebuild перезапишет scenes_chrono).
        action_scenes = [
            sc
            for sc in (assembly_input.get("scenes_chrono") or [])
            if isinstance(sc, dict)
        ]

        frames, subdiv_report = await sd_camera_expand.subdivide_vo_frames_by_camera(
            session, project, frames, assembly_input, full_vo
        )
        await session.flush()
        logger.info("[#{}] camera_subdivide report: {}", project.id, subdiv_report)
        # shot_plan в assembly_input ещё от исходных VO — rebuild сцен после дроби.
        from app.services.scene_design.agents import uses_chrono_dyn

        assembly_input = sd_camera_expand.rebuild_scenes_from_camera(
            frames,
            assembly_input,
            full_vo,
            chrono_dyn=uses_chrono_dyn(project),
        )
        assembly_input["scenes_chrono"] = sd_assembler.attach_action_chains_to_scenes(
            list(assembly_input.get("scenes_chrono") or []),
            action_scenes,
        )
        assembly_input = sd_assembler.merge_world_style_checkpoints(
            project, assembly_input
        )
        logger.info(
            "[#{}] camera_expand report: {}",
            project.id,
            assembly_input.get("camera_expand_report"),
        )

        payload = None
        feedback: str | None = None
        # chrono_dyn: границы уже из camera_expand; GPT-assemble только жжёт 19 чанков.
        # Локальная склейка из shot_plan/action — без повторных GPT.
        if uses_chrono_dyn(project):
            local = sd_assembler.build_local_assembler_payload(assembly_input, frames)
            problems = sd_assembler.validate_payload(
                project, frames, local, full_vo
            )
            payload = local
            if problems:
                # Качество сценария судит n_excel_gpt_1 (check), не GPT-сборщик.
                logger.warning(
                    "[#{}] scene_design assemble: local chrono_dyn warnings "
                    "(пишу как есть, проверка кино отфильтрует): {}",
                    project.id,
                    problems[:12],
                )
                extra = "; ".join(problems[:8])
                local["report"] = (
                    f"{local.get('report') or 'local_assemble'}; warnings:{extra}"
                )[:800]
            else:
                logger.info(
                    "[#{}] scene_design assemble: local chrono_dyn ok (no GPT)",
                    project.id,
                )
        if payload is None:
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
                    candidate, assembly_input, frames=frames
                )
                problems = sd_assembler.validate_payload(
                    project, frames, candidate, full_vo
                )
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
