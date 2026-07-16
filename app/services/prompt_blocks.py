"""CRUD для prompts/blocks + журнал событий блоков в local library."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import local_library as lib
from app.services.prompt_composer import _block_label, list_block_categories
from app.services.prompt_paths import user_prompt_file

BLOCK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$", re.I)
CATEGORY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$", re.I)

BLOCK_EVENT_TYPES = frozenset(
    {
        "block_created",
        "block_updated",
        "block_discovered",
        "block_selected",
        "block_viewed",
        "block_deleted",
    }
)


def _validate_category(category: str) -> str:
    cat = category.strip()
    if not CATEGORY_RE.match(cat):
        raise ValueError(f"invalid block category: {category}")
    return cat


def _validate_block_id(block_id: str) -> str:
    bid = block_id.strip()
    if not BLOCK_ID_RE.match(bid):
        raise ValueError(f"invalid block id: {block_id}")
    return bid


def block_repo_path(category: str, block_id: str) -> Path:
    cat = _validate_category(category)
    bid = _validate_block_id(block_id)
    return user_prompt_file("blocks", cat, f"{bid}.md")


def block_library_key(category: str, block_id: str) -> str:
    cat = _validate_category(category)
    bid = _validate_block_id(block_id)
    return f"prompts/blocks/{cat}/{bid}.md"


def read_block_file(category: str, block_id: str) -> str:
    path = block_repo_path(category, block_id)
    if not path.is_file():
        raise FileNotFoundError(f"block not found: {category}/{block_id}")
    return path.read_text(encoding="utf-8")


async def log_block_event(
    session: AsyncSession,
    event_type: str,
    *,
    category: str,
    block_id: str,
    item_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "category": category,
        "block_id": block_id,
        "path": block_library_key(category, block_id),
    }
    if extra:
        payload.update(extra)
    item = await lib.get_item(session, item_id) if item_id else None
    if item is None:
        item = await lib.get_item_by_key(session, "block", block_library_key(category, block_id))
    await lib.log_event(session, event_type, item=item, payload=payload)


async def save_block(
    session: AsyncSession,
    category: str,
    block_id: str,
    content: str,
    *,
    message: str | None = None,
    author: str = "studio",
    source: str = "prompt_studio",
    create_if_missing: bool = False,
) -> dict[str, Any]:
    """Записать блок на диск + версия в library."""
    cat = _validate_category(category)
    bid = _validate_block_id(block_id)
    path = block_repo_path(cat, bid)
    existed = path.is_file()
    if not existed and not create_if_missing:
        raise FileNotFoundError(f"block not found: {cat}/{bid}")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    key = block_library_key(cat, bid)
    label = _block_label(content, bid)
    item, version, changed = await lib.create_or_update_item(
        session,
        kind="block",
        key=key,
        title=f"{cat} · {label}",
        file_path=key,
        content=content,
        message=message or ("create block" if not existed else "update block"),
        author=author,
        source=source,
        meta={"category": cat, "block_id": bid, "label": label},
        force_version=True,
    )

    if not existed:
        await log_block_event(
            session,
            "block_created",
            category=cat,
            block_id=bid,
            item_id=item.id,
            extra={"version": version.version, "label": label},
        )
    elif changed:
        await log_block_event(
            session,
            "block_updated",
            category=cat,
            block_id=bid,
            item_id=item.id,
            extra={"version": version.version, "label": label},
        )

    return {
        "category": cat,
        "id": bid,
        "label": label,
        "body": content,
        "version": version.version,
        "created": not existed,
        "changed": changed,
        "library_item_id": item.id,
    }


async def create_block(
    session: AsyncSession,
    category: str,
    block_id: str,
    content: str,
    *,
    message: str | None = None,
    author: str = "studio",
) -> dict[str, Any]:
    path = block_repo_path(category, block_id)
    if path.is_file():
        raise FileExistsError(f"block already exists: {category}/{block_id}")
    return await save_block(
        session,
        category,
        block_id,
        content,
        message=message or "create block from Studio",
        author=author,
        source="prompt_studio_create",
        create_if_missing=True,
    )


async def delete_block(
    session: AsyncSession,
    category: str,
    block_id: str,
    *,
    author: str = "studio",
) -> dict[str, Any]:
    cat = _validate_category(category)
    bid = _validate_block_id(block_id)
    path = block_repo_path(cat, bid)
    if not path.is_file():
        raise FileNotFoundError(f"block not found: {cat}/{bid}")
    path.unlink()
    key = block_library_key(cat, bid)
    item = await lib.get_item_by_key(session, "block", key)
    await log_block_event(
        session,
        "block_deleted",
        category=cat,
        block_id=bid,
        item_id=item.id if item else None,
    )
    return {"category": cat, "id": bid, "deleted": True}


async def rename_block(
    session: AsyncSession,
    category: str,
    block_id: str,
    new_block_id: str,
    *,
    message: str | None = None,
    author: str = "studio",
) -> dict[str, Any]:
    cat = _validate_category(category)
    old_id = _validate_block_id(block_id)
    new_id = _validate_block_id(new_block_id)
    if old_id == new_id:
        raise ValueError("new block id equals current id")
    old_path = block_repo_path(cat, old_id)
    new_path = block_repo_path(cat, new_id)
    if not old_path.is_file():
        raise FileNotFoundError(f"block not found: {cat}/{old_id}")
    if new_path.is_file():
        raise FileExistsError(f"block already exists: {cat}/{new_id}")

    content = old_path.read_text(encoding="utf-8")
    old_path.unlink()
    new_path.write_text(content, encoding="utf-8")

    await log_block_event(
        session,
        "block_deleted",
        category=cat,
        block_id=old_id,
        extra={"renamed_to": new_id},
    )
    result = await save_block(
        session,
        cat,
        new_id,
        content,
        message=message or f"rename {old_id} → {new_id}",
        author=author,
        source="prompt_studio_rename",
        create_if_missing=True,
    )
    result["renamed_from"] = old_id
    return result


async def sync_blocks_catalog(session: AsyncSession) -> dict[str, Any]:
    """Сверка prompts/blocks с library; логирует новые файлы как block_discovered."""
    before = list_block_categories()
    before_keys = {
        block_library_key(cat, name)
        for cat, names in before.items()
        for name in names
    }
    existing_keys = {
        item.key
        for item in await lib.list_items(session, kind="block", limit=5000)
    }

    discovered: list[dict[str, str]] = []
    imported = 0

    for cat, names in before.items():
        for name in names:
            key = block_library_key(cat, name)
            if key in existing_keys:
                continue
            try:
                body = read_block_file(cat, name)
            except FileNotFoundError:
                continue
            item, version, _ = await lib.create_or_update_item(
                session,
                kind="block",
                key=key,
                title=f"{cat} · {_block_label(body, name)}",
                file_path=key,
                content=body,
                message="discovered on disk",
                author="system",
                source="block_catalog_sync",
                meta={"category": cat, "block_id": name},
            )
            await log_block_event(
                session,
                "block_discovered",
                category=cat,
                block_id=name,
                item_id=item.id,
                extra={"version": version.version},
            )
            discovered.append({"category": cat, "block_id": name})
            imported += 1

    after = list_block_categories()
    new_on_disk = sorted(set(block_library_key(c, n) for c, ns in after.items() for n in ns) - before_keys)

    return {
        "categories": len(after),
        "blocks_total": sum(len(v) for v in after.values()),
        "discovered": discovered,
        "discovered_count": len(discovered),
        "imported_to_library": imported,
        "new_on_disk_keys": new_on_disk,
    }


async def list_block_activity(
    session: AsyncSession,
    *,
    limit: int = 100,
    category: str | None = None,
) -> list[dict[str, Any]]:
    events = await lib.list_events(session, limit=limit * 3)
    out: list[dict[str, Any]] = []
    for evt in events:
        if evt.event_type not in BLOCK_EVENT_TYPES and evt.event_type not in {"created", "updated"}:
            continue
        payload = evt.payload or {}
        if evt.event_type in {"created", "updated"} and payload.get("source") not in {
            "prompt_studio",
            "prompt_studio_create",
            "block_catalog_sync",
            "startup_import",
        }:
            key = ""
            if evt.item_id:
                item = await lib.get_item(session, evt.item_id)
                key = item.key if item else ""
            if not key.startswith("prompts/blocks/"):
                continue
            payload = {**(payload or {}), "path": key}
        if category and payload.get("category") != category:
            continue
        out.append(
            {
                "id": evt.id,
                "event_type": evt.event_type,
                "category": payload.get("category"),
                "block_id": payload.get("block_id"),
                "path": payload.get("path"),
                "payload": payload,
                "created_at": evt.created_at.isoformat(),
            }
        )
        if len(out) >= limit:
            break
    return out
