"""purge_tmp_gpt must not treat single-pattern entries as character globs."""

from __future__ import annotations

from pathlib import Path

from app.services import chatgpt_xlsx as cx


def test_img_pr_tmp_globs_are_real_tuples() -> None:
    patterns = cx._STEP_TMP_GLOBS["img_pr"]
    assert isinstance(patterns, tuple)
    assert all(isinstance(p, str) and len(p) > 1 for p in patterns)
    assert "*" not in patterns


def test_purge_img_pr_keeps_checkpoint(tmp_path: Path) -> None:
    tmp = tmp_path / "tmp_gpt"
    tmp.mkdir()
    (tmp / "prompt_img_pr_x.txt").write_text("p", encoding="utf-8")
    (tmp / "img_pr_checkpoint.json").write_text("{}", encoding="utf-8")
    (tmp / "db_frames_b01.json").write_text("{}", encoding="utf-8")
    (tmp / "keep_me.txt").write_text("k", encoding="utf-8")

    class _P:
        id = 1
        data_dir = tmp_path

    removed = cx.purge_tmp_gpt_for_step(_P(), "img_pr")  # type: ignore[arg-type]
    assert removed >= 1
    assert (tmp / "img_pr_checkpoint.json").is_file()
    assert (tmp / "keep_me.txt").is_file()
    assert not (tmp / "prompt_img_pr_x.txt").exists()
    assert not (tmp / "db_frames_b01.json").exists()
