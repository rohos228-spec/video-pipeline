"""Fail-dump сырого GPT при ошибке парса scene_design."""

from __future__ import annotations

from pathlib import Path

from app.services.scene_design.runner import _dump_agent_fail


def test_dump_agent_fail_writes_scene_design_and_tmp_gpt(tmp_path: Path) -> None:
    reply = '{"not": "valid skeleton"}\n' + ("x" * 100)
    err = RuntimeError("нет JSON (len=120)")

    class _P:
        id = 99
        data_dir = tmp_path

    path = _dump_agent_fail(_P(), "skeleton", reply, err)  # type: ignore[arg-type]
    assert path is not None
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "нет JSON" in text
    assert "valid skeleton" in text
    tmp_files = list((tmp_path / "tmp_gpt").glob("sd_skeleton_fail_*.txt"))
    assert tmp_files
    assert "valid skeleton" in tmp_files[0].read_text(encoding="utf-8")
