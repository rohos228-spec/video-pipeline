"""Поиск и восстановление исходного закадрового текста (до GPT-переписывания).

Источники (по приоритету «самый ранний = исходный»):
  1. Самый старый бэкап data/.../old/*_voiceover.txt
  2. voiceover.txt / script_text у самого раннего дочернего проекта
  3. Текущий voiceover.txt родителя (если бэкапов нет — файл не перезаписывался)
  4. project.script_text в БД
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.services.mass_factory import list_mass_children, mass_parent_id

_BACKUP_RE = re.compile(r"^(\d{8}_\d{6})_voiceover\.txt$")
_VOICEOVER_BACKUP_GLOB = "*_voiceover.txt"


@dataclass(frozen=True)
class VoiceoverCandidate:
    text: str
    source: str
    priority: int


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").strip()


def backup_sort_key(path: Path) -> tuple[str, str]:
    """Ключ сортировки бэкапов: по метке времени в имени, затем по имени."""
    m = _BACKUP_RE.match(path.name)
    if m:
        return m.group(1), path.name
    return path.name, path.name


def list_voiceover_backups(project: Project) -> list[Path]:
    old_dir = project.data_dir / "old"
    if not old_dir.is_dir():
        return []
    backups = [
        p
        for p in old_dir.glob(_VOICEOVER_BACKUP_GLOB)
        if p.is_file() and p.stat().st_size > 0
    ]
    return sorted(backups, key=backup_sort_key)


def oldest_voiceover_backup(project: Project) -> Path | None:
    backups = list_voiceover_backups(project)
    return backups[0] if backups else None


def _candidate_from_file(path: Path, source: str, priority: int) -> VoiceoverCandidate | None:
    if not path.is_file() or path.stat().st_size == 0:
        return None
    text = _read_text(path)
    if not text:
        return None
    return VoiceoverCandidate(text=text, source=source, priority=priority)


def _candidate_from_text(text: str, source: str, priority: int) -> VoiceoverCandidate | None:
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    return VoiceoverCandidate(text=cleaned, source=source, priority=priority)


async def discover_original_candidates(
    session: AsyncSession | None,
    project: Project,
) -> list[VoiceoverCandidate]:
    """Все кандидаты на «исходный» voiceover для проекта."""
    out: list[VoiceoverCandidate] = []

    oldest = oldest_voiceover_backup(project)
    if oldest is not None:
        c = _candidate_from_file(oldest, f"old/{oldest.name}", priority=0)
        if c:
            out.append(c)

    if session is not None:
        children = await list_mass_children(session, project.id)
        children.sort(key=lambda p: p.id)
        for child in children:
            vo = child.data_dir / "voiceover.txt"
            c = _candidate_from_file(
                vo,
                f"child#{child.id}/voiceover.txt",
                priority=10 + child.id,
            )
            if c:
                out.append(c)
            c2 = _candidate_from_text(
                child.script_text or "",
                f"child#{child.id}/script_text",
                priority=20 + child.id,
            )
            if c2:
                out.append(c2)

    has_backups = oldest is not None
    if not has_backups:
        vo = project.data_dir / "voiceover.txt"
        c = _candidate_from_file(vo, "voiceover.txt", priority=100)
        if c:
            out.append(c)
        c2 = _candidate_from_text(
            project.script_text or "",
            "script_text",
            priority=110,
        )
        if c2:
            out.append(c2)

    return out


async def find_original_voiceover(
    session: AsyncSession | None,
    project: Project,
) -> VoiceoverCandidate | None:
    candidates = await discover_original_candidates(session, project)
    if not candidates:
        return None
    return min(candidates, key=lambda c: c.priority)


def is_parent_project(project: Project) -> bool:
    return mass_parent_id(project) is None


def _child_project_result(project: Project) -> dict[str, Any]:
    return {
        "project_id": project.id,
        "slug": project.slug,
        "restored": False,
        "reason": "child_project_skipped",
        "mass_parent_id": mass_parent_id(project),
    }


async def restore_original_voiceover(
    session: AsyncSession,
    project: Project,
    *,
    dry_run: bool = False,
    force: bool = False,
    parents_only: bool = True,
) -> dict[str, Any]:
    """Записать исходный voiceover в voiceover.txt + project.script_text.

    По умолчанию работает только для родительских проектов (без mass_parent_id).
    """
    if parents_only and not is_parent_project(project):
        return _child_project_result(project)

    from app.services.chatgpt_xlsx import save_voiceover_text

    candidate = await find_original_voiceover(session, project)
    voiceover_path = project.data_dir / "voiceover.txt"
    current = _read_text(voiceover_path) if voiceover_path.is_file() else ""
    current_db = (project.script_text or "").strip()

    if candidate is None:
        return {
            "project_id": project.id,
            "slug": project.slug,
            "restored": False,
            "reason": "no_original_found",
            "current_chars": len(current),
            "current_db_chars": len(current_db),
        }

    same_as_disk = current == candidate.text
    same_as_db = current_db == candidate.text
    if same_as_disk and same_as_db and not force:
        return {
            "project_id": project.id,
            "slug": project.slug,
            "restored": False,
            "reason": "already_original",
            "source": candidate.source,
            "chars": len(candidate.text),
        }

    if dry_run:
        return {
            "project_id": project.id,
            "slug": project.slug,
            "restored": False,
            "dry_run": True,
            "would_restore": True,
            "source": candidate.source,
            "chars": len(candidate.text),
            "current_chars": len(current),
            "current_db_chars": len(current_db),
        }

    save_voiceover_text(project, voiceover_path, candidate.text)
    project.script_text = candidate.text
    await session.flush()
    logger.info(
        "[#{}] restore_original_voiceover: {} симв из {}",
        project.id,
        len(candidate.text),
        candidate.source,
    )
    return {
        "project_id": project.id,
        "slug": project.slug,
        "restored": True,
        "source": candidate.source,
        "chars": len(candidate.text),
        "previous_chars": len(current),
    }


async def restore_all_parent_voiceovers(
    session: AsyncSession,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    """Восстановить исходный voiceover у всех родительских проектов."""
    rows = (await session.execute(select(Project).order_by(Project.id.asc()))).scalars().all()
    parents = [p for p in rows if mass_parent_id(p) is None]
    results: list[dict[str, Any]] = []
    restored = 0
    skipped = 0
    missing = 0
    for p in parents:
        r = await restore_original_voiceover(session, p, dry_run=dry_run, force=force)
        results.append(r)
        if r.get("restored"):
            restored += 1
        elif r.get("reason") == "no_original_found":
            missing += 1
        else:
            skipped += 1
    if not dry_run and restored:
        await session.commit()
    return {
        "parents_total": len(parents),
        "restored": restored,
        "skipped": skipped,
        "missing": missing,
        "dry_run": dry_run,
        "results": results,
    }


async def count_parent_projects(session: AsyncSession) -> int:
    parent_expr = cast(func.json_extract(Project.meta, "$.mass_parent_id"), Integer)
    total = (
        await session.execute(
            select(func.count()).select_from(Project).where(parent_expr.is_(None))
        )
    ).scalar_one()
    return int(total or 0)
