from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_partial_reference_upload_does_not_submit_paid_request(tmp_path, monkeypatch):
    from app.bots import outsee_http as provider

    host = AsyncMock(side_effect=["https://example.com/bg.png", None])
    submit = AsyncMock()
    monkeypatch.setattr(provider, "ensure_public_image_url", host)
    monkeypatch.setattr(provider, "_post_generate", submit)
    with pytest.raises(provider.OutseeApiError, match="частичный набор запрещён"):
        await provider.generate_image(
            "Image 1 background, Image 2 character", tmp_path / "out.png",
            model_slug="gpt-image-2", reference_images=["https://example.com/a.png", "https://example.com/b.png"],
        )
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_background_code_node_completes_and_advances(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.models import ProjectStatus
    from app.orchestrator.steps import enrich_xlsx as runner
    from app.services import excel_gpt_node, gpt_operator, run_sync, scene_background_refs

    slot = 1
    running, ready, _ = runner._SLOT_MAP[slot]
    project = SimpleNamespace(id=12, meta={}, status=running)
    session = SimpleNamespace(flush=AsyncMock())
    generate = AsyncMock(return_value={"generated": ["scene-a"], "reused": [], "scenes": []})
    complete, advance = AsyncMock(), AsyncMock()
    monkeypatch.setattr(scene_background_refs, "generate_scene_backgrounds", generate)
    monkeypatch.setattr(excel_gpt_node, "upload_dir", lambda *args: tmp_path)
    monkeypatch.setattr(gpt_operator, "save_operator_result", lambda *args, **kwargs: None)
    monkeypatch.setattr(run_sync, "complete_excel_gpt_node_by_key", complete)
    monkeypatch.setattr(runner, "_after_excel_gpt_done", advance)
    await runner._run_fw_backgrounds_node(session, project, node_key="n_cinema_excel_gpt_fw_backgrounds", slot_idx=slot)
    assert project.status is ready
    assert "n_cinema_excel_gpt_fw_backgrounds" in project.meta["excel_gpt_completed_keys"]
    assert (tmp_path / "backgrounds_report.json").exists()
    complete.assert_awaited_once()
    advance.assert_awaited_once()
