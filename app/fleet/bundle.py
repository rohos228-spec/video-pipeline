"""Export/import project folders for fleet montage handoff."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus

MONTAGE_READY_STATUSES = frozenset(
    {
        ProjectStatus.music_ready,
        ProjectStatus.audio_ready,
    }
)


def mark_montage_ready(meta: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(meta or {})
    out["montage_ready"] = True
    return out


def _add_dir_to_tar(tar: tarfile.TarFile, src: Path, arc_prefix: str) -> None:
    if not src.is_dir():
        return
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(src).as_posix()
        tar.add(path, arcname=f"{arc_prefix}/{rel}")


async def export_project_bundle(
    session: AsyncSession, project_id: int
) -> tuple[bytes, str]:
    project = await session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project #{project_id} not found")

    data_dir = project.data_dir
    if not data_dir.is_dir():
        raise ValueError(f"project data dir missing: {data_dir}")

    manifest = {
        "slug": project.slug,
        "topic": project.topic,
        "status": project.status.value
        if hasattr(project.status, "value")
        else str(project.status),
        "meta": project.meta or {},
    }
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode(
            "utf-8"
        )
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        _add_dir_to_tar(tar, data_dir, "data")
    filename = f"{project.slug}-fleet-bundle.tar.gz"
    logger.info("[#{}] fleet bundle export {} bytes", project.id, buf.tell())
    return buf.getvalue(), filename


async def import_project_bundle(
    session: AsyncSession,
    blob: bytes,
    *,
    run_assemble: bool = False,
) -> Project:
    del run_assemble  # queue step sets assembling separately

    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        manifest_member = tar.getmember("manifest.json")
        manifest_file = tar.extractfile(manifest_member)
        if manifest_file is None:
            raise ValueError("bundle missing manifest.json")
        manifest = json.loads(manifest_file.read().decode("utf-8"))

        slug = (manifest.get("slug") or "").strip()
        if not slug:
            raise ValueError("bundle manifest missing slug")

        project = (
            await session.execute(select(Project).where(Project.slug == slug))
        ).scalar_one_or_none()
        if project is None:
            project = Project(
                slug=slug,
                topic=manifest.get("topic") or slug,
                status=ProjectStatus.music_ready,
            )
            session.add(project)
            await session.flush()

        meta = dict(manifest.get("meta") or {})
        meta["fleet_imported"] = True
        project.meta = meta
        if manifest.get("topic"):
            project.topic = manifest.get("topic")
        project.status = ProjectStatus.music_ready

        dest = project.data_dir
        dest.mkdir(parents=True, exist_ok=True)
        for member in tar.getmembers():
            if not member.name.startswith("data/") or member.isdir():
                continue
            rel = member.name.removeprefix("data/")
            if not rel:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            target.write_bytes(extracted.read())

    await session.flush()
    logger.info("[#{}] fleet bundle import slug={}", project.id, project.slug)
    return project
