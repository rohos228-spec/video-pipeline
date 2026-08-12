"""Запись результата scene_design через существующий контракт apply-ops."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project


def _character_cards(raw: Any) -> list[dict[str, Any]]:
    return [
        c
        for c in (raw or [])
        if isinstance(c, dict) and str(c.get("id") or c.get("code") or "").strip()
    ]


def characters_from_payload_or_checkpoint(
    project: Project, payload: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Реестр персонажей: payload сборки, иначе checkpoint characters.json.

    chrono_dyn часто пишет паспорт в ``scene_design/characters.json``, но
    staging-ячейки ``kind=character`` пустые (only_agent assemble не
    вызывает store_cells). Тогда local assemble отдаёт characters=[] и
    Entity не создаются — hero не из чего генерить.
    """
    chars = _character_cards((payload or {}).get("characters"))
    if chars:
        return chars
    from app.services.scene_design.runner import load_checkpoint

    ckpt = load_checkpoint(project, "characters")
    if isinstance(ckpt, dict):
        chars = _character_cards(ckpt.get("characters"))
        if chars:
            return chars
    path = project.data_dir / "scene_design" / "characters.json"
    if not path.is_file():
        return []
    import json

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, dict):
        return []
    return _character_cards(data.get("characters"))


async def apply_scene_design(
    session: AsyncSession,
    project: Project,
    payload: dict[str, Any],
) -> dict:
    """characters → Entity, scenes → meta.scene_registry, ops → поля кадров.

    Полностью переиспользует ``db_apply.apply_ops`` — тот же контракт,
    что у scene_grammar_unified_agent (excel_gpt legacy-путь).
    """
    from app.services import db_apply

    characters = characters_from_payload_or_checkpoint(project, payload)
    if characters and not payload.get("characters"):
        payload["characters"] = characters
    return await db_apply.apply_ops(
        session,
        project,
        list(payload.get("ops") or []),
        characters=characters or None,
        scenes=list(payload.get("scenes") or []) or None,
        export_xlsx=True,
    )
