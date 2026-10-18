"""Ревью #352: «да»/«true»/«1» в удалён и карантин скрывают шот."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.models import FrameStatus
from app.orchestrator.steps.generate_images import frame_hidden_from_paid_generation
from app.orchestrator.steps.generate_videos import _skip_frame_video_generation
from app.orchestrator.validators.activity_flags import (
    ACTIVE_MARK,
    HIDE_MARKS,
    frame_is_active,
    shot_is_active,
)
from app.services.animation_prompt_gpt import collect_batch_items
from app.services.db_apply import merge_kadry_keep_missing
from app.services.montage_board_frames import _rewrite_vo_cell
from app.services.shot_templates import clear_shot_templates_cache, fill_kadry_from_catalog

_HIDE_TRUE_VALUES = (
    "да",
    "Да",
    "true",
    "True",
    "1",
    1,
    True,
    "yes",
    "on",
    "истина",
    " да ",
)


@pytest.mark.parametrize("kind", ("shot", "frame"))
@pytest.mark.parametrize("mark", HIDE_MARKS)
@pytest.mark.parametrize("value", _HIDE_TRUE_VALUES, ids=lambda value: repr(value))
def test_hide_mark_affirmative_is_inactive(kind: str, mark: str, value: object) -> None:
    """«да»/«true»/«1» в удалён и карантин не отправляют шот в генерацию."""

    payload = {mark: value}
    active = shot_is_active(payload) if kind == "shot" else frame_is_active(payload)
    assert active is False


@pytest.mark.parametrize("kind", ("shot", "frame"))
def test_active_mark_zero_string_matches_number(kind: str) -> None:
    """Blob 56a6e8cf: активен=\"0.0\" живой, активен=0.0 неактивен."""

    text = {ACTIVE_MARK: "0.0"}
    number = {ACTIVE_MARK: 0.0}
    if kind == "shot":
        assert shot_is_active(text) is True
        assert shot_is_active(number) is False
    else:
        assert frame_is_active(text) is True
        assert frame_is_active(number) is False


def test_merge_keeps_da_deleted_out_of_paid_generation(tmp_path: Path) -> None:
    merged = merge_kadry_keep_missing(
        [{"id": "K1", "удалён": "да", "закадр": "секрет", "план": "старый"}],
        [{"id": "K1", "план": "новый", "активен": True, "удалён": "нет"}],
    )
    kept = next(row for row in merged if row.get("id") == "K1")
    assert kept["удалён"] == "да"
    assert kept["план"] == "новый"
    assert shot_is_active(kept) is False

    clear_shot_templates_cache()
    catalog = fill_kadry_from_catalog(
        [
            {
                "id": "1-S1-K2",
                "сцена": 1,
                "шаблон": "T6",
                "удалён": "да",
                "закадр": "Он выходит со двора",
                "действие": "Герой выходит со двора и смотрит на дорогу",
            }
        ],
        "1. двор — герой выходит и идёт к воротам\n"
        "(Он выходит со двора и идёт к воротам.)\n",
        cell_number=1,
    )
    hidden_shot = next(row for row in catalog if row.get("id") == "1-S1-K2")
    assert hidden_shot["удалён"] == "да"
    assert shot_is_active(hidden_shot) is False

    parent = SimpleNamespace(
        number=1,
        voiceover_text="новый текст",
        attrs={
            "кадры": [
                {"id": "1-K1", "удалён": "да", "закадр": "секрет", "действие": "старое"}
            ]
        },
    )
    member = SimpleNamespace(
        number=1,
        voiceover_text="новый текст",
        attrs={"действие": "новое", "место": "двор"},
    )
    _rewrite_vo_cell(parent, [member])
    ladder = parent.attrs["кадры"]
    board_shot = next(row for row in ladder if row.get("id") == "1-K1")
    assert board_shot["удалён"] == "да"
    assert shot_is_active(board_shot) is False

    project = SimpleNamespace(id=7, data_dir=tmp_path)
    hidden = SimpleNamespace(
        number=2,
        image_prompt="герой стоит у двери на общем плане",
        animation_prompt="",
        voiceover_text="скрытый кадр",
        status=FrameStatus.planned,
        attrs={"удалён": "да"},
    )
    live = SimpleNamespace(
        number=3,
        image_prompt="герой стоит у двери на общем плане",
        animation_prompt="",
        voiceover_text="живой кадр",
        status=FrameStatus.planned,
        attrs={},
    )
    items = collect_batch_items(project, [hidden, live])
    assert [item.frame.number for item in items] == [3]
    assert frame_hidden_from_paid_generation(hidden) is True
    assert frame_is_active(hidden.attrs) is False
    assert _skip_frame_video_generation(hidden, False) is True
    assert _skip_frame_video_generation(live, False) is False
