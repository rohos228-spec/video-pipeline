"""Совместимость: hero check→regen→check делегирует в vision_check_loop."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services import vision_check_loop as vcl

# Re-export meta keys / helpers used by generate_hero and tests.
META_IDS = vcl.META_HERO_IDS
META_ROUND = vcl.META_HERO_ROUND
META_RETURN = vcl.META_HERO_RETURN
MAX_HERO_CHECK_REGEN_ROUNDS = vcl.MAX_VISION_CHECK_ROUNDS


def get_hero_check_regen_ids(project: Project) -> list[str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get(META_IDS) or []
    if not isinstance(raw, list):
        return []
    from app.services.check_analysis import normalize_hero_excel_id

    out: list[str] = []
    seen: set[str] = set()
    for x in raw:
        nid = normalize_hero_excel_id(str(x or ""))
        if nid and nid not in seen:
            seen.add(nid)
            out.append(nid)
    return out


def hero_check_loop_active(project: Project) -> bool:
    return vcl.vision_check_loop_active(project)


def clear_hero_check_regen_meta(project: Project) -> None:
    vcl.clear_vision_check_meta(project)


async def maybe_start_hero_check_regen_after_check(
    session: AsyncSession,
    project: Project,
    node_key: str | None,
) -> bool:
    """После checkMode: hero или scenes → vision_check_loop."""
    return await vcl.maybe_start_vision_check_loop_after_check(
        session, project, node_key
    )


async def maybe_return_to_check_after_hero(
    session: AsyncSession,
    project: Project,
) -> ProjectStatus | None:
    return await vcl.maybe_return_to_check_after_vision_ready(session, project)


def filter_chars_for_hero_check_regen(
    chars: list[Any],
    project: Project,
) -> tuple[list[Any], set[str] | None]:
    """Если заданы regen ids — вернуть только их; иначе (chars, None)."""
    ids = get_hero_check_regen_ids(project)
    if not ids:
        return chars, None
    id_set = set(ids)
    filtered = [ch for ch in chars if getattr(ch, "id", None) in id_set]
    return filtered, id_set
