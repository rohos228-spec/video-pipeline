"""Local, SQLite-versioned prompt library.

SQLite is the source of truth for versions. Files under data/library/current
are materialized copies used by the existing prompt composer and by users who
want to inspect the library on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import LibraryConfig, LibraryEvent, LibraryItem, LibraryVersion, Project
from app.project_root import find_project_root
from app.settings import settings

REPO_ROOT = find_project_root()
PROMPTS_ROOT = REPO_ROOT / "prompts"
LIBRARY_ROOT = settings.data_dir / "library"
CURRENT_ROOT = LIBRARY_ROOT / "current"
OLD_ROOT = LIBRARY_ROOT / "old"
LOGS_ROOT = LIBRARY_ROOT / "logs"

TEXT_SUFFIXES = {".md", ".txt", ".json", ".yaml", ".yml"}
SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9а-яА-ЯёЁ_.-]+")


def library_roots() -> dict[str, Path]:
    return {
        "root": LIBRARY_ROOT,
        "current": CURRENT_ROOT,
        "old": OLD_ROOT,
        "logs": LOGS_ROOT,
    }


def use_local_library_prompts() -> bool:
    """Whether runtime prompt reads should prefer data/library/current.

    Tests must be deterministic and must not depend on a developer's local
    saved prompts. Normal app runs keep the local library enabled.
    """
    raw = os.getenv("VIDEO_PIPELINE_USE_LOCAL_LIBRARY")
    if raw is not None:
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return "pytest" not in sys.modules


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_hash(data: Any) -> str:
    return content_hash(json.dumps(data, ensure_ascii=False, sort_keys=True, default=str))


def _now_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


def _ensure_safe_rel_path(value: str | Path) -> Path:
    rel = Path(str(value).replace("\\", "/"))
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError(f"unsafe library path: {value}")
    return rel


def _materialized_path(file_path: str | Path) -> Path:
    rel = _ensure_safe_rel_path(file_path)
    return CURRENT_ROOT / rel


def _old_snapshot_path(item: LibraryItem, version: int) -> Path:
    rel = _ensure_safe_rel_path(item.file_path)
    return OLD_ROOT / f"{_now_stamp()}_item{item.id}_v{version}" / rel


def classify_prompt_rel_path(rel: Path) -> str:
    parts = rel.parts
    if not parts:
        return "prompt"
    if parts[0] == "blocks":
        return "block"
    if parts[0] == "steps":
        return "step_template"
    if parts[0] == "styles":
        return "style"
    if parts[0] == "step-presets":
        return "step_preset"
    return "prompt"


def title_from_path(rel: Path) -> str:
    return rel.stem.replace("_", " ").replace("-", " ").strip() or rel.stem


def safe_key_part(value: str) -> str:
    cleaned = SAFE_KEY_RE.sub("_", value.strip()).strip("._-")
    return cleaned[:80] or "prompt"


async def log_event(
    session: AsyncSession,
    event_type: str,
    *,
    item: LibraryItem | None = None,
    payload: dict[str, Any] | None = None,
) -> LibraryEvent:
    evt = LibraryEvent(item_id=item.id if item else None, event_type=event_type, payload=payload or {})
    session.add(evt)
    return evt


async def get_item(session: AsyncSession, item_id: int) -> LibraryItem | None:
    return await session.get(LibraryItem, item_id)


async def get_item_by_key(session: AsyncSession, kind: str, key: str) -> LibraryItem | None:
    return (
        await session.execute(
            select(LibraryItem).where(LibraryItem.kind == kind, LibraryItem.key == key)
        )
    ).scalar_one_or_none()


async def list_items(
    session: AsyncSession,
    *,
    kind: str | None = None,
    q: str | None = None,
    limit: int = 500,
) -> list[LibraryItem]:
    stmt = select(LibraryItem).order_by(LibraryItem.kind.asc(), LibraryItem.key.asc()).limit(limit)
    if kind:
        stmt = stmt.where(LibraryItem.kind == kind)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(LibraryItem.key.ilike(like) | LibraryItem.title.ilike(like))
    return list((await session.execute(stmt)).scalars().all())


async def create_or_update_item(
    session: AsyncSession,
    *,
    kind: str,
    key: str,
    title: str,
    file_path: str,
    content: str,
    message: str | None = None,
    author: str | None = "local",
    source: str | None = None,
    meta: dict[str, Any] | None = None,
    force_version: bool = False,
) -> tuple[LibraryItem, LibraryVersion, bool]:
    """Create or update an item.

    Returns (item, version, created_new_version).
    """
    _ensure_safe_rel_path(file_path)
    h = content_hash(content)
    item = await get_item_by_key(session, kind, key)
    if item is None:
        item = LibraryItem(
            kind=kind,
            key=key,
            title=title,
            file_path=file_path,
            active_version=1,
            content_hash=h,
            meta=meta or {},
        )
        session.add(item)
        await session.flush()
        version = LibraryVersion(
            item_id=item.id,
            version=1,
            content=content,
            content_hash=h,
            message=message or "initial import",
            author=author,
            source=source,
            file_path=file_path,
            meta=meta or {},
        )
        session.add(version)
        await session.flush()
        _write_current(item.file_path, content)
        _write_old_snapshot(item, version.version, content)
        await log_event(
            session,
            "created",
            item=item,
            payload={"version": version.version, "source": source},
        )
        return item, version, True

    latest = await get_active_version(session, item)
    if latest and latest.content_hash == h and not force_version:
        item.title = title or item.title
        item.file_path = file_path
        item.meta = {**(item.meta or {}), **(meta or {})}
        _write_current(item.file_path, latest.content)
        return item, latest, False

    next_version = (item.active_version or 0) + 1
    item.title = title or item.title
    item.file_path = file_path
    item.active_version = next_version
    item.content_hash = h
    item.meta = {**(item.meta or {}), **(meta or {})}
    version = LibraryVersion(
        item_id=item.id,
        version=next_version,
        content=content,
        content_hash=h,
        message=message or "updated",
        author=author,
        source=source,
        file_path=file_path,
        meta=meta or {},
    )
    session.add(version)
    await session.flush()
    _write_current(item.file_path, content)
    _write_old_snapshot(item, version.version, content)
    await log_event(
        session,
        "updated",
        item=item,
        payload={"version": version.version, "source": source},
    )
    return item, version, True


async def get_active_version(
    session: AsyncSession,
    item: LibraryItem,
) -> LibraryVersion | None:
    return (
        await session.execute(
            select(LibraryVersion).where(
                LibraryVersion.item_id == item.id,
                LibraryVersion.version == item.active_version,
            )
        )
    ).scalar_one_or_none()


async def list_versions(session: AsyncSession, item_id: int) -> list[LibraryVersion]:
    return list(
        (
            await session.execute(
                select(LibraryVersion)
                .where(LibraryVersion.item_id == item_id)
                .order_by(LibraryVersion.version.desc())
            )
        )
        .scalars()
        .all()
    )


async def restore_version(
    session: AsyncSession,
    item_id: int,
    version: int,
    *,
    author: str | None = "local",
) -> tuple[LibraryItem, LibraryVersion]:
    item = await session.get(LibraryItem, item_id)
    if item is None:
        raise FileNotFoundError("library item not found")
    target = (
        await session.execute(
            select(LibraryVersion).where(
                LibraryVersion.item_id == item_id,
                LibraryVersion.version == version,
            )
        )
    ).scalar_one_or_none()
    if target is None:
        raise FileNotFoundError("library version not found")
    _, new_version, _ = await create_or_update_item(
        session,
        kind=item.kind,
        key=item.key,
        title=item.title,
        file_path=item.file_path,
        content=target.content,
        message=f"restore v{version}",
        author=author,
        source="restore",
        meta={**(item.meta or {}), "restored_from": version},
        force_version=True,
    )
    await log_event(
        session,
        "restored",
        item=item,
        payload={"from_version": version, "to_version": new_version.version},
    )
    return item, new_version


async def save_config(
    session: AsyncSession,
    *,
    name: str,
    snapshot: dict[str, Any],
    project_id: int | None = None,
    meta: dict[str, Any] | None = None,
) -> LibraryConfig:
    cfg = LibraryConfig(
        name=name.strip() or f"config-{_now_stamp()}",
        project_id=project_id,
        snapshot=snapshot,
        content_hash=json_hash(snapshot),
        meta=meta or {},
    )
    session.add(cfg)
    await session.flush()
    await log_event(session, "config_saved", payload={"config_id": cfg.id, "project_id": project_id})
    _write_json(CURRENT_ROOT / "configs" / f"{cfg.id}.json", cfg.snapshot)
    _write_json(OLD_ROOT / f"{_now_stamp()}_config{cfg.id}" / "config.json", cfg.snapshot)
    return cfg


async def apply_config_to_project(
    session: AsyncSession,
    *,
    config_id: int,
    project: Project,
) -> Project:
    cfg = await session.get(LibraryConfig, config_id)
    if cfg is None:
        raise FileNotFoundError("library config not found")
    snap = dict(cfg.snapshot or {})
    if isinstance(snap.get("prompt_overrides"), dict):
        project.prompt_overrides = snap["prompt_overrides"]
    if isinstance(snap.get("gpt_text_overrides"), dict):
        project.gpt_text_overrides = snap["gpt_text_overrides"]
    if isinstance(snap.get("meta"), dict):
        project.meta = {**(project.meta or {}), **snap["meta"]}
    await log_event(
        session,
        "config_applied",
        payload={"config_id": cfg.id, "project_id": project.id},
    )
    return project


async def save_project_config(
    session: AsyncSession,
    *,
    project: Project,
    name: str | None = None,
    workflow_snapshot: dict[str, Any] | None = None,
) -> LibraryConfig:
    snapshot = {
        "project_id": project.id,
        "project_slug": project.slug,
        "topic": project.topic,
        "prompt_overrides": project.prompt_overrides or {},
        "gpt_text_overrides": project.gpt_text_overrides or {},
        "meta": project.meta or {},
        "workflow": workflow_snapshot or {},
    }
    return await save_config(
        session,
        name=name or f"{project.slug}-config-{_now_stamp()}",
        project_id=project.id,
        snapshot=snapshot,
        meta={"kind": "project_config"},
    )


async def save_prompt_bundle(
    session: AsyncSession,
    *,
    bundle_key: str,
    title: str,
    source_prompt: str,
    processed_prompt: str,
    blocks: list[dict[str, Any]],
    source_path: str | None = None,
    project_id: int | None = None,
    step_id: str | None = None,
    step_code: str | None = None,
) -> dict[str, LibraryItem]:
    """Store a full prompt package: source, processed result, final blocks.

    Each piece is a regular versioned LibraryItem. `bundle_id` in meta links
    them together for UI/history/export.
    """
    bundle_id = f"{safe_key_part(bundle_key)}-{_now_stamp()}"
    base = Path("prompts") / "bundles" / safe_key_part(bundle_key)
    meta = {
        "bundle_id": bundle_id,
        "project_id": project_id,
        "step_id": step_id,
        "step_code": step_code,
        "source_path": source_path,
    }
    source_item, _source_version, _ = await create_or_update_item(
        session,
        kind="source_prompt",
        key=(base / "source.md").as_posix(),
        title=f"{title} · source",
        file_path=(base / "source.md").as_posix(),
        content=source_prompt,
        message="save prompt bundle source",
        author="studio",
        source="prompt_bundle",
        meta=meta,
        force_version=True,
    )
    processed_item, _processed_version, _ = await create_or_update_item(
        session,
        kind="processed_prompt",
        key=(base / "processed.md").as_posix(),
        title=f"{title} · processed",
        file_path=(base / "processed.md").as_posix(),
        content=processed_prompt,
        message="save prompt bundle processed",
        author="studio",
        source="prompt_bundle",
        meta=meta,
        force_version=True,
    )
    block_items: list[LibraryItem] = []
    for index, block in enumerate(blocks, start=1):
        block_kind = str(block.get("kind") or block.get("category") or "block")
        block_label = str(block.get("label") or block_kind)
        block_body = str(block.get("body") or block.get("content") or "")
        block_key = safe_key_part(f"{index:02d}_{block_kind}_{block_label}")
        item, _version, _ = await create_or_update_item(
            session,
            kind="prompt_block",
            key=(base / "blocks" / f"{block_key}.md").as_posix(),
            title=f"{title} · {block_label}",
            file_path=(base / "blocks" / f"{block_key}.md").as_posix(),
            content=block_body,
            message="save prompt bundle block",
            author="studio",
            source="prompt_bundle",
            meta={**meta, "block_index": index, "block_kind": block_kind, "block_label": block_label},
            force_version=True,
        )
        block_items.append(item)
    manifest = {
        "bundle_id": bundle_id,
        "title": title,
        "project_id": project_id,
        "step_id": step_id,
        "step_code": step_code,
        "source_path": source_path,
        "source_item_id": source_item.id,
        "processed_item_id": processed_item.id,
        "block_item_ids": [item.id for item in block_items],
    }
    manifest_item, _manifest_version, _ = await create_or_update_item(
        session,
        kind="prompt_bundle",
        key=(base / "manifest.json").as_posix(),
        title=f"{title} · bundle",
        file_path=(base / "manifest.json").as_posix(),
        content=json.dumps(manifest, ensure_ascii=False, indent=2),
        message="save prompt bundle manifest",
        author="studio",
        source="prompt_bundle",
        meta=meta,
        force_version=True,
    )
    await log_event(
        session,
        "prompt_bundle_saved",
        item=manifest_item,
        payload=manifest,
    )
    return {
        "manifest": manifest_item,
        "source": source_item,
        "processed": processed_item,
        "blocks": block_items,
    }


async def list_configs(
    session: AsyncSession,
    *,
    project_id: int | None = None,
    limit: int = 200,
) -> list[LibraryConfig]:
    stmt = select(LibraryConfig).order_by(LibraryConfig.created_at.desc()).limit(limit)
    if project_id is not None:
        stmt = stmt.where(LibraryConfig.project_id == project_id)
    return list((await session.execute(stmt)).scalars().all())


async def list_events(
    session: AsyncSession,
    *,
    item_id: int | None = None,
    event_type: str | None = None,
    limit: int = 200,
) -> list[LibraryEvent]:
    stmt = select(LibraryEvent).order_by(LibraryEvent.created_at.desc()).limit(limit)
    if item_id is not None:
        stmt = stmt.where(LibraryEvent.item_id == item_id)
    if event_type is not None:
        stmt = stmt.where(LibraryEvent.event_type == event_type)
    return list((await session.execute(stmt)).scalars().all())


async def import_existing_prompts(session: AsyncSession) -> dict[str, int]:
    """Import repo prompts into the local library if missing or changed."""
    counts = {"seen": 0, "versions": 0, "skipped": 0}
    if not PROMPTS_ROOT.is_dir():
        return counts
    for src in sorted(PROMPTS_ROOT.rglob("*")):
        if not src.is_file() or src.suffix.lower() not in TEXT_SUFFIXES:
            continue
        rel = src.relative_to(PROMPTS_ROOT)
        try:
            text = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            counts["skipped"] += 1
            continue
        kind = classify_prompt_rel_path(rel)
        file_path = (Path("prompts") / rel).as_posix()
        key = file_path
        try:
            async with session.begin_nested():
                _item, _version, changed = await create_or_update_item(
                    session,
                    kind=kind,
                    key=key,
                    title=title_from_path(rel),
                    file_path=file_path,
                    content=text,
                    message="import from prompts/",
                    author="system",
                    source="startup_import",
                    meta={"repo_path": str(src.relative_to(REPO_ROOT).as_posix())},
                )
        except (IntegrityError, OperationalError):
            # Startup may run more than one importer in quick succession
            # (preflight/API lifespan/worker). If another transaction wins the
            # unique key race or SQLite is briefly locked, leave the existing
            # item intact and continue. Prompt files remain available on disk.
            counts["skipped"] += 1
            continue
        counts["seen"] += 1
        if changed:
            counts["versions"] += 1
    return counts


def current_prompts_root() -> Path:
    return CURRENT_ROOT / "prompts"


def ensure_library_dirs() -> None:
    for p in (LIBRARY_ROOT, CURRENT_ROOT, OLD_ROOT, LOGS_ROOT):
        p.mkdir(parents=True, exist_ok=True)


def _write_current(file_path: str, content: str) -> Path:
    path = _materialized_path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_old_snapshot(item: LibraryItem, version: int, content: str) -> Path:
    path = _old_snapshot_path(item, version)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def materialized_file_path(item: LibraryItem) -> Path:
    return _materialized_path(item.file_path)


def export_item_to_old(item: LibraryItem) -> Path | None:
    current = materialized_file_path(item)
    if not current.is_file():
        return None
    dst = _old_snapshot_path(item, item.active_version)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(current, dst)
    return dst
