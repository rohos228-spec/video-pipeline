"""Export/import project folders for fleet montage handoff."""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.project_root import find_project_root

MONTAGE_READY_STATUSES = frozenset(
    {
        ProjectStatus.music_ready,
        ProjectStatus.audio_ready,
    }
)

_BUNDLE_CACHE_DIR = find_project_root() / "data" / "fleet-bundles"


def mark_montage_ready(meta: dict[str, Any] | None) -> dict[str, Any]:
    out = dict(meta or {})
    out["montage_ready"] = True
    return out


def _should_skip_bundle_file(path: Path, *, data_root: Path) -> bool:
    name = path.name
    if name.startswith("~$"):
        return True
    rel = path.relative_to(data_root).as_posix()
    if rel.startswith("old/") and name.endswith(".xlsx"):
        return True
    return False


def _bundle_cache_path(slug: str, ready_at: str) -> Path:
    key = hashlib.sha256(f"{slug}:{ready_at}".encode()).hexdigest()[:16]
    return _BUNDLE_CACHE_DIR / f"{slug}-{key}.tar.gz"


def _write_bundle_file(
    *,
    data_dir: Path,
    manifest: dict[str, Any],
    dest: Path,
) -> None:
    from app.fleet.transfer_state import check_transfer_cancelled, parse_project_id_from_label

    project_id = int(manifest.get("project_id") or 0)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tar.gz.part")
    with tarfile.open(tmp, mode="w:gz") as tar:
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        if data_dir.is_dir():
            for path in sorted(data_dir.rglob("*")):
                if not path.is_file():
                    continue
                if _should_skip_bundle_file(path, data_root=data_dir):
                    continue
                if project_id:
                    check_transfer_cancelled(project_id)
                rel = path.relative_to(data_dir).as_posix()
                tar.add(path, arcname=f"data/{rel}")
    tmp.replace(dest)


def get_or_build_bundle_file(
    *,
    project_id: int,
    slug: str,
    data_dir: Path,
    manifest: dict[str, Any],
    ready_at: str = "",
) -> tuple[Path, str, bool]:
    """Sync: собрать bundle на диск (с кэшем по slug+ready_at)."""
    from app.fleet.transfer_state import check_transfer_cancelled

    check_transfer_cancelled(project_id)
    manifest = dict(manifest)
    manifest["project_id"] = project_id
    filename = f"{slug}-fleet-bundle.tar.gz"
    cache = _bundle_cache_path(slug, ready_at or slug)
    if cache.is_file() and cache.stat().st_size > 0:
        logger.info("[#{}] fleet bundle cache hit {}", project_id, cache.name)
        return cache, filename, True
    logger.info("[#{}] fleet bundle building → {}", project_id, cache.name)
    _write_bundle_file(data_dir=data_dir, manifest=manifest, dest=cache)
    check_transfer_cancelled(project_id)
    return cache, filename, False


def _add_dir_to_tar(tar: tarfile.TarFile, src: Path, arc_prefix: str) -> None:
    if not src.is_dir():
        return
    for path in sorted(src.rglob("*")):
        if not path.is_file():
            continue
        if _should_skip_bundle_file(path, data_root=src):
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
