"""Correction/montage: не ронять Generate до attach refs; промт как у img-шага."""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.bots import outsee as outsee_mod
from app.services.montage_board_regen import prepare_image_regen


def test_generate_image_no_early_disabled_raise_before_refs() -> None:
    src = inspect.getsource(outsee_mod.OutseeBot._generate_image_on_page)
    # Ранний raise убит — он ломал montage correction до attach reference.
    assert "кнопка Generate заблокирована — промт не принят" not in src
    assert "_attach_reference_images_robust" in src
    # После attach refs идёт поиск Generate + wait enabled (как img-шаг).
    after_attach = src.split("_attach_reference_images_robust", 1)[1]
    assert "кнопка Generate найдена" in after_attach
    assert "_wait_button_enabled" in after_attach


@pytest.mark.asyncio
async def test_correction_mode_prepends_excel_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.models import Project, Frame

    monkeypatch.setattr("app.settings.settings.data_dir", str(tmp_path))
    project = Project(id=47, slug="t", topic="t", hero_mode="auto")
    scenes = project.data_dir / "scenes"
    scenes.mkdir(parents=True)
    current = scenes / "frame_018_oldold01.png"
    current.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)

    fr = Frame(
        id=1,
        project_id=47,
        number=18,
        image_prompt="base hero standing in street, cinematic",
        status="images_ready",
    )
    session = MagicMock()

    async def _get_frame(*_a, **_k):
        return fr

    monkeypatch.setattr(
        "app.services.montage_board_regen._frame_by_number",
        _get_frame,
    )
    monkeypatch.setattr(
        "app.services.montage_board_regen.find_shot1_image",
        lambda *_a, **_k: current,
    )
    monkeypatch.setattr(
        "app.services.montage_board_regen._image_prompt_from_excel",
        lambda *_a, **_k: "base hero standing in street, cinematic",
    )
    monkeypatch.setattr(
        "app.services.montage_board_regen.write_plan_image_prompt",
        lambda *_a, **_k: True,
    )

    prep = await prepare_image_regen(
        session,
        project,
        18,
        shot=1,
        mode="correction",
        correction="замени клонов персонажа с переднего плана",
    )
    assert "base hero standing in street" in prep.prompt_text
    assert "замени клонов" in prep.prompt_text
    assert "CORRECTION" in prep.prompt_text
    assert prep.refs == [current]
