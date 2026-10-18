"""Промт K2/K3: сцена с референса + крупность/ракурс + раскладка площадки."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.vo_shot_expand import (
    compose_coverage_child_prompt,
    effective_image_prompt,
    write_coverage_child_prompts,
)


_LAYOUT = (
    "СТАРТ (первый кадр, до действия): папка закрыта на столе, руки ещё не трогают обложку. "
    "План СРЕДНИЙ. Зона «архивный стол»: стол с папкой, портретом и служебной книгой. "
    "Камера с юга, смотрит на север. Предметы на старте: "
    "Справа: служебная книга. "
    "В центре: папка Сергея Ткача — стол с папкой, портретом и служебной книгой "
    "(лежит на столе). Архивист — у южной стороны, у камеры, спиной или плечом "
    "в кадре, спиной к камере. "
    "КОНЕЦ (последний кадр действия): папка раскрыта. "
    "В кадре меняется: папка Сергея Ткача → открыта. "
    "В референсе: план ОБЩИЙ, камера с юга, смотрит на север, ракурс фронт. "
    "Смена: план СРЕДНИЙ, камера с востока, смотрит на запад, ракурс 3/4. "
    "Угол камеры относительно референса ≥30°."
)


def _parent() -> SimpleNamespace:
    return SimpleNamespace(
        number=1,
        uuid="p" * 24,
        voiceover_text="архив",
        image_prompt="PARENT STYLE LOCK still " * 20,
        attrs={"camera_subdivide": {"coverage_kind": "parent", "shot_id": "1-S1-K1"}},
    )


def _child() -> SimpleNamespace:
    return SimpleNamespace(
        number=2,
        uuid="c" * 24,
        voiceover_text="",
        image_prompt="",
        attrs={
            "раскладка": _LAYOUT,
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": "p" * 24,
                "shot_id": "1-S1-K2",
                "крупность": "СРЕДНИЙ",
                "ракурс": "уровень глаз, нейтральный, фронт",
            },
        },
    )


def test_compose_coverage_child_prompt_matches_layout_formula() -> None:
    text = compose_coverage_child_prompt(_child(), _parent())
    assert text.startswith("сцена с референса (кадр 1), крупность СРЕДНИЙ РАКУРС ")
    assert "СТАРТ (первый кадр, до действия): папка закрыта на столе" in text
    assert "В кадре меняется: папка Сергея Ткача → открыта." in text
    assert "PARENT STYLE" not in text


def test_write_coverage_child_prompts_fills_empty_shot_not_parent() -> None:
    parent = _parent()
    child = _child()
    n = write_coverage_child_prompts([parent, child])
    assert n == 1
    assert child.image_prompt.startswith("сцена с референса (кадр 1)")
    assert parent.image_prompt.startswith("PARENT STYLE")


def test_effective_image_prompt_uses_written_child_not_parent_style() -> None:
    parent = _parent()
    child = _child()
    write_coverage_child_prompts([parent, child])
    got = effective_image_prompt(child, [parent, child])
    assert got.startswith("сцена с референса (кадр 1)")
    assert "PARENT STYLE" not in got


def test_write_does_not_clobber_existing_child_prompt() -> None:
    parent = _parent()
    child = _child()
    child.image_prompt = "already written STYLE LOCK " * 10
    n = write_coverage_child_prompts([parent, child])
    assert n == 0
    assert child.image_prompt.startswith("already written")
