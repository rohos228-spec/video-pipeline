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


def _meta_state(project: Project) -> dict[str, Any]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    sd = meta.get("scene_design")
    return dict(sd) if isinstance(sd, dict) else {}


def _save_state(project: Project, sd: dict[str, Any]) -> None:
    meta = dict(project.meta or {})
    meta["scene_design"] = sd
    project.meta = meta


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
    if name not in ag.CATEGORY_AGENTS:
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
    return True


def agents_all_done(project: Project) -> bool:
    """Все 5 категорийных агентов имеют валидные чекпоинты."""
    return all(load_checkpoint(project, name) is not None for name in ag.CATEGORY_AGENTS)


async def _run_one_agent(
    project: Project, name: str, context: str, *, timeout: float
) -> dict[str, Any]:
    from app.services import gpt_client

    prompt = ag.load_prompt(name, project)
    text = f"{prompt}\n\n---\n\n{context}"
    reply = await gpt_client.gpt_ask_fresh(text, timeout=timeout, project_id=project.id)
    return ag.parse_agent_slice(name, reply)


async def run_category_agents(
    project: Project,
    context: str,
    *,
    timeout: float = 900,
) -> dict[str, dict[str, Any]]:
    """Категорийные агенты: волна1 (characters/world/style/action) → camera.

    Camera всегда после action (нужны фазы). Чекпоинтнутые пропускаются.
    """
    sem = asyncio.Semaphore(max(1, int(settings.scene_design_max_parallel)))
    results: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}

    async def _one(name: str, ctx: str) -> None:
        cached = load_checkpoint(project, name)
        if cached is not None:
            logger.info(
                "[#{}] scene_design/{}: checkpoint — пропуск GPT", project.id, name
            )
            results[name] = cached
            return
        async with sem:
            try:
                data = await _run_one_agent(project, name, ctx, timeout=timeout)
            except Exception as e:  # noqa: BLE001
                errors[name] = str(e)
                logger.warning("[#{}] scene_design/{} failed: {}", project.id, name, e)
                return
        save_checkpoint(project, name, data)
        results[name] = data
        logger.info("[#{}] scene_design/{}: ok", project.id, name)

    await asyncio.gather(*(_one(n, context) for n in ag.WAVE1_AGENTS))
    if any(a in errors for a in ("action",)):
        failed = ", ".join(sorted(errors))
        raise ag.SceneDesignAgentError(
            f"scene_design: агенты упали ({failed}): "
            + "; ".join(f"{k}: {v}" for k, v in sorted(errors.items()))[:400]
        )

    camera_ctx = context
    action_slice = results.get("action")
    if isinstance(action_slice, dict) and action_slice.get("scenes"):
        camera_ctx = (
            context
            + "\n\n# ACTION SCENES (JSON) — закон фаз для camera\n"
            + json.dumps(action_slice, ensure_ascii=False, indent=1)
        )
    await asyncio.gather(*(_one(n, camera_ctx) for n in ag.WAVE2_AGENTS))
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
        "\n\n".join(parts), timeout=timeout, project_id=project.id
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
    for i, batch in enumerate(batches, start=1):
        chunk_input = chunks.filter_assembly_input_for_frames(
            assembly_input,
            batch,
            frame_list,
            full_vo,
            include_characters=(i == 1),
        )
        chunk_ctx = chunks.build_chunk_context(
            full_vo, batch, chunk_index=i, chunk_total=total
        )
        # Feedback только на первый чанк — иначе раздувает все запросы.
        fb = feedback if i == 1 else None
        logger.info(
            "[#{}] scene_design assemble chunk {}/{} frames={}",
            project.id,
            i,
            total,
            [f.number for f in batch],
        )
        part = await run_assembler(
            project, chunk_ctx, chunk_input, feedback=fb, timeout=timeout
        )
        parts.append(part)

    merged = chunks.merge_assembler_payloads(parts)
    if not merged.get("scenes") or not merged.get("ops"):
        raise ag.SceneDesignAgentError(
            "scene_design/assemble: после склейки чанков пустой scenes/ops"
        )
    return merged
