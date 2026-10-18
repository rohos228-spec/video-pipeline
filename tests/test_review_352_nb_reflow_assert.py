"""Неблокирующее ревью #352: проверка склейки reflow не исчезает под python -O."""

from __future__ import annotations

import inspect


def test_reflow_checks_join_without_assert() -> None:
    from app.services.scene_shot_grammar import reflow_vo_by_sentences

    src = inspect.getsource(reflow_vo_by_sentences)
    assert "assert " not in src
    assert "RuntimeError" in src
    shots = [
        {"id": "a", "закадр": "Первое предложение. Второе"},
        {"id": "b", "закадр": "предложение."},
    ]
    reflow_vo_by_sentences(shots, "Первое предложение. Второе предложение.")
    glued = " ".join(s["закадр"].strip() for s in shots if s["закадр"].strip())
    assert glued == "Первое предложение. Второе предложение."
