"""Ручной ▶ excel_gpt не должен skip-filled: GPT обязан пойти."""

from __future__ import annotations

from types import SimpleNamespace

from app.orchestrator.steps.enrich_xlsx import (
    _all_frame_prompts_ready,
    _clear_excel_gpt_ui_force_full,
    _excel_gpt_ui_force_full,
    _select_fw_frames_for_gpt,
    _should_skip_qc_gpt,
)


def _filled_frame(i: int, *, camera: bool = True) -> SimpleNamespace:
    cs = (
        {"крупность": "Средний план", "движение": "статика", "набор": "SET_01"}
        if camera
        else {}
    )
    return SimpleNamespace(
        uuid=f"u{i}",
        image_prompt=f"img-{i}",
        animation_prompt=f"mov-{i}",
        attrs={"shot01_action": f"действие {i}", "camera_subdivide": cs},
    )


def test_fw_frames_force_full_includes_shot_children() -> None:
    parent = _filled_frame(1)
    child = SimpleNamespace(
        uuid="u-child",
        image_prompt="",
        animation_prompt="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": "u1",
                "shot_index": 2,
            }
        },
    )
    gpt, camera_only = _select_fw_frames_for_gpt([parent, child], force_full=True)
    assert [fr.uuid for fr in gpt] == ["u1", "u-child"]
    assert camera_only is False


def test_ui_force_full_sends_all_filled_fw_frames() -> None:
    frames = [_filled_frame(1), _filled_frame(2)]
    gpt, camera_only = _select_fw_frames_for_gpt(frames, force_full=True)
    assert [fr.uuid for fr in gpt] == ["u1", "u2"]
    assert camera_only is False


def test_without_force_full_skips_filled_fw_frames() -> None:
    frames = [_filled_frame(1), _filled_frame(2)]
    gpt, camera_only = _select_fw_frames_for_gpt(frames, force_full=False)
    assert gpt == []
    assert camera_only is False


def test_without_force_full_camera_only_when_prompts_ready() -> None:
    frames = [_filled_frame(1, camera=False), _filled_frame(2, camera=False)]
    gpt, camera_only = _select_fw_frames_for_gpt(frames, force_full=False)
    assert [fr.uuid for fr in gpt] == ["u1", "u2"]
    assert camera_only is True


def test_qc_skip_disabled_on_ui_force_full() -> None:
    frames = [_filled_frame(1), _filled_frame(2)]
    assert _all_frame_prompts_ready(frames)
    assert _should_skip_qc_gpt(frames, force_full=False) is True
    assert _should_skip_qc_gpt(frames, force_full=True) is False


def test_excel_gpt_ui_force_full_flag_roundtrip() -> None:
    project = SimpleNamespace(meta={})
    assert _excel_gpt_ui_force_full(project) is False
    project.meta = {
        "excel_gpt_ui_force_full": True,
        "excel_gpt_force_full_rerun": "n_excel_gpt_fw_frames",
    }
    assert _excel_gpt_ui_force_full(project) is True
    assert _clear_excel_gpt_ui_force_full(project) is True
    assert _excel_gpt_ui_force_full(project) is False
    assert "excel_gpt_force_full_rerun" not in project.meta
