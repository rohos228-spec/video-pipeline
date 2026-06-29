"""Export/import project folders for fleet montage handoff."""

from __future__ import annotations

import io
import json
import os
import shutil
import tarfile
import threading
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
    from datetime import datetime, timezone

    out = dict(meta or {})
    out["montage_ready"] = True
    out["montage_ready_at"] = datetime.now(timezone.utc).isoformat()
    return out


BUNDLE_SKIP_DIR_NAMES = frozenset(
    {"old", "__pycache__", ".git", "tmp", "temp", "node_modules"}
)

_bundle_build_locks: dict[int, threading.Lock] = {}
_bundle_build_locks_guard = threading.Lock()


def _bundle_cache_dir() -> Path:
    from app.settings import settings

    root = Path(settings.data_dir) / "fleet-bundles"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _bundle_cache_path(project_id: int, ready_at: str) -> Path:
    token = ready_at.replace(":", "").replace("+", "p")[:32] or "na"
    return _bundle_cache_dir() / f"p{project_id}-{token}.tar.gz"


def get_or_build_bundle_file(
    *,
    project_id: int,
    slug: str,
    data_dir: Path,
    manifest: dict[str, Any],
    ready_at: str = "",
) -> tuple[Path, str, bool]:
    """Return (path, filename, from_cache). Builds once per montage_ready_at."""
    filename = f"{slug}-fleet-bundle.tar.gz"
    cache_path = _bundle_cache_path(project_id, ready_at)
    if cache_path.is_file() and cache_path.stat().st_size > 0:
        logger.info(
            "[#{}] fleet bundle: serve cached {} bytes ({})",
            project_id,
            cache_path.stat().st_size,
            cache_path.name,
        )
        return cache_path, filename, True

    with _bundle_build_locks_guard:
        lock = _bundle_build_locks.setdefault(project_id, threading.Lock())
    with lock:
        if cache_path.is_file() and cache_path.stat().st_size > 0:
            logger.info("[#{}] fleet bundle: serve cached (after lock)", project_id)
            return cache_path, filename, True

        tmp = cache_path.with_suffix(".tar.gz.part")
        tmp.unlink(missing_ok=True)
        _build_bundle_file_sync(
            data_dir,
            manifest,
            project_id=project_id,
            slug=slug,
            out_path=tmp,
        )
        tmp.replace(cache_path)
        return cache_path, filename, False


def _should_skip_bundle_file(path: Path, *, data_root: Path | None = None) -> bool:
    """Lock/temp/архивные пути не нужны в bundle и часто недоступны для чтения."""
    if data_root is not None:
        try:
            rel = path.relative_to(data_root)
            for part in rel.parts[:-1]:
                if part.lower() in BUNDLE_SKIP_DIR_NAMES:
                    return True
                if part.startswith("~$"):
                    return True
        except ValueError:
            return True

    name = path.name
    if name.startswith("~$"):
        return True
    if name.startswith(".") and name not in {".gitkeep"}:
        return True
    lower = name.lower()
    if lower in {"thumbs.db", "desktop.ini"}:
        return True
    if lower.endswith((".tmp", ".temp", ".lock")):
        return True
    return False


def _add_dir_to_tar(
    tar: tarfile.TarFile, src: Path, arc_prefix: str, *, project_id: int | None = None
) -> None:
    """Пакует data_dir; old/ и lock-файлы Excel не трогаем."""
    from app.fleet.transfer_state import check_transfer_cancelled

    if not src.is_dir():
        return
    added = 0
    for dirpath, dirnames, filenames in os.walk(src, topdown=True):
        dirnames[:] = sorted(
            d
            for d in dirnames
            if d.lower() not in BUNDLE_SKIP_DIR_NAMES and not d.startswith("~$")
        )
        current = Path(dirpath)
        for fname in sorted(filenames):
            if project_id is not None and added % 32 == 0:
                check_transfer_cancelled(project_id)
            path = current / fname
            if _should_skip_bundle_file(path, data_root=src):
                logger.debug("fleet bundle: skip {}", path)
                continue
            rel = path.relative_to(src).as_posix()
            try:
                tar.add(path, arcname=f"{arc_prefix}/{rel}")
                added += 1
            except (PermissionError, OSError) as exc:
                logger.warning("fleet bundle: skip unreadable {} ({})", path, exc)


async def export_project_bundle(
    session: AsyncSession, project_id: int
) -> tuple[bytes, str]:
    """Legacy in-memory export (tests / small bundles)."""
    path, filename = await export_project_bundle_to_file(session, project_id)
    try:
        return path.read_bytes(), filename
    finally:
        path.unlink(missing_ok=True)


def _build_bundle_file_sync(
    data_dir: Path,
    manifest: dict[str, Any],
    *,
    project_id: int,
    slug: str,
    out_path: Path,
) -> int:
    """CPU/disk-heavy tar build — no DB session."""
    if not data_dir.is_dir():
        raise ValueError(f"project data dir missing: {data_dir}")
    logger.info(
        "[#{}] ▶ fleet bundle BUILD START (упаковка data/, 5–15 мин для больших проектов)",
        project_id,
    )
    from app.fleet.transfer_state import check_transfer_cancelled, emit_fleet_transfer_sync

    check_transfer_cancelled(project_id)
    emit_fleet_transfer_sync(
        project_id,
        phase="packing",
        direction="to_hub",
        percent=5,
        message="Упаковка файлов проекта…",
        slug=slug,
    )
    with tarfile.open(out_path, mode="w:gz") as tar:
        manifest_bytes = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        tar.addfile(info, io.BytesIO(manifest_bytes))
        _add_dir_to_tar(tar, data_dir, "data", project_id=project_id)
    check_transfer_cancelled(project_id)
    size = out_path.stat().st_size
    logger.info(
        "[#{}] ✓ fleet bundle BUILD DONE {:.0f} MB → {}",
        project_id,
        size / (1024 * 1024),
        out_path.name,
    )
    emit_fleet_transfer_sync(
        project_id,
        phase="packing",
        direction="to_hub",
        percent=100,
        total_mb=size / (1024 * 1024),
        sent_mb=size / (1024 * 1024),
        message=f"Bundle упакован ({size / (1024 * 1024):.0f} MB)",
        slug=slug,
    )
    return size


async def export_project_bundle_to_file(
    session: AsyncSession, project_id: int
) -> tuple[Path, str]:
    import asyncio
    import tempfile

    project = await session.get(Project, project_id)
    if project is None:
        raise ValueError(f"project #{project_id} not found")

    data_dir = project.data_dir.resolve()
    manifest = {
        "slug": project.slug,
        "topic": project.topic,
        "status": project.status.value
        if hasattr(project.status, "value")
        else str(project.status),
        "meta": dict(project.meta or {}),
    }
    slug = project.slug
    pid = project.id

    fd, tmp_name = tempfile.mkstemp(suffix=".tar.gz", prefix="fleet-bundle-")
    os.close(fd)
    out_path = Path(tmp_name)
    try:
        await asyncio.to_thread(
            _build_bundle_file_sync,
            data_dir,
            manifest,
            project_id=pid,
            slug=slug,
            out_path=out_path,
        )
        return out_path, f"{slug}-fleet-bundle.tar.gz"
    except Exception:
        out_path.unlink(missing_ok=True)
        raise


async def import_project_bundle(
    session: AsyncSession,
    blob: bytes,
    *,
    run_assemble: bool = False,
) -> Project:
    return await import_project_bundle_file(
        session, io.BytesIO(blob), run_assemble=run_assemble
    )


async def import_project_bundle_file(
    session: AsyncSession,
    source: io.IOBase | Path,
    *,
    run_assemble: bool = False,
) -> Project:
    del run_assemble  # queue step sets assembling separately

    if isinstance(source, Path):
        tf = tarfile.open(source, mode="r:gz")
    else:
        tf = tarfile.open(fileobj=source, mode="r:gz")

    with tf:
        manifest_member = tf.getmember("manifest.json")
        manifest_file = tf.extractfile(manifest_member)
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
        for member in tf.getmembers():
            if not member.name.startswith("data/") or member.isdir():
                continue
            rel = member.name.removeprefix("data/")
            if not rel:
                continue
            target = dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            extracted = tf.extractfile(member)
            if extracted is None:
                continue
            target.write_bytes(extracted.read())

    await session.flush()
    logger.info("[#{}] fleet bundle import slug={}", project.id, project.slug)
    await finalize_fleet_bundle_import(session, project)
    return project


async def finalize_fleet_bundle_import(
    session: AsyncSession, project: Project
) -> None:
    """После распаковки data/: синхрон xlsx→БД и артефакты с диска для сборки."""
    from app.services.artifact_recovery import recover_before_assemble
    from app.services.chatgpt_xlsx import sync_project_xlsx

    xlsx = project.data_dir / "project.xlsx"
    if xlsx.is_file():
        try:
            info = await sync_project_xlsx(session, project, xlsx)
            logger.info("[#{}] fleet bundle: sync xlsx → {}", project.id, info)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[#{}] fleet bundle: sync xlsx failed: {}", project.id, exc)
    await recover_before_assemble(session, project)
    await session.flush()
