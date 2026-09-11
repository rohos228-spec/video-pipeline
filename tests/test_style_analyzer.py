"""style_analyzer: парсинг JSON, батчинг, валидация путей, анализ с моком LLM."""

from __future__ import annotations

import pytest

from app.services.style_analyzer import (
    batch_paths,
    collect_image_paths,
    parse_json_object,
)


def test_parse_json_object_plain():
    assert parse_json_object('{"name": "Полька", "desc": "x"}') == {"name": "Полька", "desc": "x"}


def test_parse_json_object_in_fence():
    raw = 'Ответ:\n```json\n{"name": "Нуар"}\n```\nконец'
    assert parse_json_object(raw) == {"name": "Нуар"}


def test_parse_json_object_garbage():
    assert parse_json_object("никакого json тут") is None
    assert parse_json_object("") is None


def test_batch_paths():
    items = list(range(20))
    batches = batch_paths(items, size=8)
    assert [len(b) for b in batches] == [8, 8, 4]


def test_collect_image_paths_validation(tmp_path):
    img = tmp_path / "a.png"
    img.write_bytes(b"\x89PNG")
    txt = tmp_path / "b.txt"
    txt.write_text("x", encoding="utf-8")

    assert collect_image_paths([img]) == [img]
    with pytest.raises(ValueError, match="Не изображение"):
        collect_image_paths([txt])
    with pytest.raises(ValueError, match="не найден"):
        collect_image_paths([tmp_path / "nope.png"])
    with pytest.raises(ValueError, match="Нет изображений"):
        collect_image_paths([])


@pytest.mark.asyncio
async def test_analyze_style_images_with_mock(monkeypatch, tmp_path):
    """Две пачки заметок → одна запись стиля (LLM замокана)."""
    from app.services import style_analyzer

    imgs = []
    for i in range(10):
        p = tmp_path / f"img{i:02d}.png"
        p.write_bytes(b"\x89PNG")
        imgs.append(p)

    calls: list[str] = []

    class FakeClient:
        async def ask_with_files(self, text, files, *, system=None, **kw):
            if "пачку референсов" in text:
                calls.append("notes")
                return '{"palette": "red/black", "line": "ink", "light": "hard", "composition": "poster", "subjects": "cats", "mood": "grim", "forbidden": "pastel"}'
            calls.append("synth")
            return (
                '{"name": "Тест-полька", "desc": "Тестовый стиль", "category": "Постер", '
                '"prompt_core": "Trash polka test core\\n\\nRU: тест\\n\\nНе pastel, cute"}'
            )

    from app.services import gpt_client

    monkeypatch.setattr(gpt_client, "get_gpt_client", lambda: FakeClient())
    monkeypatch.setattr(style_analyzer, "get_gpt_client", lambda: FakeClient(), raising=False)

    entry = await style_analyzer.analyze_style_images(imgs, name_hint="тест")
    assert entry["name"] == "Тест-полька"
    assert entry["prompt_core"].startswith("Trash polka")
    assert entry["images"] == 10
    # 10 изображений → 2 пачки заметок + 1 синтез
    assert calls.count("notes") == 2
    assert calls.count("synth") == 1


@pytest.mark.asyncio
async def test_categorize_styles_with_mock(monkeypatch):
    from app.services import gpt_client, style_analyzer

    class FakeClient:
        async def ask_with_files(self, text, files, *, system=None, **kw):
            return '{"categories": [{"name": "Постеры", "criteria": "ink+red", "style_names": ["А", "Б"]}]}'

    monkeypatch.setattr(gpt_client, "get_gpt_client", lambda: FakeClient())
    monkeypatch.setattr(style_analyzer, "get_gpt_client", lambda: FakeClient(), raising=False)

    res = await style_analyzer.categorize_styles(
        [{"name": "А", "prompt_core": "x"}, {"name": "Б", "prompt_core": "y"}]
    )
    assert res["styles"] == 2
    assert res["categories"][0]["style_names"] == ["А", "Б"]
