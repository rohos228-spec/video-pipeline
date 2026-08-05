"""Character registry + hero: Entity / db_frames SoT (не Excel)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.orchestrator.steps.enrich_xlsx import (
    _is_character_registry_prompt,
    _is_scene_grammar_prompt,
)
from app.services.excel_characters import (
    ExcelCharacter,
    characters_from_entities,
    entity_cards_for_gpt,
)


def test_detect_character_registry_prompt() -> None:
    assert _is_character_registry_prompt(
        "агент по созданию персонажей 02.08.txt.md", None
    )
    assert _is_character_registry_prompt(
        None, "Агент: character_registry_database_agent_v3_web_verified\n"
    )
    assert not _is_character_registry_prompt("scene_grammar_unified_agent_v1", None)
    assert _is_scene_grammar_prompt("scene_grammar_unified_agent_v1", None)


def test_characters_from_entities_and_gpt_cards() -> None:
    ents = [
        SimpleNamespace(
            type="character",
            code="c01",
            name="Асаahara",
            sort_key=10.0,
            attrs={
                "внешность": "мужчина в очках",
                "одежда": "белый халат",
                "характер": "спокойный",
                "правила": "",
            },
        ),
        SimpleNamespace(
            type="character",
            code="c02",
            name="оставь формат неизменный",
            sort_key=20.0,
            attrs={
                "look": "моложе",
                "clothes": "толстовка",
                "char": "резче",
                "rules": "c01",
            },
        ),
        SimpleNamespace(type="background", code="f01", name="метро", attrs={}),
    ]
    chars = characters_from_entities(ents)
    assert [c.id for c in chars] == ["c01", "c02"]
    assert chars[1].ref_ids == ["c01"]
    cards = entity_cards_for_gpt(ents)
    assert cards[0]["имя"] == "Асаahara"
    assert cards[0]["внешность"] == "мужчина в очках"
    assert cards[1]["правила"] == "c01"


@pytest.mark.asyncio
async def test_hero_loads_from_entity_not_xlsx(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from app.models import Project, ProjectStatus
    from app.orchestrator.steps import generate_hero
    from app.settings import settings

    slug = "aum-chars"
    videos = tmp_path / "videos" / slug
    videos.mkdir(parents=True)
    # xlsx отсутствует — Entity должен хватить
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    fake = [ExcelCharacter(id="c01", name="Герой", look="лицо")]

    async def _ents(_session, _project):
        return fake

    monkeypatch.setattr(generate_hero, "_load_entity_characters", _ents)
    monkeypatch.setattr(
        "app.services.excel_characters.parse_persons_sheet",
        lambda _p: (_ for _ in ()).throw(AssertionError("xlsx must not be used")),
    )

    p = Project(topic="t", slug=slug, hero_mode="auto")
    p.status = ProjectStatus.generating_hero
    p.meta = {}
    session = AsyncMock()
    session.flush = AsyncMock()

    cfg = await generate_hero._load_excel_hero_from_xlsx(session, p)
    assert cfg is not None
    assert cfg["source"] == "entity"
    assert cfg["characters"][0]["id"] == "c01"


@pytest.mark.asyncio
async def test_hero_falls_back_to_xlsx_when_entity_empty(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from app.models import Project, ProjectStatus
    from app.orchestrator.steps import generate_hero
    from app.settings import settings

    slug = "aum-chars-xlsx"
    videos = tmp_path / "videos" / slug
    videos.mkdir(parents=True)
    (videos / "project.xlsx").write_bytes(b"fake")
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    async def _ents(_session, _project):
        return []

    monkeypatch.setattr(generate_hero, "_load_entity_characters", _ents)
    fake = ExcelCharacter(id="c01", name="ИзExcel", look="лицо")
    monkeypatch.setattr(
        "app.services.excel_characters.parse_persons_sheet",
        lambda _p: [fake],
    )

    p = Project(topic="t", slug=slug, hero_mode="auto")
    p.status = ProjectStatus.generating_hero
    p.meta = {}
    session = AsyncMock()
    session.flush = AsyncMock()

    cfg = await generate_hero._load_excel_hero_from_xlsx(session, p)
    assert cfg is not None
    assert cfg["source"] == "xlsx"
    assert cfg["characters"][0]["name"] == "ИзExcel"
