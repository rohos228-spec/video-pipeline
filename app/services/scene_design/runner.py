"""Параллельный запуск агентов scene_design + per-agent чекпоинты.

Чекпоинт = JSON на диске ``data/.../scene_design/<agent>.json`` + статус
в ``project.meta["scene_design"]["agents"][name]``. При soft-retry шага
завершённые агенты пропускаются (GPT не дёргается повторно).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.models import Project
from app.services.scene_design import agents as ag
from app.settings import settings


def scene_design_enabled(project: Project | None) -> bool:
    """Per-project override (``meta.scene_design_enabled``) сильнее env."""
    meta = project.meta if project is not None and isinstance(project.meta, dict) else {}
    override = meta.get("scene_design_enabled")
    if override is not None:
        return bool(override)
    return bool(settings.scene_design_enabled)


def scene_design_done(project: Project) -> bool:
    meta = project.meta if isinstance(project.meta, dict) else {}
    sd = meta.get("scene_design")
    return isinstance(sd, dict) and sd.get("status") == "done"


def _state_dir(project: Project) -> Path:
    return project.data_dir / "scene_design"


def _agent_file(project: Project, name: str) -> Path:
    return _state_dir(project) / f"{name}.json"


def _chunks_dir(project: Project, name: str) -> Path:
    return _state_dir(project) / "chunks" / name


def _chunk_file(project: Project, name: str, label: str) -> Path:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in label)
    return _chunks_dir(project, name) / f"{safe}.json"


def load_chunk_checkpoint(
    project: Project, name: str, label: str
) -> dict[str, Any] | None:
    """Готовый JSON одного proactive-чанка (не дёргать GPT повторно)."""
    path = _chunk_file(project, name, label)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    # assemble: нужны и scenes[], и ops[] (не один LIST_KEY).
    if name == ag.ASSEMBLER:
        scenes = data.get("scenes")
        ops = data.get("ops")
        if not isinstance(scenes, list) or not scenes:
            return None
        if not isinstance(ops, list) or not ops:
            return None
        return data
    list_key = ag.LIST_KEY.get(name)
    if (
        not list_key
        or not isinstance(data.get(list_key), list)
        or not data[list_key]
    ):
        return None
    return data


def save_chunk_checkpoint(
    project: Project, name: str, label: str, data: dict[str, Any]
) -> None:
    d = _chunks_dir(project, name)
    d.mkdir(parents=True, exist_ok=True)
    _chunk_file(project, name, label).write_text(
        json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8"
    )


def clear_chunk_checkpoints(project: Project, name: str) -> None:
    d = _chunks_dir(project, name)
    if not d.is_dir():
        return
    for p in d.glob("*.json"):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        d.rmdir()
    except OSError:
        pass


def _meta_state(project: Project) -> dict[str, Any]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    sd = meta.get("scene_design")
    return dict(sd) if isinstance(sd, dict) else {}


def _save_state(project: Project, sd: dict[str, Any]) -> None:
    meta = dict(project.meta or {})
    meta["scene_design"] = sd
    project.meta = meta


def get_only_agent(project: Project) -> str | None:
    """Точечный ▶ с ноды веера: имя агента или None (полный прогон)."""
    sd = _meta_state(project)
    name = str(sd.get("only_agent") or "").strip()
    return name or None


def set_only_agent(project: Project, name: str | None) -> None:
    """Запомнить / сбросить only_agent для per-node ▶."""
    sd = _meta_state(project)
    if name:
        sd["only_agent"] = str(name).strip()
    else:
        sd.pop("only_agent", None)
    _save_state(project, sd)


def clear_only_agent(project: Project) -> None:
    set_only_agent(project, None)


def resolve_sd_node_key(project: Project, agent: str) -> str | None:
    """node_key канваса с data.sd_agent == agent."""
    from app.services.canvas_graph import canvas_graph_from_meta
    from app.services.excel_gpt_node import sd_agent_marker

    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta) or {}
    for n in cg.get("nodes") or []:
        if sd_agent_marker(n) == agent:
            key = str(n.get("id") or "").strip()
            if key:
                return key
    return None


def load_checkpoint(project: Project, name: str) -> dict[str, Any] | None:
    """Готовый срез агента с прошлого прогона (soft retry без повторного GPT)."""
    sd = _meta_state(project)
    agents_meta = sd.get("agents") if isinstance(sd.get("agents"), dict) else {}
    info = agents_meta.get(name)
    if not isinstance(info, dict) or info.get("status") != "done":
        return None
    path = _agent_file(project, name)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    list_key = ag.LIST_KEY[name]
    if (
        not isinstance(data, dict)
        or not isinstance(data.get(list_key), list)
        or not data[list_key]
    ):
        return None
    return data


def save_checkpoint(project: Project, name: str, data: dict[str, Any]) -> None:
    _state_dir(project).mkdir(parents=True, exist_ok=True)
    _agent_file(project, name).write_text(
        json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    # Полный агент готов — промежуточные чанки больше не нужны.
    clear_chunk_checkpoints(project, name)
    sd = _meta_state(project)
    agents_meta = dict(sd.get("agents") or {})
    agents_meta[name] = {
        "status": "done",
        "at": datetime.now(UTC).isoformat(),
        "path": f"scene_design/{name}.json",
    }
    sd["agents"] = agents_meta
    _save_state(project, sd)


def mark_running(project: Project) -> None:
    sd = _meta_state(project)
    sd["status"] = "running"
    sd["started_at"] = datetime.now(UTC).isoformat()
    sd.pop("error", None)
    _save_state(project, sd)


def mark_failed(project: Project, error: str) -> None:
    sd = _meta_state(project)
    sd["status"] = "failed"
    sd["error"] = (error or "")[:500]
    _save_state(project, sd)


def mark_done(project: Project, report: str) -> None:
    sd = _meta_state(project)
    sd["status"] = "done"
    sd["finished_at"] = datetime.now(UTC).isoformat()
    sd["report"] = (report or "")[:2000]
    sd.pop("error", None)
    _save_state(project, sd)


def mark_agents_done(project: Project) -> None:
    """Фаза агентов завершена: срезы + ячейки записаны, сборка ещё впереди."""
    sd = _meta_state(project)
    sd["status"] = "agents_done"
    sd["agents_finished_at"] = datetime.now(UTC).isoformat()
    sd.pop("error", None)
    _save_state(project, sd)


def mark_assemble_running(project: Project) -> None:
    sd = _meta_state(project)
    sd["status"] = "assembling"
    sd.pop("error", None)
    _save_state(project, sd)


def invalidate_agent(project: Project, name: str) -> bool:
    """Сбросить чекпоинт одного агента (per-agent перезапуск с ноды канваса).

    Удаляет meta-статус и файл ``scene_design/<agent>.json``; ячейки агента
    перезапишет ``store_cells`` при следующем прогоне. Возвращает True,
    если агент известен.
    """
    if name not in ag.ALL_AGENTS:
        return False
    sd = _meta_state(project)
    agents_meta = dict(sd.get("agents") or {})
    agents_meta.pop(name, None)
    sd["agents"] = agents_meta
    # Сборка стала stale — статус больше не «done».
    if sd.get("status") == "done":
        sd["status"] = "agents_done"
        sd.pop("report", None)
        sd.pop("finished_at", None)
    _save_state(project, sd)
    path = _agent_file(project, name)
    if path.is_file():
        path.unlink()
    clear_chunk_checkpoints(project, name)
    return True


def agents_all_done(project: Project) -> bool:
    """Все агенты текущих волн (скелет + категории) имеют валидные чекпоинты."""
    needed = [a for wave in ag.agent_waves(project) for a in wave]
    return all(load_checkpoint(project, name) is not None for name in needed)


async def _run_one_agent(
    project: Project,
    name: str,
    context: str,
    *,
    timeout: float,
    validate: bool = True,
    max_retries: int | None = None,
) -> dict[str, Any]:
    from app.services import gpt_client

    prompt = ag.load_prompt(name, project)
    text = f"{prompt}\n\n---\n\n{context}"
    reply = await gpt_client.gpt_ask_fresh(
        text,
        timeout=timeout,
        project_id=project.id,
        max_retries=max_retries,
    )
    return ag.parse_agent_slice(name, reply, validate=validate)


def _append_slice_context(
    base: str, *, title: str, payload: dict[str, Any] | None
) -> str:
    if not isinstance(payload, dict) or not payload:
        return base
    return (
        base
        + f"\n\n# {title}\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


async def _run_one_agent_adaptive(
    project: Project,
    name: str,
    *,
    frames: list[Any],
    full_frames: list[Any],
    slice_extras: list[tuple[str, dict[str, Any]]],
    action_scenes: list[Any] | None,
    timeout: float,
    depth: int = 0,
    label: str = "full",
) -> dict[str, Any]:
    """action/camera: сразу куски ≤N кадров (параллель), иначе 524→/2→/4."""
    from app.services.scene_design import agent_chunks as ach
    from app.services.scene_design import context_builder
    from app.settings import settings

    frame_list = [f for f in frames if getattr(f, "uuid", None)]
    full_list = [f for f in full_frames if getattr(f, "uuid", None)]
    max_chunk = int(settings.scene_design_agent_chunk_frames)
    parallel = max(1, int(settings.scene_design_agent_chunk_parallel))

    async def _run_parts(
        batches: list[list[Any]], *, child_depth: int, prefix: str
    ) -> dict[str, Any]:
        sem = asyncio.Semaphore(parallel)

        async def _one(i: int, batch: list[Any]) -> dict[str, Any]:
            part_label = f"{prefix}{i}"
            # Proactive-чанки: переживают soft-retry / 500 / рестарт.
            if prefix == "p":
                cached = load_chunk_checkpoint(project, name, part_label)
                if cached is not None:
                    logger.info(
                        "[#{}] scene_design/{}: chunk {} — checkpoint, skip GPT",
                        project.id,
                        name,
                        part_label,
                    )
                    return cached
            async with sem:
                data = await _run_one_agent_adaptive(
                    project,
                    name,
                    frames=batch,
                    full_frames=full_list,
                    slice_extras=slice_extras,
                    action_scenes=action_scenes,
                    timeout=timeout,
                    depth=child_depth,
                    label=part_label,
                )
            if prefix == "p":
                try:
                    save_chunk_checkpoint(project, name, part_label, data)
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "[#{}] scene_design/{}: chunk ckpt save failed {}",
                        project.id,
                        name,
                        part_label,
                        exc_info=True,
                    )
            return data

        parts = await asyncio.gather(
            *(_one(i, b) for i, b in enumerate(batches, start=1))
        )
        return ach.merge_agent_slices(name, list(parts))

    # Проактивно: не слать 65 кадров целиком (5 мин → 524 впустую).
    if (
        depth == 0
        and max_chunk > 1
        and len(frame_list) > max_chunk
        and name in ach.SPLITTABLE_AGENTS
    ):
        batches = ach.split_frames_batches(frame_list, max_frames=max_chunk)
        logger.info(
            "[#{}] scene_design/{}: proactive {} chunks ×≤{} frames (parallel={})",
            project.id,
            name,
            len(batches),
            max_chunk,
            parallel,
        )
        return await _run_parts(batches, child_depth=1, prefix="p")

    slim = depth > 0
    ctx = context_builder.build_shared_context(
        project, frame_list, mode="chunk" if slim else "full"
    )
    for title, payload in slice_extras:
        # camera: полный ACTION в extras не тащим — ниже фильтр по чанку.
        if name == "camera" and title.startswith("ACTION SCENES"):
            continue
        use_payload = (
            context_builder.compact_slice_payload(title, payload)
            if slim and isinstance(payload, dict)
            else payload
        )
        ctx = _append_slice_context(ctx, title=title, payload=use_payload)
    if name == "camera" and action_scenes is not None:
        full_vo = context_builder.full_voiceover(project, full_list)
        filtered = ach.filter_action_scenes_for_frames(
            list(action_scenes), frame_list, full_list, full_vo
        )
        ctx = _append_slice_context(
            ctx,
            title="ACTION SCENES (JSON) — закон фаз для camera",
            payload={"scenes": filtered},
        )
    if depth > 0:
        ctx = ctx + "\n\n" + ach.chunk_instruction(label=label, depth=depth)

    attempt_to = float(settings.scene_design_agent_attempt_timeout_s)
    call_timeout = (
        min(float(timeout), attempt_to) if attempt_to > 0 else float(timeout)
    )

    async def _call_once() -> dict[str, Any]:
        # max_retries=0: первый 524 сразу в /2 split, не жечь GPT_MAX_RETRIES.
        return await _run_one_agent(
            project,
            name,
            ctx,
            timeout=call_timeout,
            validate=(depth == 0),
            max_retries=0,
        )

    try:
        return await _call_once()
    except Exception as e:  # noqa: BLE001
        if ach.is_credits_failure(e):
            raise ach.CreditsExhausted(
                f"scene_design/{name}: нет кредитов GPT (402) — "
                f"пополни баланс; retry/split отключены. {e}"
            ) from e
        # 500/502/503: один повтор того же чанка (без /2), потом raise.
        if ach.is_transient_server_failure(e):
            logger.warning(
                "[#{}] scene_design/{}: {} — same-chunk retry (no split)",
                project.id,
                name,
                e,
            )
            await asyncio.sleep(2.0)
            try:
                return await _call_once()
            except Exception as e2:  # noqa: BLE001
                if ach.is_credits_failure(e2):
                    raise ach.CreditsExhausted(
                        f"scene_design/{name}: нет кредитов GPT (402) — "
                        f"пополни баланс; retry/split отключены. {e2}"
                    ) from e2
                e = e2
        can_split = (
            name in ach.SPLITTABLE_AGENTS
            and ach.is_capacity_failure(e)
            and depth < ach.MAX_SPLIT_DEPTH
            and len(frame_list) >= 2
        )
        if not can_split:
            if (
                name in ach.SPLITTABLE_AGENTS
                and ach.is_capacity_failure(e)
                and depth >= ach.MAX_SPLIT_DEPTH
            ):
                raise ach.CapacitySplitExhausted(
                    f"scene_design/{name}: capacity-failure после "
                    f"{ach.MAX_SPLIT_DEPTH} дроблений (/4) — {e}"
                ) from e
            raise
        left, right = ach.split_frames_half(frame_list)
        logger.warning(
            "[#{}] scene_design/{}: {} — split depth {}→{} frames {}+{} (parallel)",
            project.id,
            name,
            e,
            depth,
            depth + 1,
            len(left),
            len(right),
        )
        return await _run_parts(
            [left, right],
            child_depth=depth + 1,
            prefix=(f"{label}·" if depth else ""),
        )


async def run_category_agents(
    project: Project,
    context: str,
    *,
    frames: list[Any] | None = None,
    timeout: float = 900,
    only_agent: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Категорийные агенты волнами.

    Legacy: chars/world/style/action → camera.
    chrono_dyn: chars/world/style → action → camera (как на канвасе).
    Чекпоинтнутые пропускаются.

    ``only_agent`` / meta.scene_design.only_agent — точечный ▶ с ноды:
    GPT только для этого агента; остальные из чекпоинта или пропуск
    (без вызова GPT). Нужно, чтобы ▶ скелета не поднимал весь веер.

    ``frames`` — для adaptive-split action/camera при 524 (дробим закадр /2,/4).
    """
    from app.services.scene_design import agent_chunks as ach

    sem = asyncio.Semaphore(max(1, int(settings.scene_design_max_parallel)))
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    waves = ag.agent_waves(project)
    chrono = ag.uses_chrono_dyn(project)
    frame_list = [f for f in (frames or []) if getattr(f, "uuid", None)]
    target = (only_agent or get_only_agent(project) or "").strip() or None
    if target:
        logger.info(
            "[#{}] scene_design: only_agent={} — GPT только для этой ноды",
            project.id,
            target,
        )

    async def _one(
        name: str,
        ctx: str,
        *,
        slice_extras: list[tuple[str, dict[str, Any]]] | None = None,
        action_scenes: list[Any] | None = None,
    ) -> None:
        cached = load_checkpoint(project, name)
        if cached is not None:
            logger.info(
                "[#{}] scene_design/{}: checkpoint — пропуск GPT", project.id, name
            )
            results[name] = cached
            return
        if target and name != target:
            logger.info(
                "[#{}] scene_design/{}: skip GPT (only_agent={})",
                project.id,
                name,
                target,
            )
            return
        async with sem:
            try:
                if name in ach.SPLITTABLE_AGENTS and frame_list:
                    data = await _run_one_agent_adaptive(
                        project,
                        name,
                        frames=frame_list,
                        full_frames=frame_list,
                        slice_extras=list(slice_extras or []),
                        action_scenes=action_scenes,
                        timeout=timeout,
                    )
                else:
                    data = await _run_one_agent(project, name, ctx, timeout=timeout)
            except Exception as e:  # noqa: BLE001
                errors[name] = str(e)
                logger.warning("[#{}] scene_design/{} failed: {}", project.id, name, e)
                return
        save_checkpoint(project, name, data)
        results[name] = data
        logger.info("[#{}] scene_design/{}: ok", project.id, name)

    skeleton_on = ag.uses_skeleton(project)
    for wave_i, wave in enumerate(waves):
        ctx = context
        extras: list[tuple[str, dict[str, Any]]] = []
        action_scenes: list[Any] | None = None
        # Скелет (волна 0) — во все последующие волны.
        if skeleton_on and wave != ag.SKELETON_WAVE:
            sk = results.get(ag.SKELETON)
            if isinstance(sk, dict):
                title = "SKELETON (JSON) — закон сцен/нитей"
                extras.append((title, sk))
                ctx = _append_slice_context(ctx, title=title, payload=sk)
        chrono_wave1_i = 1 if skeleton_on else 0
        if chrono and wave_i == chrono_wave1_i + 1:
            # action видит паспорта верхних трёх
            for name in ag.CHRONO_WAVE1_AGENTS:
                slice_data = results.get(name)
                if isinstance(slice_data, dict):
                    title = f"{name.upper()} SLICE (JSON)"
                    extras.append((title, slice_data))
                    ctx = _append_slice_context(ctx, title=title, payload=slice_data)
        if wave == ag.WAVE2_AGENTS or wave == ag.CHRONO_WAVE3_AGENTS:
            action_slice = results.get("action")
            if isinstance(action_slice, dict) and action_slice.get("scenes"):
                title = "ACTION SCENES (JSON) — закон фаз для camera"
                extras.append((title, action_slice))
                action_scenes = list(action_slice.get("scenes") or [])
                ctx = _append_slice_context(ctx, title=title, payload=action_slice)
        logger.info(
            "[#{}] scene_design wave {}: {}",
            project.id,
            wave_i + 1,
            ",".join(wave),
        )
        await asyncio.gather(
            *(
                _one(n, ctx, slice_extras=extras, action_scenes=action_scenes)
                for n in wave
            )
        )
        # Жёсткий стоп: упала текущая волна — дальше не идём.
        # В only_agent режиме смотрим только целевого агента.
        wave_failed = [
            n for n in wave if n in errors and (not target or n == target)
        ]
        if wave_failed:
            failed = ", ".join(sorted(errors))
            raise ag.SceneDesignAgentError(
                f"scene_design: агенты упали ({failed}): "
                + "; ".join(f"{k}: {v}" for k, v in sorted(errors.items()))[:400]
            )
        # Точечный ▶: после волны с целевым агентом дальше не идём.
        if target and target in wave:
            break

    if errors:
        if target:
            errors = {k: v for k, v in errors.items() if k == target}
        if errors:
            failed = ", ".join(sorted(errors))
            raise ag.SceneDesignAgentError(
                f"scene_design: агенты упали ({failed}): "
                + "; ".join(f"{k}: {v}" for k, v in sorted(errors.items()))[:400]
            )
    return results


async def run_assembler(
    project: Project,
    context: str,
    assembly_input: dict[str, Any],
    *,
    feedback: str | None = None,
    timeout: float = 1200,
    max_retries: int | None = None,
) -> dict[str, Any]:
    """Финальный агент-сборщик: упорядоченные ячейки + кадры → {characters, scenes, ops}.

    ``assembly_input`` — выход ``chronology.build_assembly_input``: сцены,
    шоты и этапы стиля уже в хронологии закадра, кадры привязаны к сценам.
    Для больших роликов вызывай ``run_assembler_chunked``.
    """
    from app.services import gpt_client

    prompt = ag.load_prompt(ag.ASSEMBLER, project)
    cells_json = json.dumps(assembly_input, ensure_ascii=False, indent=1)
    parts = [
        prompt,
        "---",
        context,
        f"# ЯЧЕЙКИ АГЕНТОВ (JSON, хронологический порядок, кадры привязаны к сценам)\n{cells_json}",
    ]
    if feedback:
        parts.append(f"# ОШИБКИ ПРОШЛОЙ СБОРКИ (исправь)\n{feedback}")
    reply = await gpt_client.gpt_ask_fresh(
        "\n\n".join(parts),
        timeout=timeout,
        project_id=project.id,
        max_retries=max_retries,
    )
    return ag.parse_assembler_payload(reply)


async def run_assembler_chunked(
    project: Project,
    frames: list[Any],
    full_vo: str,
    assembly_input: dict[str, Any],
    *,
    max_frames: int | None = None,
    feedback: str | None = None,
    timeout: float = 900,
) -> dict[str, Any]:
    """Сборка кусками: несколько GPT-вызовов → merge → один payload.

    ``max_frames`` — размер чанка (Frame-строки пайплайна). ``None`` → из settings.
    При ``max_frames <= 1`` — один вызов на весь ролик (legacy).
    """
    from app.models import Frame
    from app.services.scene_design import assemble_chunks as chunks
    from app.settings import settings

    frame_list = [f for f in frames if isinstance(f, Frame)]
    n = (
        int(max_frames)
        if max_frames is not None
        else int(settings.scene_design_assemble_chunk_frames)
    )
    if n <= 1 or len(frame_list) <= n:
        # Legacy: полный shared context + весь assembly_input.
        from app.services.scene_design import context_builder

        context = context_builder.build_shared_context(project, frame_list)
        return await run_assembler(
            project, context, assembly_input, feedback=feedback, timeout=timeout
        )

    from app.services.scene_design import agent_chunks as ach

    batches = chunks.split_frame_batches(frame_list, max_frames=n)
    logger.info(
        "[#{}] scene_design assemble chunked: {} chunks ×≤{} frames (total {})",
        project.id,
        len(batches),
        n,
        len(frame_list),
    )
    parts: list[dict[str, Any]] = []
    total = len(batches)

    async def _assemble_batch(
        batch: list[Any],
        *,
        chunk_index: int,
        include_characters: bool,
        fb: str | None,
        depth: int = 0,
        label: str = "",
    ) -> dict[str, Any]:
        chunk_input = chunks.filter_assembly_input_for_frames(
            assembly_input,
            batch,
            frame_list,
            full_vo,
            include_characters=include_characters,
        )
        chunk_ctx = chunks.build_chunk_context(
            full_vo, batch, chunk_index=chunk_index, chunk_total=total
        )
        if depth > 0:
            chunk_ctx = (
                chunk_ctx
                + "\n\n"
                + ach.chunk_instruction(label=label or str(chunk_index), depth=depth)
            )
        try:
            # На adaptive-path: 1×524 → сразу half-split, без GPT_MAX_RETRIES кругов.
            return await run_assembler(
                project,
                chunk_ctx,
                chunk_input,
                feedback=fb,
                timeout=timeout,
                max_retries=0,
            )
        except Exception as e:  # noqa: BLE001
            if (
                ach.is_capacity_failure(e)
                and depth < ach.MAX_SPLIT_DEPTH
                and len(batch) >= 2
            ):
                left, right = ach.split_frames_half(batch)
                logger.warning(
                    "[#{}] scene_design assemble chunk {}: {} — split depth {}→{}",
                    project.id,
                    chunk_index,
                    e,
                    depth,
                    depth + 1,
                )
                await asyncio.sleep(1.5)
                left_part = await _assemble_batch(
                    left,
                    chunk_index=chunk_index,
                    include_characters=include_characters,
                    fb=fb,
                    depth=depth + 1,
                    label=f"{label or chunk_index}·a",
                )
                await asyncio.sleep(1.5)
                right_part = await _assemble_batch(
                    right,
                    chunk_index=chunk_index,
                    include_characters=False,
                    fb=None,
                    depth=depth + 1,
                    label=f"{label or chunk_index}·b",
                )
                return chunks.merge_assembler_payloads([left_part, right_part])
            if ach.is_capacity_failure(e) and depth >= ach.MAX_SPLIT_DEPTH:
                raise ach.CapacitySplitExhausted(
                    f"scene_design/assemble: capacity-failure после "
                    f"{ach.MAX_SPLIT_DEPTH} дроблений чанка {chunk_index} — {e}"
                ) from e
            raise

    for i, batch in enumerate(batches, start=1):
        label = f"p{i}"
        cached = load_chunk_checkpoint(project, ag.ASSEMBLER, label)
        if cached is not None:
            logger.info(
                "[#{}] scene_design assemble chunk {}/{} frames={} — checkpoint",
                project.id,
                i,
                total,
                [f.number for f in batch],
            )
            parts.append(cached)
            continue
        logger.info(
            "[#{}] scene_design assemble chunk {}/{} frames={}",
            project.id,
            i,
            total,
            [f.number for f in batch],
        )
        part = await _assemble_batch(
            batch,
            chunk_index=i,
            include_characters=(i == 1),
            fb=(feedback if i == 1 else None),
        )
        save_chunk_checkpoint(project, ag.ASSEMBLER, label, part)
        logger.info(
            "[#{}] scene_design assemble chunk {}/{} ok: scenes={} ops={}",
            project.id,
            i,
            total,
            len(part.get("scenes") or []),
            len(part.get("ops") or []),
        )
        parts.append(part)

    merged = chunks.merge_assembler_payloads(parts)
    logger.info(
        "[#{}] scene_design assemble merge: parts={} scenes={} ops={}",
        project.id,
        len(parts),
        len(merged.get("scenes") or []),
        len(merged.get("ops") or []),
    )
    if not merged.get("scenes") or not merged.get("ops"):
        raise ag.SceneDesignAgentError(
            "scene_design/assemble: после склейки чанков пустой scenes/ops "
            f"(parts={len(parts)}; "
            f"scenes={[len(p.get('scenes') or []) for p in parts if isinstance(p, dict)]}; "
            f"ops={[len(p.get('ops') or []) for p in parts if isinstance(p, dict)]})"
        )
    clear_chunk_checkpoints(project, ag.ASSEMBLER)
    return merged
