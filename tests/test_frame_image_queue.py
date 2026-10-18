"""Очередь generate_images: диск важнее статуса в БД."""

from __future__ import annotations

from pathlib import Path

from app.models import Frame, FrameStatus
from app.services.scan_frames import frame_needs_shot1_image, is_valid_scene_image


def _frame(n: int, status: FrameStatus, prompt: str = "p") -> Frame:
    fr = Frame(project_id=1, number=n, voiceover_text="v")
    fr.status = status
    fr.image_prompt = prompt
    return fr


def test_image_generated_without_file_still_needs_outsee(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    fr = _frame(3, FrameStatus.image_generated)
    assert frame_needs_shot1_image(fr, scenes) is True


def test_valid_png_on_disk_skips_generation(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = scenes / "frame_003_abcd1234.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
    assert is_valid_scene_image(png)
    fr = _frame(3, FrameStatus.image_prompt_ready)
    assert frame_needs_shot1_image(fr, scenes) is False


def test_shot_child_without_png_needs_own_image(tmp_path: Path) -> None:
    """K2/K3 — отдельные кадры со своим куском закадра, не shot2 родителя."""
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    fr = _frame(2, FrameStatus.image_prompt_ready)
    fr.attrs = {"camera_subdivide": {"role": "shot", "parent_uuid": "abc"}}
    assert frame_needs_shot1_image(fr, scenes) is True


def test_shot_child_empty_prompt_borrows_parent(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    parent = _frame(1, FrameStatus.image_prompt_ready, prompt="PARENT STILL")
    parent.uuid = "a" * 24
    parent.attrs = {"camera_subdivide": {"role": "vo_parent"}}
    child = _frame(2, FrameStatus.image_prompt_ready, prompt="")
    child.uuid = "b" * 24
    child.attrs = {"camera_subdivide": {"role": "shot", "parent_uuid": "a" * 24}}
    assert frame_needs_shot1_image(child, scenes) is False
    assert frame_needs_shot1_image(child, scenes, [parent, child]) is True


def test_shot_child_with_png_skips_generation(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = scenes / "frame_002_abcd1234.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
    fr = _frame(2, FrameStatus.image_prompt_ready)
    fr.attrs = {"camera_subdivide": {"role": "shot", "parent_uuid": "abc"}}
    assert frame_needs_shot1_image(fr, scenes) is False


def test_tiny_png_still_needs_generation(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    png = scenes / "frame_003_abcd1234.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 50_000)
    fr = _frame(3, FrameStatus.image_generated)
    assert frame_needs_shot1_image(fr, scenes) is True


def test_parallel_image_job_opens_project_db_not_master() -> None:
    import inspect

    from app.orchestrator.steps.generate_images import _generate_frame_job

    src = inspect.getsource(_generate_frame_job)
    assert "project_db_session_scope" in src
    assert "SessionLocal" not in src


def test_requeue_failed_frames_without_png(tmp_path: Path) -> None:
    from app.orchestrator.steps.generate_images import requeue_failed_frames_without_png

    scenes = tmp_path / "scenes"
    scenes.mkdir()
    failed = _frame(4, FrameStatus.failed)
    failed.attrs = {"fail_reason": "download"}
    ok = _frame(5, FrameStatus.failed)
    png = scenes / "frame_005_abcd1234.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
    n = requeue_failed_frames_without_png([failed, ok], scenes, project_id=63)
    assert n == 1
    assert failed.status is FrameStatus.image_prompt_ready
    assert "fail_reason" not in (failed.attrs or {})
    assert ok.status is FrameStatus.failed
    assert frame_needs_shot1_image(failed, scenes) is True
    assert frame_needs_shot1_image(ok, scenes) is False
