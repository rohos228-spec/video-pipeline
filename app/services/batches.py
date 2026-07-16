"""Сервис «Массовое создание» — батч-проекты.

BatchProject — контейнер для группы подпроектов (роликов), сделанных
по одному шаблону. Полностью изолирован:
  - своя папка `data/batches/<slug>/`
  - снапшот промптов из `prompts/` копируется в `data/batches/<slug>/prompts/`
    в момент создания батча; основная папка `prompts/` потом может быть
    отредактирована — на батч это не повлияет
  - общий `topics.xlsx` со списком тем
  - снапшот настроек эталонного проекта в `BatchProject.settings_snapshot`,
    применяется ко всем подпроектам при создании
  - каждый подпроект — обычная запись `projects` со ссылкой `batch_id`,
    `batch_position` (порядок) и `batch_slug` (денормализация для путей)

Сюда вынесена ВСЯ логика батча: создание, добавление тем, удаление,
подсчёт прогресса, slug-генерация, копирование промптов.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BatchProject, BatchStatus, Project, ProjectStatus
from app.services.canvas_graph import ensure_subproject_workflow_run, sanitize_canvas_graph_for_inherit
from app.services.prompt_library import PROMPTS_ROOT
from app.services import sidebar_layout as layout_svc
from app.settings import settings

# Поля Project, которые попадают в snapshot и применяются ко всем подпроектам
# при их создании. ВНИМАНИЕ: тут перечислены только «настроечные» поля,
# которые юзер задавал в мастере / меню. Поля с данными (general_plan,
# script_text, status, slug, topic, …) НЕ копируются — у каждого подпроекта
# они свои. meta — отдельный whitelist-мердж (см. INHERITED_META_KEYS).
TEMPLATE_FIELDS: tuple[str, ...] = (
    "hero_mode",
    "image_generator",
    "aspect_ratio",
    "image_resolution",
    "image_quality",
    "image_relax",
    "video_generator",
    "video_resolution",
    "video_relax",
    "hero_count",
    "hero_descriptions",
    "hero_variations",
    "hero_variation_modifiers",
    "enrich_slots_count",
    "item_descriptions",
    "item_variations",
    "prompt_overrides",
    "gpt_text_overrides",
)

# meta подпроекта: унаследовать от шаблона ТОЛЬКО эти ключи.
INHERITED_META_KEYS: frozenset[str] = frozenset(
    {
        "canvas_graph",
        "prompt_slot_variants",
        "custom_prompts",
        "excel_hero_enabled",
    }
)

_FORBIDDEN_META_EXACT: frozenset[str] = frozenset(
    {
        "montage_board",
        "prompt_applied_at",
    }
)

_FORBIDDEN_META_PREFIXES: tuple[str, ...] = ("auto_retry_", "auto_paused_", "mass_")


def _is_forbidden_meta_key(key: str) -> bool:
    if key in _FORBIDDEN_META_EXACT:
        return True
    return any(key.startswith(p) for p in _FORBIDDEN_META_PREFIXES)


def _extract_inheritable_meta(source: dict | None) -> dict:
    """Whitelist-копия meta шаблона для snapshot / подпроекта."""
    if not isinstance(source, dict):
        return {}
    import copy as _copy

    out: dict = {}
    for key in INHERITED_META_KEYS:
        if key not in source or _is_forbidden_meta_key(key):
            continue
        val = _copy.deepcopy(source[key])
        if key == "canvas_graph" and isinstance(val, dict):
            val = sanitize_canvas_graph_for_inherit(val)
        out[key] = val
    return out


def _merge_subproject_meta(
    *,
    topic_card: dict,
    permanent_product: dict | None,
    template_meta: dict | None,
) -> dict:
    """Собирает meta подпроекта: свои topic_card/product + whitelist от шаблона."""
    import copy as _copy

    meta: dict = {"topic_card": dict(topic_card)}
    if permanent_product and permanent_product.get("name"):
        meta["permanent_product"] = _copy.deepcopy(permanent_product)
    inherited = _extract_inheritable_meta(template_meta)
    for key, val in inherited.items():
        meta[key] = val
    return meta


def _strip_forbidden_meta_keys(meta: dict) -> dict:
    """Удаляет унаследованный runtime-мусор, не трогая topic_card / permanent_product."""
    cleaned = dict(meta)
    for key in list(cleaned.keys()):
        if key in ("topic_card", "permanent_product"):
            continue
        if _is_forbidden_meta_key(key):
            del cleaned[key]
    return cleaned

# Кириллица → ASCII транслитерация для slug. Дублирует логику из seed_pilot,
# чтобы не плодить зависимостей.
_CYR_MAP = str.maketrans(
    {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
)


def slugify(text: str, *, fallback: str = "batch", max_len: int = 60) -> str:
    """Превращает произвольный текст в filesystem-safe slug (ASCII)."""
    t = (text or "").lower().translate(_CYR_MAP)
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    t = re.sub(r"-+", "-", t)
    return t[:max_len] or fallback


def make_sub_slug(batch_slug: str, position: int, topic: str) -> str:
    """Slug подпроекта: `<batch_slug>__<NN>_<topic_slug>`.

    Двойное подчёркивание — разделитель префикса батча. Гарантированно
    уникально в рамках одного batch (через position), легко читается
    глазами в `data/videos/` или `data/batches/.../sub/`.
    """
    topic_part = slugify(topic, fallback=f"sub{position:03d}", max_len=40)
    return f"{batch_slug}__{position:03d}_{topic_part}"


async def _unique_batch_slug(session: AsyncSession, base: str) -> str:
    """Подбирает уникальный slug, добавляя -2, -3, …, если базовый занят."""
    slug = base
    n = 1
    while True:
        exists = (
            await session.execute(
                select(BatchProject).where(BatchProject.slug == slug)
            )
        ).scalar_one_or_none()
        if exists is None:
            return slug
        n += 1
        slug = f"{base}-{n}"


async def _unique_project_slug(session: AsyncSession, base: str) -> str:
    """Подбирает уникальный slug для подпроекта."""
    slug = base
    n = 1
    while True:
        exists = (
            await session.execute(select(Project).where(Project.slug == slug))
        ).scalar_one_or_none()
        if exists is None:
            return slug
        n += 1
        slug = f"{base}-{n}"


def _snapshot_settings_from(project: Project) -> dict:
    """Снимаем настройки эталонного проекта в dict для settings_snapshot."""
    import copy as _copy

    snap: dict = {}
    for f in TEMPLATE_FIELDS:
        val = getattr(project, f, None)
        if isinstance(val, (dict, list)):
            val = _copy.deepcopy(val)
        snap[f] = val
    snap["meta"] = _extract_inheritable_meta(
        project.meta if isinstance(project.meta, dict) else {}
    )
    return snap


def _copy_prompts_snapshot(target_dir: Path) -> None:
    """Копирует всё содержимое `prompts/` в `target_dir`.

    Структура сохраняется: `01_plan/default.md`, `02_script/default.md` и т.д.
    Если папка-цель уже существует — НЕ перезаписываем (снапшот неизменяем).
    """
    if target_dir.exists():
        logger.info("batches: prompts snapshot already exists at {}", target_dir)
        return
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(PROMPTS_ROOT, target_dir, dirs_exist_ok=False)
    logger.info("batches: copied prompts snapshot → {}", target_dir)


async def create_batch(
    session: AsyncSession,
    *,
    name: str,
    template_project_id: int | None = None,
) -> BatchProject:
    """Создаёт массовый проект и инициализирует папки на диске.

    - sanitize name → unique slug
    - копирует промпты из `prompts/` в `data/batches/<slug>/prompts/`
    - если задан template_project_id — снимает snapshot его настроек
    - создаёт пустую папку `sub/` для будущих подпроектов
    - вставляет запись в БД (status=new, тем нет — добавляются позже)
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Имя массового проекта не может быть пустым")

    base_slug = slugify(name)
    slug = await _unique_batch_slug(session, base_slug)

    settings_snapshot: dict = {}
    if template_project_id is not None:
        template = (
            await session.execute(
                select(Project).where(Project.id == template_project_id)
            )
        ).scalar_one_or_none()
        if template is not None:
            settings_snapshot = _snapshot_settings_from(template)

    batch = BatchProject(
        name=name,
        slug=slug,
        status=BatchStatus.new,
        template_project_id=template_project_id,
        settings_snapshot=settings_snapshot,
    )
    session.add(batch)
    await session.flush()

    # Папочная структура на диске
    base_dir = Path(settings.data_dir) / "batches" / slug
    (base_dir / "sub").mkdir(parents=True, exist_ok=True)
    _copy_prompts_snapshot(base_dir / "prompts")

    logger.info(
        "batches: created #{} '{}' (slug={}, template_pid={})",
        batch.id, name, slug, template_project_id,
    )
    layout_svc.ensure_batch_folder(batch.id, batch.name)
    return batch


async def add_topics(
    session: AsyncSession,
    batch: BatchProject,
    topics: list,
) -> list[Project]:
    """Создаёт подпроекты по списку тем.

    PR #3: каждая «тема» — это либо просто строка (заголовок), либо
    dict с расширенными карточными полями (title, source, style,
    hook_type, emotion, fact, logic, integration, shoot_note,
    hero_mode). Карточные поля попадают в `Project.meta["topic_card"]`
    и далее в промпты плана/сценария.

    Каждый подпроект:
      - получает batch_id / batch_position / batch_slug (денормализация)
      - наследует поля из settings_snapshot
      - получает carded-описание в meta["topic_card"]
      - создаётся со status=new
      - получает свою папку `data/batches/<batch_slug>/sub/<sub_slug>/`
    """
    if not topics:
        return []

    # Нормализуем входные данные: str → dict с одним полем title.
    norm_topics: list[dict] = []
    for t in topics:
        if isinstance(t, dict):
            title = (t.get("title") or t.get("topic") or "").strip()
            if not title:
                continue
            card = dict(t)
            card["title"] = title
            card["topic"] = title  # обратная совместимость
            norm_topics.append(card)
        elif isinstance(t, str) and t.strip():
            title = t.strip()
            norm_topics.append({"title": title, "topic": title})
    if not norm_topics:
        return []

    # Считаем сколько уже подпроектов у этого батча — продолжаем нумерацию.
    existing = (
        await session.execute(
            select(Project).where(Project.batch_id == batch.id)
        )
    ).scalars().all()
    next_position = (
        max((p.batch_position or 0) for p in existing) if existing else 0
    ) + 1

    snap = batch.settings_snapshot or {}
    template_meta = snap.get("meta") if isinstance(snap.get("meta"), dict) else {}
    created: list[Project] = []

    # Карточные поля, попадающие в Project.meta["topic_card"].
    CARD_KEYS = [
        "title", "source", "style", "hook_type", "emotion", "fact",
        "logic", "integration", "shoot_note",
    ]

    # Снимок постоянного продукта массового — копируем в meta каждого
    # подпроекта, чтобы build-функции работали синхронно без запросов к БД.
    perm_product = (batch.meta or {}).get("permanent_product")

    for offset, card in enumerate(norm_topics):
        position = next_position + offset
        title = card["title"]
        base = make_sub_slug(batch.slug, position, title)
        slug = await _unique_project_slug(session, base)

        # hero_mode из карточки (если указан) перебивает наследование из snapshot.
        card_hero_mode = (card.get("hero_mode") or "").strip().lower() or None

        # Карточные поля, кроме служебных, → meta["topic_card"].
        topic_card = {k: card[k] for k in CARD_KEYS if card.get(k)}
        meta = _merge_subproject_meta(
            topic_card=topic_card,
            permanent_product=perm_product,
            template_meta=template_meta,
        )

        kwargs: dict = {
            "slug": slug,
            "topic": title,
            "status": ProjectStatus.new,
            "batch_id": batch.id,
            "batch_position": position,
            "batch_slug": batch.slug,
            "meta": meta,
            # auto_mode наследуется из snapshot (если зашит); по умолчанию False
            "auto_mode": bool(snap.get("auto_mode", False)),
        }
        for f in TEMPLATE_FIELDS:
            if f in snap and snap[f] is not None:
                import copy as _copy
                v = snap[f]
                if isinstance(v, (dict, list)):
                    v = _copy.deepcopy(v)
                kwargs[f] = v

        # hero_mode: явный из карточки > из snapshot > default 'auto'.
        if card_hero_mode in ("hero", "no_hero", "auto"):
            kwargs["hero_mode"] = card_hero_mode
        elif "hero_mode" not in kwargs or not kwargs.get("hero_mode"):
            kwargs["hero_mode"] = "auto"

        proj = Project(**kwargs)
        session.add(proj)
        await session.flush()

        # Папка подпроекта на диске + все подпапки.
        sub_dir = proj.data_dir
        sub_dir.mkdir(parents=True, exist_ok=True)
        for sub in (
            "characters",
            "items",
            "scenes",
            "videos",
            "audio",
            "subs",
            "final",
        ):
            (sub_dir / sub).mkdir(parents=True, exist_ok=True)

        created.append(proj)
        await ensure_subproject_workflow_run(session, proj)
        layout_svc.ensure_batch_subproject_layout(
            proj.id, batch_id=batch.id, batch_position=position
        )
        logger.info(
            "batches: sub-project #{} '{}' (slug={}, pos={}) added to batch #{} "
            "[card_keys={}]",
            proj.id, title, proj.slug, position, batch.id,
            list(topic_card.keys()),
        )

    return created


async def list_batches(session: AsyncSession) -> list[BatchProject]:
    """Все массовые проекты, новые сверху."""
    return list(
        (
            await session.execute(
                select(BatchProject).order_by(BatchProject.id.desc())
            )
        ).scalars().all()
    )


async def batch_progress(
    session: AsyncSession,
    batch: BatchProject,
) -> dict:
    """Сводка по подпроектам массового: счётчики по статусам.

    Возвращает:
      {
        "total": int,
        "queued": int,       # new, planning, …
        "in_progress": int,  # любой *ing
        "ready": int,        # любой *_ready (не финальный)
        "done": int,         # published
        "paused": int,
        "failed": int,
        "by_status": {status_value: count}
      }
    """
    subs = (
        await session.execute(
            select(Project)
            .where(Project.batch_id == batch.id)
            .order_by(Project.batch_position.asc())
        )
    ).scalars().all()

    by_status: dict[str, int] = {}
    queued = in_progress = ready = done = paused = failed = 0
    for p in subs:
        st = p.status
        by_status[st.value] = by_status.get(st.value, 0) + 1
        if st is ProjectStatus.published:
            done += 1
        elif st is ProjectStatus.paused:
            paused += 1
        elif st is ProjectStatus.failed:
            failed += 1
        elif st is ProjectStatus.new:
            queued += 1
        elif st.value.endswith("_ready"):
            ready += 1
        else:
            in_progress += 1

    return {
        "total": len(subs),
        "queued": queued,
        "in_progress": in_progress,
        "ready": ready,
        "done": done,
        "paused": paused,
        "failed": failed,
        "by_status": by_status,
    }


async def delete_batch(
    session: AsyncSession,
    batch_id: int,
    *,
    delete_files: bool = True,
) -> None:
    """Удаляет массовый проект + все его подпроекты + (опционально) папку.

    Папка на диске удаляется, если delete_files=True (default). Подпроекты
    удаляются каскадно по batch_id (или, для безопасности, обнулением
    ссылки batch_id — мы выбираем безопасный путь: SET NULL по FK +
    явное удаление здесь, чтобы не оставлять сирот).
    """
    batch = (
        await session.execute(
            select(BatchProject).where(BatchProject.id == batch_id)
        )
    ).scalar_one_or_none()
    if batch is None:
        return

    subs = (
        await session.execute(
            select(Project).where(Project.batch_id == batch_id)
        )
    ).scalars().all()
    for p in subs:
        layout_svc.remove_project_from_layout(p.id)
        await session.delete(p)

    layout_svc.delete_batch_folder(batch_id)

    base_dir = batch.data_dir
    await session.delete(batch)
    await session.flush()

    if delete_files and base_dir.exists():
        try:
            shutil.rmtree(base_dir)
            logger.info("batches: removed dir {}", base_dir)
        except Exception as e:  # noqa: BLE001
            logger.warning("batches: failed to remove {}: {}", base_dir, e)


async def get_batch(
    session: AsyncSession, batch_id: int
) -> BatchProject | None:
    return (
        await session.execute(
            select(BatchProject).where(BatchProject.id == batch_id)
        )
    ).scalar_one_or_none()


async def get_batch_subprojects(
    session: AsyncSession,
    batch_id: int,
) -> list[Project]:
    """Подпроекты массового, отсортированные по batch_position."""
    return list(
        (
            await session.execute(
                select(Project)
                .where(Project.batch_id == batch_id)
                .order_by(Project.batch_position.asc())
            )
        ).scalars().all()
    )


# ----------------------------------------------------------------------
# Постоянный продукт массового (PR #3)
# ----------------------------------------------------------------------


def _ensure_meta(batch: BatchProject) -> dict:
    if batch.meta is None:
        batch.meta = {}
    return batch.meta


async def set_permanent_product_field(
    session: AsyncSession,
    batch_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    reference_image_path: str | None = None,
) -> BatchProject | None:
    """Обновляет одно или несколько полей постоянного продукта массового.

    Передавать только нужные kwarg'и (другие останутся без изменений).
    Поле сохраняется в `batch.meta["permanent_product"]`:
      - name: как называть в кадре/сценарии
      - description: описание (вид, использование, фишка)
      - reference_image_path: путь к загруженной картинке-референсу
    """
    batch = await get_batch(session, batch_id)
    if batch is None:
        return None
    meta = _ensure_meta(batch)
    prod = dict(meta.get("permanent_product") or {})
    if name is not None:
        prod["name"] = name.strip() or None
    if description is not None:
        prod["description"] = description.strip() or None
    if reference_image_path is not None:
        prod["reference_image_path"] = reference_image_path or None
    # Не храним полностью пустой dict — но сохраняем структуру если есть хоть
    # одно непустое поле.
    cleaned = {k: v for k, v in prod.items() if v}
    if cleaned:
        meta["permanent_product"] = cleaned
    else:
        meta.pop("permanent_product", None)
    # SQLAlchemy с JSON-полем не всегда триггерит dirty по mutate — присваиваем
    # ссылку заново.
    batch.meta = dict(meta)
    await session.flush()
    # (parity #7) Пробрасываем изменения не только в new, но и в ожидающие
    # пре-visual стадии (planning/scripting/splitting). Ниже оседаем
    # в batch.meta["product_late_subs"] список суб-id'ов, которые уже
    # прошли этот порог — юзер в меню увидит предупреждение.
    too_late = await _propagate_product_to_new_subs(
        session, batch.id, meta.get("permanent_product")
    )
    if too_late:
        meta = dict(batch.meta or {})
        meta["product_late_subs"] = too_late
        batch.meta = meta
        await session.flush()
    return batch


async def clear_permanent_product(
    session: AsyncSession, batch_id: int
) -> BatchProject | None:
    """Полностью удаляет постоянный продукт массового."""
    batch = await get_batch(session, batch_id)
    if batch is None:
        return None
    meta = _ensure_meta(batch)
    if "permanent_product" in meta:
        meta = dict(meta)
        meta.pop("permanent_product", None)
        batch.meta = meta
        await session.flush()
    # При очистке продукта игнорируем «too_late» — без продукта
    # нет риска несогласованности.
    await _propagate_product_to_new_subs(session, batch_id, None)
    return batch


# (single-mass parity #7) Какие статусы мы считаем «безопасными» для
# retro-replace permanent_product. После frames_ready (появились
# именованные кадры) — менять продукт опасно: персонажи/предметы
# уже сгенерируются в контексте старого продукта.
_SAFE_PRODUCT_RETRO_STATUSES = {
    ProjectStatus.new,
    ProjectStatus.planning,
    ProjectStatus.plan_ready,
    ProjectStatus.scripting,
    ProjectStatus.script_ready,
    ProjectStatus.splitting,
}


async def _propagate_product_to_new_subs(
    session: AsyncSession,
    batch_id: int,
    product: dict | None,
) -> list[int]:
    """Обновляет meta["permanent_product"] у подпроектов массового.

    (single-mass parity #7) Раньше обновлялись ТОЛЬКО подпроекты
    в статусе new — это означало, что если юзер продукт поменял/добавил
    после старта очереди, первые несколько видео рисуались без него.

    Теперь продукт прописывается в любой sub-project в пре-visual
    статусе (до frames_ready). Субы, которые уже перевалили за
    frames_ready / hero, НЕ трогаются (это привело бы к несогласован-
    ности герои/предметы внутри одного видео), но возвращаются
    их ID — вызывающий код (меню «Продукт») может показать юзеру
    список «вот в этих видео продукт не упомянется».

    Returns: список sub-project id'ов, которые были «слишком далеко»
    и НЕ получили обновленный продукт. Нужно для UI hint.
    """
    import copy as _copy

    all_subs = (
        await session.execute(
            select(Project).where(
                Project.batch_id == batch_id,
                Project.status.not_in(
                    [ProjectStatus.published, ProjectStatus.failed]
                ),
            )
        )
    ).scalars().all()
    too_late: list[int] = []
    updated = 0
    for p in all_subs:
        if p.status not in _SAFE_PRODUCT_RETRO_STATUSES:
            too_late.append(p.id)
            continue
        m = dict(p.meta or {})
        if product and product.get("name"):
            m["permanent_product"] = _copy.deepcopy(product)
        else:
            m.pop("permanent_product", None)
        p.meta = m
        updated += 1
    if updated:
        await session.flush()
    return too_late


def get_permanent_product(batch: BatchProject) -> dict | None:
    """Удобный аксессор для подпроектов: читает product-карточку из batch."""
    meta = batch.meta or {}
    prod = meta.get("permanent_product")
    if prod and prod.get("name"):
        return dict(prod)
    return None


# ----------------------------------------------------------------------
# BLOCK B — Настройки массовой генерации
# ----------------------------------------------------------------------

# Дефолты всех «переключателей» режима. Хранятся в
# batch.settings_snapshot["mass_settings"] (JSON sub-dict).
DEFAULT_MASS_SETTINGS: dict = {
    "enrich_slots_count": 3,        # 1..5
    "hero_count": 1,                # 1..5
    "hero_variations": 1,           # 1..5 (применяется ко всем героям)
    "excel_hero_enabled": False,    # bool
    "auto_mode": True,              # bool — default for sub-projects
    "bgm_enabled": False,           # bool
    "bgm_level": 70,                # 0..100
    "pause_minutes": 0,             # пауза между sub'ами, мин
    "max_parallelism": 1,           # пока всегда 1
    "auto_review_kinds": [],        # пусто — visual=auto-approve
}

_INT_LIMITS: dict[str, tuple[int, int]] = {
    "enrich_slots_count": (1, 5),
    "hero_count": (1, 5),
    "hero_variations": (1, 5),
    "bgm_level": (0, 100),
    "pause_minutes": (0, 1440),
    "max_parallelism": (1, 1),  # пока фиксируем 1
}

_BOOL_FIELDS = {
    "excel_hero_enabled",
    "auto_mode",
    "bgm_enabled",
}

_KNOWN_AUTO_REVIEW_KINDS = (
    "approve_plan",
    "approve_script",
    "approve_hero",
    "approve_images",
    "approve_videos",
    "approve_final",
)


def get_mass_settings(batch: BatchProject) -> dict:
    """Читает настройки массового с дефолтами.

    Система хранения: batch.settings_snapshot["mass_settings"]
    (JSON sub-dict). Суб-проекты при старте очереди получают
    эти значения в свои поля / meta (см. apply_mass_settings_to_subs).
    """
    snap = batch.settings_snapshot or {}
    raw = snap.get("mass_settings") or {}
    merged: dict = dict(DEFAULT_MASS_SETTINGS)
    if isinstance(raw, dict):
        for k, default in DEFAULT_MASS_SETTINGS.items():
            v = raw.get(k, default)
            if isinstance(default, bool):
                merged[k] = bool(v)
            elif isinstance(default, int):
                lo, hi = _INT_LIMITS.get(k, (None, None))
                try:
                    iv = int(v)
                except (TypeError, ValueError):
                    iv = int(default)
                if lo is not None and hi is not None:
                    iv = max(lo, min(hi, iv))
                merged[k] = iv
            elif isinstance(default, list):
                if isinstance(v, list):
                    merged[k] = [
                        x for x in v
                        if isinstance(x, str) and x in _KNOWN_AUTO_REVIEW_KINDS
                    ]
                else:
                    merged[k] = list(default)
    return merged


async def set_mass_setting(
    session: AsyncSession,
    batch_id: int,
    field: str,
    value,
) -> BatchProject | None:
    """Установить одно поле масс-настроек (типы/границы сами
    проверяем). Value-приведение: bool/int/list — по дефолтуф
    в DEFAULT_MASS_SETTINGS[field]."""
    batch = await get_batch(session, batch_id)
    if batch is None or field not in DEFAULT_MASS_SETTINGS:
        return None
    current = get_mass_settings(batch)
    default = DEFAULT_MASS_SETTINGS[field]
    if isinstance(default, bool):
        current[field] = bool(value)
    elif isinstance(default, int):
        try:
            iv = int(value)
        except (TypeError, ValueError):
            iv = int(default)
        lo, hi = _INT_LIMITS.get(field, (None, None))
        if lo is not None and hi is not None:
            iv = max(lo, min(hi, iv))
        current[field] = iv
    elif isinstance(default, list):
        if isinstance(value, list):
            current[field] = [
                x for x in value
                if isinstance(x, str) and x in _KNOWN_AUTO_REVIEW_KINDS
            ]
    snap = dict(batch.settings_snapshot or {})
    snap["mass_settings"] = current
    batch.settings_snapshot = snap
    await session.flush()
    return batch


async def toggle_mass_setting(
    session: AsyncSession, batch_id: int, field: str
) -> BatchProject | None:
    """Переключает bool-поле; для «ложных bool» мы трактуем
    auto_review_kinds.<kind> как присутствие в списке."""
    batch = await get_batch(session, batch_id)
    if batch is None:
        return None
    current = get_mass_settings(batch)
    if field in _BOOL_FIELDS:
        current[field] = not bool(current.get(field))
    elif field.startswith("auto_review_kinds."):
        kind = field.split(".", 1)[1]
        if kind not in _KNOWN_AUTO_REVIEW_KINDS:
            return batch
        lst = list(current.get("auto_review_kinds") or [])
        if kind in lst:
            lst.remove(kind)
        else:
            lst.append(kind)
        current["auto_review_kinds"] = lst
    else:
        return batch
    snap = dict(batch.settings_snapshot or {})
    snap["mass_settings"] = current
    batch.settings_snapshot = snap
    await session.flush()
    return batch


async def apply_mass_settings_to_subs(
    session: AsyncSession, batch_id: int
) -> int:
    """Перед стартом очереди переносим масс-настройки в каждый
    sub-project, который ещё не вышел из status==new (сохраняем
    parity-принцип #7: in-flight sub'ы НЕ трогаем).

    Маппинг:
      enrich_slots_count, hero_count, hero_variations → прямо в колонки.
      auto_mode                                       → прямо в колонку.
      excel_hero_enabled                              → meta["excel_hero_enabled"].
      bgm_*, pause_minutes, max_parallelism           → meta["mass_*"].
      auto_review_kinds                               → meta["auto_review_kinds"].
    Returns: количество обновлённых sub-проектов.
    """
    batch = await get_batch(session, batch_id)
    if batch is None:
        return 0
    ms = get_mass_settings(batch)
    subs = (
        await session.execute(
            select(Project).where(
                Project.batch_id == batch_id,
                Project.status == ProjectStatus.new,
            )
        )
    ).scalars().all()
    if not subs:
        return 0
    n_hero = int(ms["hero_count"])
    n_var = int(ms["hero_variations"])
    for p in subs:
        p.enrich_slots_count = int(ms["enrich_slots_count"])
        p.hero_count = n_hero
        p.hero_variations = [n_var] * n_hero
        p.auto_mode = bool(ms["auto_mode"])
        m = dict(p.meta or {})
        m["excel_hero_enabled"] = bool(ms["excel_hero_enabled"])
        m["mass_bgm_enabled"] = bool(ms["bgm_enabled"])
        m["mass_bgm_level"] = int(ms["bgm_level"])
        m["mass_pause_minutes"] = int(ms["pause_minutes"])
        m["mass_max_parallelism"] = int(ms["max_parallelism"])
        m["auto_review_kinds"] = list(ms["auto_review_kinds"])
        p.meta = m
    await session.flush()
    logger.info(
        "batch #{} ({}): applied mass settings to {} sub(s)",
        batch.id, batch.slug, len(subs),
    )
    return len(subs)


# ----------------------------------------------------------------------
# Управление очередью (PR #2)
# ----------------------------------------------------------------------


async def start_batch_queue(
    session: AsyncSession, batch_id: int
) -> BatchProject | None:
    """Запустить очередь массового: status=running + auto_mode=True для
    всех подпроектов, которые ещё не закончены.

    Воркер (serial_tick_batches) сам подхватит первого по batch_position
    в статусе `new` и переведёт его в planning.
    """
    batch = await get_batch(session, batch_id)
    if batch is None:
        return None
    batch.status = BatchStatus.running
    # (BLOCK B) Перед стартом — переносим mass_settings в каждый
    # sub (только в status==new). Остальные in-flight sub'ы неизменны.
    await apply_mass_settings_to_subs(session, batch_id)
    # Включаем auto_mode для всех подпроектов которые ещё в работе,
    # переопределяя масс-настройкой auto_mode (юзер может хотеть
    # ruchnoy режим внутри batch'а).
    subs = await get_batch_subprojects(session, batch_id)
    terminal = {ProjectStatus.published, ProjectStatus.failed}
    ms = get_mass_settings(batch)
    auto_default = bool(ms["auto_mode"])
    for p in subs:
        if p.status not in terminal:
            p.auto_mode = auto_default
    await session.flush()
    logger.info(
        "batch #{} ({}): очередь запущена, auto_mode default={}, субы: {}",
        batch.id, batch.slug, auto_default,
        sum(1 for p in subs if p.status not in terminal),
    )
    return batch


async def pause_batch_queue(
    session: AsyncSession, batch_id: int
) -> BatchProject | None:
    """Поставить очередь на паузу.

    Текущий running-подпроект НЕ прерываем (он доработает текущий шаг
    до *_ready, но дальше не пойдёт). Следующие подпроекты не стартуют.
    """
    batch = await get_batch(session, batch_id)
    if batch is None:
        return None
    batch.status = BatchStatus.paused
    # Снимаем auto_mode у подпроектов в *_ready состоянии и new,
    # чтобы auto_advance их не двигал.
    subs = await get_batch_subprojects(session, batch_id)
    for p in subs:
        if p.status is ProjectStatus.new or p.status.value.endswith("_ready"):
            p.auto_mode = False
    await session.flush()
    logger.info("batch #{} ({}): пауза", batch.id, batch.slug)
    return batch


async def resume_batch_queue(
    session: AsyncSession, batch_id: int
) -> BatchProject | None:
    """Снять с паузы: то же что start_batch_queue, но не двигает
    подпроекты в paused-состоянии (только включает auto_mode у new
    и *_ready)."""
    batch = await get_batch(session, batch_id)
    if batch is None:
        return None
    batch.status = BatchStatus.running
    subs = await get_batch_subprojects(session, batch_id)
    terminal = {ProjectStatus.published, ProjectStatus.failed, ProjectStatus.paused}
    for p in subs:
        if p.status not in terminal:
            p.auto_mode = True
    await session.flush()
    logger.info("batch #{} ({}): возобновлено", batch.id, batch.slug)
    return batch


async def retry_paused_subprojects(
    session: AsyncSession, batch_id: int
) -> int:
    """Вернуть все подпроекты в paused → new, чтобы воркер их подхватил.

    Сбрасывает счётчики авто-retry, очищает auto_paused_reason.
    Возвращает кол-во возвращённых подпроектов.
    """
    subs = await get_batch_subprojects(session, batch_id)
    count = 0
    for p in subs:
        if p.status is ProjectStatus.paused:
            p.status = ProjectStatus.new
            p.auto_mode = True
            # Очищаем мета-флаги авто-paused.
            meta = dict(p.meta or {})
            for k in list(meta.keys()):
                if k.startswith("auto_retry_") or k in (
                    "auto_paused_reason", "auto_paused_fix_hints",
                ):
                    del meta[k]
            p.meta = meta
            count += 1
    await session.flush()
    logger.info(
        "batch #{}: вернули в очередь {} paused-подпроект(ов)",
        batch_id, count,
    )
    return count


async def pause_all_running_batches(
    session: AsyncSession,
) -> dict[str, int]:
    """Жёсткая пауза ВСЕЙ массовой генерации.

    Применяется при «⏸ Пауза массовой» в главном меню.

    Делает три вещи:
      1) batch.status = paused для всех батчей с status=running;
      2) auto_mode=False у всех batch-подпроектов в new и *_ready
         (как в `pause_batch_queue` для одного батча);
      3) ROLLBACK running-статуса у всех batch-подпроектов на их
         prerequisite *_ready. Это и есть «стоп бесконечных скриптов»
         — если до этого maybe_auto_advance гонял такой подпроект
         по кругу running→ready→running, теперь он зависнет в *_ready
         и без снятия паузы не двинется.

    Возвращает {"batches": N, "rolled_back": M, "auto_mode_off": K}.
    """
    from app.telegram.menu import step_by_running_status
    from sqlalchemy import select as _sel

    out = {"batches": 0, "rolled_back": 0, "auto_mode_off": 0}

    batches_q = await session.execute(
        _sel(BatchProject).where(BatchProject.status == BatchStatus.running)
    )
    for b in batches_q.scalars().all():
        b.status = BatchStatus.paused
        out["batches"] += 1

    subs_q = await session.execute(
        _sel(Project).where(Project.batch_id.is_not(None))
    )
    for p in subs_q.scalars().all():
        # 1) Если статус — running, ROLLBACK на prerequisite *_ready.
        #    Это останавливает бесконечные циклы maybe_auto_advance.
        step = step_by_running_status(p.status)
        if step is not None and step.requires is not None:
            logger.info(
                "[#{}] MASS PAUSE rollback: {} -> {}",
                p.id, p.status.value, step.requires.value,
            )
            p.status = step.requires
            out["rolled_back"] += 1
        # 2) Снимаем auto_mode у всех new и *_ready (после rollback это
        #    и есть бывшие running-подпроекты тоже).
        if p.status is ProjectStatus.new or p.status.value.endswith("_ready"):
            if p.auto_mode:
                p.auto_mode = False
                out["auto_mode_off"] += 1

    await session.flush()
    logger.info(
        "MASS PAUSE: paused {} batches, rolled back {} running subs, "
        "auto_mode off for {} subs",
        out["batches"], out["rolled_back"], out["auto_mode_off"],
    )
    return out


async def resume_all_paused_batches(
    session: AsyncSession,
) -> dict[str, int]:
    """Снять жёсткую паузу: batch.status=paused → running, и включить
    auto_mode у новых/*_ready batch-подпроектов (НЕ у paused/failed,
    те так и остаются — их надо явно `retry_paused_subprojects`)."""
    from sqlalchemy import select as _sel

    out = {"batches": 0, "auto_mode_on": 0}

    batches_q = await session.execute(
        _sel(BatchProject).where(BatchProject.status == BatchStatus.paused)
    )
    for b in batches_q.scalars().all():
        b.status = BatchStatus.running
        out["batches"] += 1

    subs_q = await session.execute(
        _sel(Project).where(Project.batch_id.is_not(None))
    )
    terminal = {
        ProjectStatus.published,
        ProjectStatus.failed,
        ProjectStatus.paused,
    }
    for p in subs_q.scalars().all():
        if p.status not in terminal and not p.auto_mode:
            p.auto_mode = True
            out["auto_mode_on"] += 1

    await session.flush()
    logger.info(
        "MASS RESUME: resumed {} batches, auto_mode on for {} subs",
        out["batches"], out["auto_mode_on"],
    )
    return out


# ----------------------------------------------------------------------
# Миграция: очистка унаследованного мусора в meta подпроектов
# ----------------------------------------------------------------------


async def clean_subprojects_meta(
    session: AsyncSession,
    *,
    batch_id: int | None = None,
) -> dict:
    """Удаляет унаследованный runtime-мусор из meta подпроектов.

    Восстанавливает topic_card из topics.xlsx батча (по slug или position).
    Returns: {"projects": N, "stripped_keys": M, "topic_cards_restored": K}.
    """
    from app.storage import batch_sheet

    q = select(Project).where(Project.batch_id.is_not(None))
    if batch_id is not None:
        q = q.where(Project.batch_id == batch_id)
    subs = (await session.execute(q)).scalars().all()

    batches_cache: dict[int, BatchProject | None] = {}
    topics_by_batch: dict[int, dict[str, dict]] = {}
    topics_by_pos: dict[int, dict[int, dict]] = {}

    out = {"projects": 0, "stripped_keys": 0, "topic_cards_restored": 0}

    for p in subs:
        bid = p.batch_id
        if bid is None:
            continue
        if bid not in batches_cache:
            batch = await get_batch(session, bid)
            batches_cache[bid] = batch
            if batch is not None:
                rows = batch_sheet.read_topics(batch.topics_xlsx_path)
                topics_by_batch[bid] = {
                    (r.get("slug") or ""): r for r in rows if r.get("slug")
                }
                by_pos: dict[int, dict] = {}
                for r in rows:
                    try:
                        pos = int(r.get("position"))
                    except (TypeError, ValueError):
                        continue
                    by_pos[pos] = r
                topics_by_pos[bid] = by_pos

        meta = dict(p.meta or {})
        before_len = len(meta)
        meta = _strip_forbidden_meta_keys(meta)
        out["stripped_keys"] += max(0, before_len - len(meta))

        batch = batches_cache.get(bid)
        if batch is not None:
            row = topics_by_batch.get(bid, {}).get(p.slug)
            if row is None and p.batch_position is not None:
                row = topics_by_pos.get(bid, {}).get(int(p.batch_position))
            if row:
                card = batch_sheet.topic_card_from_row(row)
                if card:
                    meta["topic_card"] = card
                    out["topic_cards_restored"] += 1

        if meta != (p.meta or {}):
            p.meta = meta
            out["projects"] += 1

    if out["projects"]:
        await session.flush()
    return out
