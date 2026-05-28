"""Фабрика видео: родитель-шаблон + очередь дочерних проектов."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import NodeRun, Project, ProjectStatus, Workflow, WorkflowRun, WorkflowRunStatus
from app.storage import ProjectSheet

COPY_PROJECT_FIELDS = (
    "hero_mode",
    "image_generator",
    "aspect_ratio",
    "image_resolution",
    "image_relax",
    "video_generator",
    "video_resolution",
    "video_relax",
    "hero_count",
    "hero_descriptions",
    "hero_variations",
    "hero_variation_modifiers",
    "item_descriptions",
    "item_variations",
    "enrich_slots_count",
    "prompt_overrides",
    "gpt_text_overrides",
    "auto_mode",
)

COPY_META_KEYS = (
    "graph_executor",
    "ai_control",
    "auto_review_kinds",
    "ai_new_window_per_check",
    "prompt_slot_variants",
    "prompt_styles",
    "custom_prompts",
    "disabled_nodes",
    "excel_lane_bindings",
)

STRIP_META_KEYS = frozenset(
    {
        "mass_factory",
        "mass_parent_id",
        "mass_lane",
        "mass_lane_position",
        "mass_queue_active",
        "mass_queue_topics",
        "mass_queue_cursor",
        "mass_queue_pending_replace",
        "mass_lanes_count",
        "mass_excel_topics",
        "mass_excel_file",
        "mass_excel_revision",
        "mass_completed_child_ids",
        "mass_excel_topics_loaded",
        "excel_feed_node",
    }
)

MASS_CHILD_DONE_STATUSES = frozenset(
    {
        ProjectStatus.assembled,
        ProjectStatus.publishing,
        ProjectStatus.published,
    }
)


def is_mass_factory_parent(project: Project) -> bool:
    meta = project.meta if isinstance(project.meta, dict) else {}
    return bool(meta.get("mass_factory"))


def is_mass_factory_child(project: Project) -> bool:
    return mass_parent_id(project) is not None


def mass_parent_id(project: Project) -> int | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get("mass_parent_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def assert_not_factory_template_for_generation(project: Project) -> None:
    if is_mass_factory_parent(project) and not is_mass_factory_child(project):
        raise ValueError(
            "Шаблон фабрики: настройте промпты здесь, генерация — в дочерних проектах очереди"
        )


def build_child_meta(
    template_meta: dict[str, Any],
    *,
    parent_id: int,
    lane_position: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in template_meta.items():
        if key in STRIP_META_KEYS:
            continue
        if key in COPY_META_KEYS or key.startswith("prompt_"):
            out[key] = val
    out["graph_executor"] = template_meta.get("graph_executor", True)
    out["mass_parent_id"] = parent_id
    out["mass_lane"] = lane_position
    out["mass_lane_position"] = lane_position
    return out


async def _unique_slug(session: AsyncSession, base: str, slugify) -> str:
    slug = slugify(base)
    candidate = slug
    suffix = 2
    while (
        await session.execute(select(Project).where(Project.slug == candidate))
    ).scalar_one_or_none():
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


async def init_child_data_dir(project: Project) -> None:
    project.data_dir.mkdir(parents=True, exist_ok=True)
    for sub in (
        "characters",
        "items",
        "scenes",
        "videos",
        "audio",
        "subs",
        "final",
    ):
        (project.data_dir / sub).mkdir(parents=True, exist_ok=True)
    try:
        sheet = ProjectSheet(file_path=project.data_dir / "project.xlsx")
        sheet.ensure_initialized(project_id=project.id, slug=project.slug)
        sheet.write_general(
            topic=project.topic,
            slug=project.slug,
            hero_mode=project.hero_mode,
            status=project.status.value,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[#{}] mass_factory: project.xlsx init: {}", project.id, exc)


async def ensure_child_workflow_from_parent(
    session: AsyncSession,
    parent_id: int,
    child_id: int,
) -> None:
    """Копирует граф родителя (или default workflow) в ту же DB-сессию."""
    existing = (
        await session.execute(select(WorkflowRun).where(WorkflowRun.project_id == child_id))
    ).scalar_one_or_none()
    if existing is not None:
        return

    parent_run = (
        await session.execute(select(WorkflowRun).where(WorkflowRun.project_id == parent_id))
    ).scalar_one_or_none()
    if parent_run and parent_run.nodes_snapshot and parent_run.edges_snapshot:
        nodes_snapshot = list(parent_run.nodes_snapshot)
        edges_snapshot = list(parent_run.edges_snapshot)
        workflow_id = parent_run.workflow_id
    else:
        wf = (
            await session.execute(select(Workflow).where(Workflow.is_default == True))  # noqa: E712
        ).scalar_one_or_none()
        if wf is None:
            logger.warning(
                "mass_factory: no parent graph and no default workflow for child #{}",
                child_id,
            )
            return
        nodes_snapshot = list(wf.nodes or [])
        edges_snapshot = list(wf.edges or [])
        workflow_id = wf.id

    run = WorkflowRun(
        workflow_id=workflow_id,
        project_id=child_id,
        status=WorkflowRunStatus.new,
        nodes_snapshot=nodes_snapshot,
        edges_snapshot=edges_snapshot,
    )
    session.add(run)
    await session.flush()
    for node in run.nodes_snapshot:
        session.add(
            NodeRun(
                workflow_run_id=run.id,
                node_key=node["id"],
                node_type=node["type"],
            )
        )
    await session.flush()


async def create_mass_child(
    session: AsyncSession,
    template: Project,
    *,
    topic: str,
    lane_position: int,
    slugify,
) -> Project:
    meta_template = dict(template.meta or {})
    kwargs = {f: getattr(template, f) for f in COPY_PROJECT_FIELDS}
    slug = await _unique_slug(session, topic, slugify)
    child = Project(
        slug=slug,
        topic=topic.strip(),
        status=ProjectStatus.new,
        meta=build_child_meta(
            meta_template,
            parent_id=template.id,
            lane_position=lane_position,
        ),
        **kwargs,
    )
    session.add(child)
    await session.flush()
    await init_child_data_dir(child)
    await ensure_child_workflow_from_parent(session, template.id, child.id)
    return child


async def list_mass_children(session: AsyncSession, parent_id: int) -> list[Project]:
    parent_expr = cast(func.json_extract(Project.meta, "$.mass_parent_id"), Integer)
    rows = (
        await session.execute(select(Project).where(parent_expr == parent_id))
    ).scalars().all()
    out = list(rows)
    out.sort(key=lambda p: (p.meta or {}).get("mass_lane_position") or 999)
    return out


async def delete_new_mass_children(session: AsyncSession, parent_id: int) -> int:
    deleted = 0
    for child in await list_mass_children(session, parent_id):
        if child.status is not ProjectStatus.new:
            continue
        run = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.project_id == child.id)
            )
        ).scalar_one_or_none()
        if run is not None:
            await session.delete(run)
        await session.delete(child)
        deleted += 1
    if deleted:
        await session.flush()
    return deleted


def queue_state(parent: Project) -> dict[str, Any]:
    meta = dict(parent.meta or {})
    topics = [str(t).strip() for t in (meta.get("mass_queue_topics") or []) if str(t).strip()]
    cursor = int(meta.get("mass_queue_cursor") or 0)
    completed = list(meta.get("mass_completed_child_ids") or [])
    return {
        "active": bool(meta.get("mass_queue_active")),
        "topics": topics,
        "cursor": cursor,
        "revision": int(meta.get("mass_excel_revision") or 0),
        "filename": str(meta.get("mass_excel_file") or ""),
        "completed_child_ids": completed,
        "pending_replace": meta.get("mass_queue_pending_replace"),
    }


async def apply_topics_upload(
    session: AsyncSession,
    parent: Project,
    *,
    topics: list[str],
    filename: str,
) -> dict[str, Any]:
    """Правило B: новый Excel заменяет необработанную очередь."""
    meta = dict(parent.meta or {})
    meta["mass_factory"] = True
    meta["mass_excel_topics"] = topics
    meta["mass_excel_file"] = filename
    meta["mass_excel_revision"] = int(meta.get("mass_excel_revision") or 0) + 1

    busy = await serial_busy_child(session, parent.id)
    if busy is not None:
        meta["mass_queue_pending_replace"] = topics
        parent.meta = meta
        flag_modified(parent, "meta")
        await session.flush()
        return {
            "topics": topics,
            "count": len(topics),
            "revision": meta["mass_excel_revision"],
            "queued_after_current": True,
            "busy_child_id": busy,
        }

    await delete_new_mass_children(session, parent.id)
    meta["mass_queue_topics"] = topics
    meta["mass_queue_cursor"] = 0
    meta["mass_queue_active"] = False
    meta.pop("mass_queue_pending_replace", None)
    parent.meta = meta
    flag_modified(parent, "meta")
    await session.flush()
    return {
        "topics": topics,
        "count": len(topics),
        "revision": meta["mass_excel_revision"],
        "queued_after_current": False,
    }


async def serial_busy_child(session: AsyncSession, parent_id: int) -> int | None:
    from app.orchestrator.auto_advance import MASS_LANE_BUSY_STATUSES

    for child in await list_mass_children(session, parent_id):
        if child.status in (ProjectStatus.assembled, ProjectStatus.published):
            continue
        if child.status is ProjectStatus.new:
            continue
        if child.status in MASS_LANE_BUSY_STATUSES:
            return child.id
        if child.status.value.endswith("_ready") or child.status in (
            ProjectStatus.plan_ready,
            ProjectStatus.script_ready,
            ProjectStatus.paused,
            ProjectStatus.failed,
        ):
            return child.id
    return None


async def start_mass_queue(
    session: AsyncSession,
    parent: Project,
    *,
    topics: list[str] | None,
    slugify,
) -> dict[str, Any]:
    meta = dict(parent.meta or {})
    meta["mass_factory"] = True
    queue = [str(t).strip() for t in (topics or meta.get("mass_queue_topics") or []) if str(t).strip()]
    if not queue:
        excel_topics = meta.get("mass_excel_topics")
        if isinstance(excel_topics, list):
            queue = [str(t).strip() for t in excel_topics if str(t).strip()]
    if not queue:
        raise ValueError("нет тем в очереди — загрузите Excel со списком видео")

    if await serial_busy_child(session, parent.id) is not None:
        raise ValueError("очередь занята — дождитесь завершения текущего видео или остановите шаг")

    await delete_new_mass_children(session, parent.id)
    meta["mass_queue_topics"] = queue
    meta["mass_queue_cursor"] = 0
    meta["mass_queue_active"] = True
    meta["mass_lanes_count"] = len(queue)
    parent.meta = meta
    parent.topic = parent.topic or "Фабрика видео"
    await session.flush()

    from app.services.project_steps import start_step

    child = await create_mass_child(session, parent, topic=queue[0], lane_position=1, slugify=slugify)
    await start_step(session, child, "plan")
    meta["mass_queue_cursor"] = 1
    parent.meta = meta
    await session.flush()

    return {
        "started_id": child.id,
        "topic": child.topic,
        "queue_size": len(queue),
        "remaining": max(0, len(queue) - 1),
    }


async def _apply_pending_replace(session: AsyncSession, parent: Project) -> None:
    meta = dict(parent.meta or {})
    pending = meta.get("mass_queue_pending_replace")
    if not isinstance(pending, list) or not pending:
        return
    await delete_new_mass_children(session, parent.id)
    meta["mass_queue_topics"] = [str(t).strip() for t in pending if str(t).strip()]
    meta["mass_queue_cursor"] = 0
    meta.pop("mass_queue_pending_replace", None)
    parent.meta = meta
    await session.flush()


async def on_child_montage_complete(session: AsyncSession, child: Project) -> Project | None:
    """После монтажа дочернего — запустить следующую тему из очереди."""
    parent_id = mass_parent_id(child)
    if parent_id is None:
        return None
    if child.status not in MASS_CHILD_DONE_STATUSES:
        return None

    parent = await session.get(Project, parent_id)
    if parent is None:
        return None
    meta = dict(parent.meta or {})
    if not meta.get("mass_queue_active"):
        return None

    completed = list(meta.get("mass_completed_child_ids") or [])
    if child.id not in completed:
        completed.append(child.id)
    meta["mass_completed_child_ids"] = completed
    parent.meta = meta
    await session.flush()

    if await serial_busy_child(session, parent_id) is not None:
        return None

    await _apply_pending_replace(session, parent)
    meta = dict(parent.meta or {})
    topics = [str(t).strip() for t in (meta.get("mass_queue_topics") or []) if str(t).strip()]
    cursor = int(meta.get("mass_queue_cursor") or 0)
    if cursor >= len(topics):
        meta["mass_queue_active"] = False
        parent.meta = meta
        await session.flush()
        logger.info("mass_factory: parent #{} queue finished", parent_id)
        return None

    from app.web.routers.projects import _slugify
    from app.services.project_steps import start_step

    next_topic = topics[cursor]
    next_child = await create_mass_child(
        session,
        parent,
        topic=next_topic,
        lane_position=cursor + 1,
        slugify=_slugify,
    )
    await start_step(session, next_child, "plan")
    meta["mass_queue_cursor"] = cursor + 1
    parent.meta = meta
    await session.flush()
    logger.info(
        "mass_factory: parent #{} started child #{} ({}/{})",
        parent_id,
        next_child.id,
        cursor + 1,
        len(topics),
    )
    return next_child


async def mass_factory_status(session: AsyncSession, parent: Project) -> dict[str, Any]:
    children = await list_mass_children(session, parent.id)
    qs = queue_state(parent)
    busy_id = await serial_busy_child(session, parent.id)
    child_rows = []
    for c in children:
        child_rows.append(
            {
                "id": c.id,
                "topic": c.topic,
                "slug": c.slug,
                "status": c.status.value,
                "lane_position": (c.meta or {}).get("mass_lane_position"),
            }
        )
    return {
        **qs,
        "factory": is_mass_factory_parent(parent),
        "busy_child_id": busy_id,
        "children": child_rows,
    }
