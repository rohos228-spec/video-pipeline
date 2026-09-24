"""Сверка персонажей сцены с реестром Entity (type=character)."""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity
from app.services.scene_design.assembler import _fold_ru

_CODE_RE = re.compile(r"c\d{2}", re.IGNORECASE)


def _aliases(ent: Entity) -> list[str]:
    names: list[str] = []
    name = str(getattr(ent, "name", "") or "").strip()
    if name:
        names.append(name)
    attrs = getattr(ent, "attrs", None) or {}
    if isinstance(attrs, dict):
        for key in ("имя", "name", "aliases", "алиасы", "роль", "role"):
            raw = attrs.get(key)
            if isinstance(raw, str) and raw.strip():
                names.append(raw.strip())
            elif isinstance(raw, list):
                names.extend(str(x).strip() for x in raw if str(x).strip())
    code = str(getattr(ent, "code", "") or "").strip()
    if code:
        names.append(code)
    out: list[str] = []
    seen: set[str] = set()
    for item in names:
        folded = _fold_ru(item)
        if folded and folded not in seen:
            seen.add(folded)
            out.append(item)
    first = _fold_ru(name).split()[:1]
    if first and len(first[0]) >= 3 and first[0] not in seen:
        out.append(name.split()[0])
    return out


def registry_rows(entities: list[Entity]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for ent in entities:
        if str(getattr(ent, "type", "") or "") != "character":
            continue
        code = str(getattr(ent, "code", "") or "").strip().lower()
        name = str(getattr(ent, "name", "") or "").strip()
        if not code and not name:
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "aliases": " | ".join(_aliases(ent)),
            }
        )
    return rows


def match_registry_characters(text: str, entities: list[Entity]) -> list[dict[str, str]]:
    """Кто из реестра назван в закадре/заказе. Нет в тексте — не подставляем."""
    folded = _fold_ru(text)
    if not folded:
        return []
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in _CODE_RE.findall(text or ""):
        code = m.lower()
        if code in seen:
            continue
        ent = next(
            (
                e
                for e in entities
                if str(getattr(e, "type", "") or "") == "character"
                and str(getattr(e, "code", "") or "").strip().lower() == code
            ),
            None,
        )
        if ent is None:
            continue
        seen.add(code)
        found.append(
            {
                "code": code,
                "name": str(getattr(ent, "name", "") or "").strip(),
            }
        )
    for ent in entities:
        if str(getattr(ent, "type", "") or "") != "character":
            continue
        code = str(getattr(ent, "code", "") or "").strip().lower()
        if not code or code in seen:
            continue
        needles = [_fold_ru(a) for a in _aliases(ent)]
        needles = [n for n in needles if len(n) >= 3]
        hit = False
        for needle in sorted(needles, key=len, reverse=True):
            if re.search(
                rf"(?<![0-9a-zа-яё]){re.escape(needle)}(?![0-9a-zа-яё])",
                folded,
            ):
                hit = True
                break
        if hit:
            seen.add(code)
            found.append(
                {
                    "code": code,
                    "name": str(getattr(ent, "name", "") or "").strip(),
                }
            )
    return found


def format_character_codes(rows: list[dict[str, str]]) -> str:
    return ", ".join(r["code"] for r in rows if r.get("code"))


def format_character_labels(rows: list[dict[str, str]]) -> str:
    parts: list[str] = []
    for row in rows:
        code = row.get("code") or ""
        name = row.get("name") or ""
        if code and name:
            parts.append(f"{code} · {name}")
        elif code:
            parts.append(code)
        elif name:
            parts.append(name)
    return ", ".join(parts)


async def load_character_registry(
    session: AsyncSession, project_id: int
) -> list[Entity]:
    return list(
        (
            await session.execute(
                select(Entity)
                .where(
                    Entity.project_id == int(project_id),
                    Entity.type == "character",
                )
                .order_by(Entity.sort_key, Entity.id)
            )
        )
        .scalars()
        .all()
    )


def intersect_codes_with_registry(
    raw: str, entities: list[Entity]
) -> list[dict[str, str]]:
    """Коды из GPT/паспорта оставляем только если они есть в реестре."""
    wanted = {m.lower() for m in _CODE_RE.findall(raw or "")}
    if not wanted:
        return match_registry_characters(raw, entities)
    out: list[dict[str, str]] = []
    for ent in entities:
        code = str(getattr(ent, "code", "") or "").strip().lower()
        if code in wanted:
            out.append(
                {
                    "code": code,
                    "name": str(getattr(ent, "name", "") or "").strip(),
                }
            )
    return out
