"""world: id только из locations_seed; без света/триггеров/предметов."""

from __future__ import annotations

import pytest

from app.services.scene_design import agents as ag


def test_normalize_world_strips_forbidden_fields() -> None:
    data = {
        "locations": [
            {
                "id": "loc01",
                "name": "банк",
                "описание": "мраморный зал",
                "зоны": ["вход", "касса"],
                "освещение": "холодный fluorescent",
                "время": "утро",
                "триггеры_закадра": ["банк"],
                "предметы": ["стойка"],
            }
        ]
    }
    out = ag.normalize_world_locations(data)
    loc = out["locations"][0]
    assert loc["id"] == "loc01"
    assert loc["name"] == "банк"
    assert "зоны" in loc
    assert "освещение" not in loc
    assert "время" not in loc
    assert "триггеры_закадра" not in loc
    assert "предметы" not in loc


def test_normalize_world_keeps_only_seed_ids() -> None:
    data = {
        "locations": [
            {"id": "loc01", "name": "а", "описание": "x", "зоны": ["1"]},
            {"id": "loc99", "name": "выдумка", "описание": "y", "зоны": ["2"]},
        ]
    }
    out = ag.normalize_world_locations(data, seed_ids={"loc01"})
    assert [x["id"] for x in out["locations"]] == ["loc01"]


def test_normalize_world_requires_all_seed_ids() -> None:
    data = {"locations": [{"id": "loc01", "name": "а", "описание": "x", "зоны": ["1"]}]}
    with pytest.raises(ag.SceneDesignAgentError, match="locations_seed"):
        ag.normalize_world_locations(data, seed_ids={"loc01", "loc02"})


def test_normalize_world_empty_seed() -> None:
    data = {
        "locations": [
            {"id": "loc01", "name": "а", "описание": "x", "зоны": ["1"]},
        ]
    }
    out = ag.normalize_world_locations(data, seed_ids=set())
    assert out["locations"] == []


def test_parse_world_allows_empty_locations() -> None:
    raw = '{"locations": [], "report": "локаций:0"}'
    data = ag.parse_agent_slice("world", raw)
    assert data["locations"] == []


def test_style_removed_from_waves() -> None:
    assert "style" not in ag.CATEGORY_AGENTS
    assert "style" not in ag.WAVE1_AGENTS
    assert "style" not in ag.CHRONO_WAVE1_AGENTS
    assert "style" in ag.DEPRECATED_AGENTS
