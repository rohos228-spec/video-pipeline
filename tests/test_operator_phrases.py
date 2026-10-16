from __future__ import annotations

import re

from app.services.operator_phrases import operator_phrase
from app.services.timeline_ru import _METHOD_RU

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def _templates() -> list[tuple[str, str]]:
    from app.web.api import create_app
    out = set()
    for path, item in create_app().openapi()["paths"].items():
        if path.startswith("/api/") and path != "/api/{rest}":
            for method in item:
                if method.upper() in MUTATING:
                    out.add((method.upper(), path))
    return sorted(out)


def test_every_mutating_route_has_its_own_russian_phrase() -> None:
    routes = _templates()
    assert len(routes) > 150
    missing = [(m, p) for m, p in routes if not operator_phrase(m, p)]
    assert not missing, f"нет фразы для маршрутов (добавьте слова в app/services/operator_phrases.py): {missing[:10]}"
    bad = [(m, p) for m, p in routes if not re.search(r"[А-Яа-яЁё]", operator_phrase(m, p) or "")]
    assert not bad
    generic = set(_METHOD_RU.values())
    assert not [(m, p) for m, p in routes if operator_phrase(m, p) in generic]


def test_phrases_distinguish_different_buttons() -> None:
    assert operator_phrase("POST", "/api/projects/{project_id}/pause") == "Оператор поставил на паузу: проект"
    assert operator_phrase("POST", "/api/projects/{project_id}/montage-board/delete-image") == (
        "Оператор удалил картинку: монтажная доска")
    assert operator_phrase("POST", "/api/projects/{project_id}/observations/kill-switch") == (
        "Оператор переключил рубильник: наблюдение")
    assert operator_phrase("POST", "/api/projects/{project_id}/stop") != operator_phrase(
        "POST", "/api/projects/{project_id}/pause")
    routes = _templates()
    assert len({operator_phrase(m, p) for m, p in routes}) >= 120
