"""Regression tests for ffmpeg subtitle burn-in on Windows."""

from __future__ import annotations

from app.services.assembly import SUBTITLES_ASS_NAME, subtitles_vf_arg


def test_subtitles_vf_arg_is_bare_filename_without_path_separators() -> None:
    """Windows ffmpeg misparses drive letters in -vf subtitles= paths."""
    vf = subtitles_vf_arg()
    assert vf == f"subtitles={SUBTITLES_ASS_NAME}"
    assert ":" not in vf
    assert "/" not in vf
    assert "\\" not in vf
