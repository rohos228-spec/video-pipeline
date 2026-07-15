"""Bundle import must not overwrite local prompts."""

from __future__ import annotations

import io
import json
import tarfile

import pytest
from sqlalchemy import select

from app.db import session_scope
from app.fleet.bundle import export_project_bundle, import_project_bundle
from app.models import Project
from app.services.prompt_history import write_prompt_with_history
from app.services.prompt_library import get_prompt_saved_at, read_prompt, write_prompt


@pytest.mark.asyncio
async def test_bundle_import_preserves_local_prompt(tmp_path, monkeypatch) -> None:
    import uuid

    slug = f"bundle-prompt-{uuid.uuid4().hex[:8]}"
    prompts_root = tmp_path / "prompts"
    (prompts_root / "01_plan").mkdir(parents=True)
    monkeypatch.setattr("app.services.prompt_library.PROMPTS_ROOT", prompts_root)

    write_prompt("plan", "draft", "local-v1")
    write_prompt_with_history("plan", "draft", "local-v2")
    saved_before = get_prompt_saved_at("plan", "draft")

    async with session_scope() as session:
        project = Project(slug=slug, topic="t", status="music_ready")
        project.prompt_overrides = {"plan": "draft"}
        project.meta = {"prompt_slot_variants": {"n_plan": {"main": "draft"}}}
        session.add(project)
        await session.flush()
        project.data_dir.mkdir(parents=True, exist_ok=True)
        (project.data_dir / "placeholder.txt").write_text("x", encoding="utf-8")
        await session.commit()
        await session.refresh(project)
        pid = project.id

        blob, _ = await export_project_bundle(session, pid)

    write_prompt_with_history("plan", "draft", "local-v3-after-export")
    saved_mid = get_prompt_saved_at("plan", "draft")

    async with session_scope() as session:
        await import_project_bundle(session, blob, run_assemble=False)
        await session.commit()

    assert read_prompt("plan", "draft") == "local-v3-after-export"
    assert get_prompt_saved_at("plan", "draft") == saved_mid

    async with session_scope() as session:
        row = (
            await session.execute(select(Project).where(Project.slug == slug))
        ).scalar_one()
        assert row.prompt_overrides.get("plan") == "draft"
        assert (row.meta or {}).get("prompt_slot_variants", {}).get("n_plan", {}).get("main") == "draft"
