"""Менеджер изолированных SQLite баз данных для каждого проекта (Project-Isolated DB).

Каждый проект получает свой собственный файл `data/videos/<slug>/project.db` (или
`data/batches/<batch>/sub/<slug>/project.db` для подпроектов).

Преимущества:
1. Ноль взаимных блокировок SQLite: генерации и правки в Проекте А никогда не блокируют Проект Б.
2. 100% переносимость: проект — это автономная папка на диске со своей базой и ассетами.
3. Пул движков (LRU Cache) с автонастройкой WAL, synchronous=NORMAL, foreign_keys=ON и busy_timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import selectinload
from sqlalchemy.pool import NullPool

from app.models import (
    Artifact,
    AsrWord,
    Attempt,
    Base,
    Entity,
    Frame,
    FrameEdge,
    FrameText,
    HITLRequest,
    NodeRun,
    Project,
    PromptVersion,
    Scene,
    SceneDesignCell,
    Workflow,
    WorkflowRun,
)

# Кэш активных движков: project_path -> AsyncEngine
_ENGINES: dict[str, AsyncEngine] = {}
_SESSION_MAKERS: dict[str, async_sessionmaker[AsyncSession]] = {}
_LOCK = asyncio.Lock()

# Кэши сопоставлений для сверхбыстрого роутинга запросов в нужную БД
_PROJECT_DIR_CACHE: dict[int, Path] = {}
_FRAME_TO_PROJECT: dict[int, int] = {}
_ENTITY_TO_PROJECT: dict[int, int] = {}
_SCENE_TO_PROJECT: dict[int, int] = {}
_EDGE_TO_PROJECT: dict[int, int] = {}
_PROMPT_TO_PROJECT: dict[int, int] = {}
_TEXT_TO_PROJECT: dict[int, int] = {}


def _clone_model_instance(model_cls: type, instance: Any) -> Any:
    """Клонирует экземпляр модели SQLAlchemy со всеми колонками таблицы."""
    data = {c.name: getattr(instance, c.name) for c in instance.__table__.columns}
    return model_cls(**data)


async def migrate_single_project_data(
    master_session: AsyncSession,
    project: Project,
    data_dir: Path | str | None = None,
    create_backup: bool = True,
) -> dict[str, int]:
    """Мигрирует все сущности одного проекта из master (state.db) в project.db.

    Исходные данные в state.db остаются нетронутыми в качестве резервной копии.
    """
    import shutil

    target_dir = Path(data_dir) if data_dir is not None else project.data_dir
    db_path = resolve_project_db_path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if db_path.exists() and create_backup:
        try:
            bak = db_path.with_suffix(".db.bak")
            shutil.copy2(db_path, bak)
            logger.info("Backup created: {}", bak)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Backup failed for {}: {}", db_path, exc)

    await init_project_db(target_dir)

    counts: dict[str, int] = {}
    pid = project.id

    async with project_session_scope(target_dir) as p_sess:
        # Проект
        existing_p = await p_sess.get(Project, pid)
        if not existing_p:
            p_sess.add(_clone_model_instance(Project, project))
            await p_sess.flush()
        else:
            for col in project.__table__.columns:
                setattr(existing_p, col.name, getattr(project, col.name))
            await p_sess.flush()

        # Шаблоны Workflow (для соблюдения внешних ключей)
        wfs = (await master_session.execute(select(Workflow))).scalars().all()
        for wf in wfs:
            if not await p_sess.get(Workflow, wf.id):
                p_sess.add(_clone_model_instance(Workflow, wf))
        await p_sess.flush()

        # Сущности со скоупом project_id
        async def _copy_scoped(model_cls: type, key_name: str) -> None:
            rows = (
                await master_session.execute(
                    select(model_cls).where(getattr(model_cls, "project_id") == pid)
                )
            ).scalars().all()
            for r in rows:
                if not await p_sess.get(model_cls, r.id):
                    p_sess.add(_clone_model_instance(model_cls, r))
            await p_sess.flush()
            counts[key_name] = len(rows)

        await _copy_scoped(Scene, "scenes")
        await _copy_scoped(Frame, "frames")
        await _copy_scoped(FrameText, "frame_texts")
        await _copy_scoped(PromptVersion, "prompt_versions")
        await _copy_scoped(FrameEdge, "frame_edges")
        await _copy_scoped(Artifact, "artifacts")
        await _copy_scoped(Entity, "entities")
        await _copy_scoped(HITLRequest, "hitl_requests")
        await _copy_scoped(SceneDesignCell, "scene_design_cells")
        await _copy_scoped(AsrWord, "asr_words")
        await _copy_scoped(Attempt, "attempts")

        # Workflow runs
        wfrs = (
            await master_session.execute(
                select(WorkflowRun).where(WorkflowRun.project_id == pid)
            )
        ).scalars().all()
        wf_ids = []
        for wf in wfrs:
            wf_ids.append(wf.id)
            if not await p_sess.get(WorkflowRun, wf.id):
                p_sess.add(_clone_model_instance(WorkflowRun, wf))
        await p_sess.flush()
        counts["workflow_runs"] = len(wfrs)

        # Node runs
        if wf_ids:
            nrs = (
                await master_session.execute(
                    select(NodeRun).where(NodeRun.workflow_run_id.in_(wf_ids))
                )
            ).scalars().all()
            for nr in nrs:
                if not await p_sess.get(NodeRun, nr.id):
                    p_sess.add(_clone_model_instance(NodeRun, nr))
            await p_sess.flush()
            counts["node_runs"] = len(nrs)

    logger.info(
        "Project #{} [{}] migrated: {} frames, {} scenes, {} artifacts",
        pid,
        project.slug,
        counts.get("frames", 0),
        counts.get("scenes", 0),
        counts.get("artifacts", 0),
    )
    return counts


async def migrate_project_by_id(project_id: int, create_backup: bool = True) -> bool:
    """Мигрирует конкретный проект по его ID из master DB в project.db."""
    from app.db import SessionLocal

    async with SessionLocal() as master_session:
        p = await master_session.get(Project, project_id)
        if p is None:
            return False
        await migrate_single_project_data(master_session, p, create_backup=create_backup)
        return True


async def auto_migrate_legacy_projects_if_needed() -> int:
    """Проверяет все проекты в master state.db: если у проекта отсутствует project.db,
    автоматически создаёт его и переносит все данные (кадры, сцены, промты, артефакты).
    Безопасно: исходные данные в state.db НЕ удаляются.
    """
    from app.db import SessionLocal

    migrated_count = 0
    try:
        async with SessionLocal() as master_session:
            projects = (await master_session.execute(select(Project))).scalars().all()
            for p in projects:
                if not p.data_dir:
                    continue
                db_path = resolve_project_db_path(p.data_dir)
                if not db_path.exists():
                    logger.info("Auto-migrating legacy project #{} [{}] to project.db...", p.id, p.slug)
                    try:
                        await migrate_single_project_data(master_session, p, create_backup=False)
                        migrated_count += 1
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Auto-migration of project #{} failed (non-fatal): {}", p.id, e)
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto_migrate_legacy_projects_if_needed error: {}", exc)

    return migrated_count


def _configure_project_sqlite(dbapi_connection: Any, _connection_record: Any) -> None:
    """Оптимальные прагмы для персональной проектной SQLite БД."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=60000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def resolve_project_db_path(project_data_dir: Path | str) -> Path:
    """Возвращает путь к project.db внутри папки проекта."""
    folder = Path(project_data_dir)
    return folder / "project.db"


def register_project_data_dir(project_id: int, data_dir: Path | str) -> None:
    """Зарегистрировать директорию проекта в кэше."""
    _PROJECT_DIR_CACHE[project_id] = Path(data_dir)


def unregister_project_data_dir(project_id: int) -> None:
    """Удалить директорию проекта из кэша."""
    _PROJECT_DIR_CACHE.pop(project_id, None)


async def get_project_data_dir(
    project_id: int, master_session: AsyncSession | None = None
) -> Path | None:
    """Получить путь к директории проекта по project_id."""
    if project_id in _PROJECT_DIR_CACHE:
        return _PROJECT_DIR_CACHE[project_id]

    if master_session is not None:
        p = await master_session.get(Project, project_id)
        if p and p.data_dir:
            path = Path(p.data_dir)
            _PROJECT_DIR_CACHE[project_id] = path
            return path
        return None

    from app.db import SessionLocal

    async with SessionLocal() as s:
        p = await s.get(Project, project_id)
        if p and p.data_dir:
            path = Path(p.data_dir)
            _PROJECT_DIR_CACHE[project_id] = path
            return path
    return None


def register_frame_project(frame_id: int, project_id: int) -> None:
    _FRAME_TO_PROJECT[frame_id] = project_id


def register_entity_project(entity_id: int, project_id: int) -> None:
    _ENTITY_TO_PROJECT[entity_id] = project_id


def register_scene_project(scene_id: int, project_id: int) -> None:
    _SCENE_TO_PROJECT[scene_id] = project_id


def register_edge_project(edge_id: int, project_id: int) -> None:
    _EDGE_TO_PROJECT[edge_id] = project_id


def register_prompt_project(prompt_id: int, project_id: int) -> None:
    _PROMPT_TO_PROJECT[prompt_id] = project_id


def register_text_project(text_id: int, project_id: int) -> None:
    _TEXT_TO_PROJECT[text_id] = project_id


async def resolve_frame_project_id(frame_id: int) -> int | None:
    """Определить project_id по frame_id с поиском в кэше, state.db и проектных базах."""
    if frame_id in _FRAME_TO_PROJECT:
        return _FRAME_TO_PROJECT[frame_id]

    from app.db import SessionLocal

    async with SessionLocal() as s:
        fr = await s.get(Frame, frame_id)
        if fr is not None:
            _FRAME_TO_PROJECT[frame_id] = fr.project_id
            return fr.project_id

    # Проверяем в активных проектных базах
    for key, sm in list(_SESSION_MAKERS.items()):
        try:
            async with sm() as p_sess:
                fr = await p_sess.get(Frame, frame_id)
                if fr is not None:
                    _FRAME_TO_PROJECT[frame_id] = fr.project_id
                    return fr.project_id
        except Exception:
            continue
    return None


async def resolve_entity_project_id(entity_id: int) -> int | None:
    """Определить project_id по entity_id."""
    if entity_id in _ENTITY_TO_PROJECT:
        return _ENTITY_TO_PROJECT[entity_id]

    from app.db import SessionLocal

    async with SessionLocal() as s:
        en = await s.get(Entity, entity_id)
        if en is not None:
            _ENTITY_TO_PROJECT[entity_id] = en.project_id
            return en.project_id

    for key, sm in list(_SESSION_MAKERS.items()):
        try:
            async with sm() as p_sess:
                en = await p_sess.get(Entity, entity_id)
                if en is not None:
                    _ENTITY_TO_PROJECT[entity_id] = en.project_id
                    return en.project_id
        except Exception:
            continue
    return None


async def resolve_scene_project_id(scene_id: int) -> int | None:
    """Определить project_id по scene_id."""
    if scene_id in _SCENE_TO_PROJECT:
        return _SCENE_TO_PROJECT[scene_id]

    from app.db import SessionLocal

    async with SessionLocal() as s:
        sc = await s.get(Scene, scene_id)
        if sc is not None:
            _SCENE_TO_PROJECT[scene_id] = sc.project_id
            return sc.project_id

    for key, sm in list(_SESSION_MAKERS.items()):
        try:
            async with sm() as p_sess:
                sc = await p_sess.get(Scene, scene_id)
                if sc is not None:
                    _SCENE_TO_PROJECT[scene_id] = sc.project_id
                    return sc.project_id
        except Exception:
            continue
    return None


async def resolve_edge_project_id(edge_id: int) -> int | None:
    """Определить project_id по edge_id."""
    if edge_id in _EDGE_TO_PROJECT:
        return _EDGE_TO_PROJECT[edge_id]

    from app.db import SessionLocal

    async with SessionLocal() as s:
        ed = await s.get(FrameEdge, edge_id)
        if ed is not None:
            _EDGE_TO_PROJECT[edge_id] = ed.project_id
            return ed.project_id

    for key, sm in list(_SESSION_MAKERS.items()):
        try:
            async with sm() as p_sess:
                ed = await p_sess.get(FrameEdge, edge_id)
                if ed is not None:
                    _EDGE_TO_PROJECT[edge_id] = ed.project_id
                    return ed.project_id
        except Exception:
            continue
    return None


async def resolve_prompt_project_id(prompt_id: int) -> int | None:
    """Определить project_id по prompt_id."""
    if prompt_id in _PROMPT_TO_PROJECT:
        return _PROMPT_TO_PROJECT[prompt_id]

    from app.db import SessionLocal

    async with SessionLocal() as s:
        pv = await s.get(PromptVersion, prompt_id)
        if pv is not None:
            _PROMPT_TO_PROJECT[prompt_id] = pv.project_id
            return pv.project_id

    for key, sm in list(_SESSION_MAKERS.items()):
        try:
            async with sm() as p_sess:
                pv = await p_sess.get(PromptVersion, prompt_id)
                if pv is not None:
                    _PROMPT_TO_PROJECT[prompt_id] = pv.project_id
                    return pv.project_id
        except Exception:
            continue
    return None


async def resolve_text_project_id(text_id: int) -> int | None:
    """Определить project_id по text_id."""
    if text_id in _TEXT_TO_PROJECT:
        return _TEXT_TO_PROJECT[text_id]

    from app.db import SessionLocal

    async with SessionLocal() as s:
        ft = await s.get(FrameText, text_id)
        if ft is not None:
            _TEXT_TO_PROJECT[text_id] = ft.project_id
            return ft.project_id

    for key, sm in list(_SESSION_MAKERS.items()):
        try:
            async with sm() as p_sess:
                ft = await p_sess.get(FrameText, text_id)
                if ft is not None:
                    _TEXT_TO_PROJECT[text_id] = ft.project_id
                    return ft.project_id
        except Exception:
            continue
    return None


async def get_project_engine(project_data_dir: Path | str) -> AsyncEngine:
    """Получить или создать асинхронный движок к project.db проекта."""
    db_file = resolve_project_db_path(project_data_dir)
    db_key = str(db_file.resolve())

    if db_key in _ENGINES:
        return _ENGINES[db_key]

    async with _LOCK:
        if db_key in _ENGINES:
            return _ENGINES[db_key]

        # Создаём родительскую директорию, если ещё не существует
        db_file.parent.mkdir(parents=True, exist_ok=True)

        url = f"sqlite+aiosqlite:///{db_file.as_posix()}"
        eng = create_async_engine(
            url,
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={"timeout": 60},
        )
        event.listens_for(eng.sync_engine, "connect")(_configure_project_sqlite)

        _ENGINES[db_key] = eng
        _SESSION_MAKERS[db_key] = async_sessionmaker(
            eng, expire_on_commit=False, class_=AsyncSession
        )
        return eng


async def get_project_sessionmaker(
    project_data_dir: Path | str,
) -> async_sessionmaker[AsyncSession]:
    """Получить фабрику сессий для конкретного проекта."""
    db_file = resolve_project_db_path(project_data_dir)
    db_key = str(db_file.resolve())
    if db_key not in _SESSION_MAKERS:
        await get_project_engine(project_data_dir)
    return _SESSION_MAKERS[db_key]


async def sync_project_row_to_project_db(
    project: Project, data_dir: Path | str | None = None
) -> None:
    """Синхронизировать запись Project в project.db."""
    dir_path = Path(data_dir or project.data_dir)
    sm = await get_project_sessionmaker(dir_path)
    async with sm() as p_sess:
        existing = await p_sess.get(Project, project.id)
        if existing is None:
            p_sess.add(
                Project(
                    id=project.id,
                    slug=project.slug,
                    title=project.title or project.slug or "Project",
                    topic=project.topic or project.title or project.slug or "default",
                    hero_mode=project.hero_mode,
                    status=project.status,
                    general_plan=project.general_plan,
                    hero_description=project.hero_description,
                    script_text=project.script_text,
                    image_generator=project.image_generator,
                    aspect_ratio=project.aspect_ratio,
                    image_resolution=project.image_resolution,
                    image_quality=project.image_quality,
                    image_relax=project.image_relax,
                    video_generator=project.video_generator,
                    video_resolution=project.video_resolution,
                    video_relax=project.video_relax,
                    hero_count=project.hero_count,
                    hero_descriptions=project.hero_descriptions,
                    hero_variations=project.hero_variations,
                    hero_variation_modifiers=project.hero_variation_modifiers,
                    item_descriptions=project.item_descriptions,
                    item_variations=project.item_variations,
                    enrich_slots_count=project.enrich_slots_count,
                    auto_mode=project.auto_mode,
                    batch_id=project.batch_id,
                    batch_position=project.batch_position,
                    batch_slug=project.batch_slug,
                    prompt_overrides=project.prompt_overrides,
                    gpt_text_overrides=project.gpt_text_overrides,
                    meta=project.meta,
                    created_at=project.created_at,
                    updated_at=project.updated_at,
                )
            )
        else:
            existing.slug = project.slug
            existing.title = project.title
            existing.topic = project.topic
            existing.hero_mode = project.hero_mode
            existing.status = project.status
            existing.general_plan = project.general_plan
            existing.hero_description = project.hero_description
            existing.script_text = project.script_text
            existing.image_generator = project.image_generator
            existing.aspect_ratio = project.aspect_ratio
            existing.image_resolution = project.image_resolution
            existing.image_quality = project.image_quality
            existing.image_relax = project.image_relax
            existing.video_generator = project.video_generator
            existing.video_resolution = project.video_resolution
            existing.video_relax = project.video_relax
            existing.hero_count = project.hero_count
            existing.hero_descriptions = project.hero_descriptions
            existing.hero_variations = project.hero_variations
            existing.hero_variation_modifiers = project.hero_variation_modifiers
            existing.item_descriptions = project.item_descriptions
            existing.item_variations = project.item_variations
            existing.enrich_slots_count = project.enrich_slots_count
            existing.auto_mode = project.auto_mode
            existing.batch_id = project.batch_id
            existing.batch_position = project.batch_position
            existing.batch_slug = project.batch_slug
            existing.prompt_overrides = project.prompt_overrides
            existing.gpt_text_overrides = project.gpt_text_overrides
            existing.meta = project.meta
            existing.updated_at = project.updated_at
        from app.db import commit_with_retry

        await commit_with_retry(p_sess)


_RUNTIME_META_KEYS = (
    "active_excel_gpt_node_key",
    "excel_gpt_ui_force_full",
    "excel_gpt_force_full_rerun",
    "enrich_auto_chain_to",
    "enrich_completed_slots",
    "excel_gpt_completed_keys",
    "excel_gpt_nodes",
    "canvas_graph",
    "user_stop",
    "mass_lane_user_stop",
    "auto_await_manual_start",
)
_CLEAR_WHEN_MASTER_RUNNING = (
    "user_stop",
    "mass_lane_user_stop",
    "auto_await_manual_start",
)


def _bind_url(session: AsyncSession) -> str:
    bind = session.get_bind()
    return str(getattr(bind, "url", "") or "").replace("\\", "/")


def _apply_runtime_fields(src: Project, dest: Project) -> None:
    """Скопировать статус / auto_mode / runtime-meta (не весь контент проекта)."""
    from app.services.project_state import is_running_status

    dest.status = src.status
    if src.auto_mode is not None:
        dest.auto_mode = src.auto_mode
    dest.updated_at = src.updated_at or datetime.utcnow()
    d_meta = dict(dest.meta) if isinstance(dest.meta, dict) else {}
    s_meta = dict(src.meta) if isinstance(src.meta, dict) else {}
    for key in _RUNTIME_META_KEYS:
        if key in s_meta:
            d_meta[key] = s_meta[key]
        elif (
            is_running_status(src.status)
            and key in _CLEAR_WHEN_MASTER_RUNNING
        ):
            d_meta.pop(key, None)
    dest.meta = d_meta


async def push_runtime_to_project_db(
    session: AsyncSession,
    project: Project,
) -> None:
    """После ▶/⏹ на master: статус + runtime meta + NodeRuns → project.db.

    Иначе воркер видит running в state.db, а advance открывает project.db
    со старым *_ready и сразу выходит — нода «не продвигается».
    Не копируем всю строку Project: в project.db контент может быть новее.
    """
    from app.db import commit_with_retry

    if "project.db" in _bind_url(session):
        return
    try:
        dir_path = Path(project.data_dir)
        if not dir_path.exists():
            found = await get_project_data_dir(int(project.id))
            if found is None:
                logger.warning(
                    "push_runtime_to_project_db: #{} no data_dir",
                    project.id,
                )
                return
            dir_path = found
        sm = await get_project_sessionmaker(dir_path)
        async with sm() as probe:
            missing = await probe.get(Project, project.id) is None
        if missing:
            await sync_project_row_to_project_db(project, data_dir=dir_path)
        async with sm() as p_sess:
            existing = await p_sess.get(Project, project.id)
            if existing is None:
                logger.warning(
                    "push_runtime_to_project_db: #{} row missing in project.db",
                    project.id,
                )
                return
            _apply_runtime_fields(project, existing)
            try:
                await _copy_noderuns_between_sessions(
                    session, p_sess, int(project.id)
                )
            except Exception as nexc:  # noqa: BLE001
                logger.warning(
                    "push_runtime_to_project_db: #{} NodeRuns skipped: {}",
                    project.id,
                    nexc,
                )
            await commit_with_retry(p_sess)
        logger.info(
            "push_runtime_to_project_db: #{} status={} → project.db",
            project.id,
            getattr(project.status, "value", project.status),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "push_runtime_to_project_db: #{} failed: {}",
            project.id,
            exc,
        )


async def pull_master_runtime_into_project(
    session: AsyncSession,
    project: Project,
) -> bool:
    """Перед advance: взять running-статус с master, если project.db отстал."""
    from app.db import session_scope
    from app.services.project_state import is_running_status

    changed = False
    async with session_scope() as master:
        m = await master.get(Project, int(project.id))
        if m is None:
            return False
        if m.status != project.status:
            take_master_status = is_running_status(m.status) and not is_running_status(
                project.status
            )
            if take_master_status:
                logger.warning(
                    "pull_master_runtime: #{} project.db={} master={} — берём master",
                    project.id,
                    getattr(project.status, "value", project.status),
                    getattr(m.status, "value", m.status),
                )
                project.status = m.status
                changed = True
            else:
                logger.warning(
                    "pull_master_runtime: #{} skip stale master {} (project.db={})",
                    project.id,
                    getattr(m.status, "value", m.status),
                    getattr(project.status, "value", project.status),
                )
        if m.auto_mode is not None and m.auto_mode != project.auto_mode:
            project.auto_mode = m.auto_mode
            changed = True
        m_meta = m.meta if isinstance(m.meta, dict) else {}
        p_meta = dict(project.meta) if isinstance(project.meta, dict) else {}
        for key in _RUNTIME_META_KEYS:
            if key in m_meta:
                if p_meta.get(key) != m_meta.get(key):
                    p_meta[key] = m_meta[key]
                    changed = True
            elif key in _CLEAR_WHEN_MASTER_RUNNING and key in p_meta and is_running_status(m.status):
                p_meta.pop(key, None)
                changed = True
        if changed:
            project.meta = p_meta
        n = await _copy_noderuns_between_sessions(
            master, session, int(project.id)
        )
        if n:
            changed = True
    if changed:
        await session.flush()
    return changed


async def push_runtime_to_master(
    session: AsyncSession,
    project: Project,
) -> None:
    """После работы в project.db: статус + runtime meta + NodeRuns → state.db.

    UI и воркер выбирают проекты по master; без этого копии нода остаётся
    running на канвасе после успешного шага / рестарта.
    """
    from app.db import commit_with_retry, session_scope

    if "project.db" not in _bind_url(session):
        return
    try:
        async with session_scope() as master:
            existing = await master.get(Project, int(project.id))
            if existing is None:
                return
            _apply_runtime_fields(project, existing)
            try:
                await _copy_noderuns_between_sessions(
                    session, master, int(project.id)
                )
            except Exception as nexc:  # noqa: BLE001
                logger.warning(
                    "push_runtime_to_master: #{} NodeRuns skipped: {}",
                    project.id,
                    nexc,
                )
            await commit_with_retry(master)
        logger.debug(
            "push_runtime_to_master: #{} status={} → state.db",
            project.id,
            getattr(project.status, "value", project.status),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "push_runtime_to_master: #{} failed: {}",
            project.id,
            exc,
        )


async def sync_runtime_both_ways(
    session: AsyncSession,
    project: Project,
) -> None:
    """После ▶/⏹: дописать runtime в ту БД, которой текущая сессия не является."""
    await push_runtime_to_project_db(session, project)
    await push_runtime_to_master(session, project)


async def _copy_noderuns_between_sessions(
    src_session: AsyncSession,
    dest_session: AsyncSession,
    project_id: int,
) -> int:
    src_run = (
        await src_session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project_id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalar_one_or_none()
    dest_run = (
        await dest_session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project_id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalar_one_or_none()
    if src_run is None or dest_run is None:
        return 0
    dest_by_key = {str(nr.node_key or ""): nr for nr in dest_run.node_runs}
    updated = 0
    for src in src_run.node_runs:
        dest = dest_by_key.get(str(src.node_key or ""))
        if dest is None:
            continue
        if (
            dest.status == src.status
            and dest.progress == src.progress
            and (dest.error or None) == (src.error or None)
        ):
            continue
        dest.status = src.status
        dest.progress = src.progress
        dest.progress_text = src.progress_text
        dest.error = src.error
        dest.started_at = src.started_at
        dest.finished_at = src.finished_at
        dest.attempts = src.attempts
        dest.updated_at = src.updated_at
        updated += 1
    return updated


async def _copy_noderuns_from_master_into_session(
    src_session: AsyncSession,
    dest_session: AsyncSession,
    project_id: int,
) -> int:
    return await _copy_noderuns_between_sessions(
        src_session, dest_session, project_id
    )


async def init_project_db(
    project_data_dir: Path | str, project: Project | None = None
) -> None:
    """Инициализировать схему таблиц в project.db проекта (create_all) и синхронизировать проект."""
    eng = await get_project_engine(project_data_dir)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from app.services.db_v2 import migrate_db_v2_schema

        await migrate_db_v2_schema(conn)

    if project is not None:
        try:
            await sync_project_row_to_project_db(project, data_dir=project_data_dir)
        except Exception as exc:  # noqa: BLE001
            logger.warning("init_project_db: sync project row failed: {}", exc)


@asynccontextmanager
async def project_session_scope(
    project_data_dir: Path | str,
) -> AsyncIterator[AsyncSession]:
    """Контекстный менеджер транзакции для персональной проектной БД с автокоммитом."""
    sm = await get_project_sessionmaker(project_data_dir)
    async with sm() as session:
        try:
            yield session
            from app.db import commit_with_retry

            await commit_with_retry(session)
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def project_db_session_scope(
    project_id_or_dir: int | Path | str,
) -> AsyncIterator[AsyncSession]:
    """Универсальный контекстный менеджер: принимает project_id или путь к директории.
    При первом обращении к проекту автоматически мигрирует данные из state.db при необходимости.
    """
    if isinstance(project_id_or_dir, int):
        data_dir = await get_project_data_dir(project_id_or_dir)
        if data_dir is None:
            from app.db import session_scope

            async with session_scope() as s:
                yield s
            return
        target_dir = data_dir
        db_path = resolve_project_db_path(target_dir)
        if not db_path.exists():
            await migrate_project_by_id(project_id_or_dir, create_backup=False)
    else:
        target_dir = Path(project_id_or_dir)
        db_path = resolve_project_db_path(target_dir)
        if not db_path.exists():
            await init_project_db(target_dir)

    async with project_session_scope(target_dir) as s:
        yield s


async def get_project_db_session(project_id: int) -> AsyncIterator[AsyncSession]:
    """Dependency generator: yields AsyncSession к project.db проекта.
    При первом запросе автоматически мигрирует данные проекта в его персональную базу.
    """
    data_dir = await get_project_data_dir(project_id)
    if data_dir is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=404, detail=f"проект {project_id} не найден"
        )

    db_path = resolve_project_db_path(data_dir)
    if not db_path.exists():
        await migrate_project_by_id(project_id, create_backup=False)

    sm = await get_project_sessionmaker(data_dir)
    session = sm()
    try:
        yield session
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


async def close_all_project_engines() -> None:
    """Корректно закрыть все открытые пулы проектов при завершении приложения."""
    async with _LOCK:
        for key, eng in list(_ENGINES.items()):
            try:
                await eng.dispose()
            except Exception as e:  # noqa: BLE001
                logger.warning("project_db: dispose failed for {}: {}", key, e)
        _ENGINES.clear()
        _SESSION_MAKERS.clear()
        _PROJECT_DIR_CACHE.clear()
