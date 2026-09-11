"""style_analyzer: парсинг JSON, батчинг, валидация путей, анализ с моком LLM."""

from __future__ import annotations

import pytest

from app.services.style_analyzer import (
    AGENT_MAX_BYTES,
    AGENT_MAX_CHARS,
    batch_paths,
    collect_image_paths,
    fit_agent_text,
    parse_agent_reply,
    parse_json_object,
    strip_markdown_fences,
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


def test_strip_markdown_fences():
    assert strip_markdown_fences("```text\nагент\n```") == "агент"
    assert strip_markdown_fences("  агент  ") == "агент"


def test_fit_agent_text_keeps_short_text():
    text = "Ты — генератор промптов.\n\nЯдро копируй дословно."
    out, warning = fit_agent_text(text)
    assert out == text
    assert warning is None


def test_fit_agent_text_trims_by_bytes():
    """Кириллица — 2 байта: агент влезает в знаки, но не в байты."""
    para = "Ядро стиля описывает палитру и линию подробно и по-русски.\n\n"
    text = para * 120
    assert len(text) < AGENT_MAX_CHARS
    assert len(text.encode("utf-8")) > AGENT_MAX_BYTES
    out, warning = fit_agent_text(text)
    assert len(out) <= AGENT_MAX_CHARS
    assert len(out.encode("utf-8")) <= AGENT_MAX_BYTES
    assert warning
    assert out.endswith("по-русски.")


def test_parse_agent_reply_fields():
    raw = (
        "NAME: Тёмные слайды\n"
        "DESC: Белая линия на тёмном фоне\n"
        "CATEGORY: Инфографика\n"
        "AGENT:\n" + "Ты отвечаешь одним готовым промптом. " * 12
    )
    entry = parse_agent_reply(raw)
    assert entry["name"] == "Тёмные слайды"
    assert entry["category"] == "Инфографика"
    assert entry["agent"].startswith("Ты отвечаешь одним готовым промптом.")
    assert "NAME:" not in entry["agent"]


def test_parse_agent_reply_without_markers():
    raw = "Ты отвечаешь одним готовым промптом в стиле референсов. " * 8
    entry = parse_agent_reply(raw, name_hint="Стиль")
    assert entry["name"] == "Стиль"
    assert entry["agent"].startswith("Ты отвечаешь")


def test_parse_agent_reply_rejects_short():
    with pytest.raises(ValueError, match="короткого агента"):
        parse_agent_reply("NAME: X\nAGENT:\nмало")


@pytest.mark.asyncio
async def test_build_style_agent_with_mock(monkeypatch, tmp_path):
    from app.services import gpt_client, style_analyzer

    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG")
    seen: dict[str, str] = {}

    class FakeClient:
        async def ask_with_files(self, text, files, *, system=None, **kw):
            if "пачку референсов" in text:
                return '{"palette": "white on #1E1235", "line": "2px outline", "light": "flat", "composition": "slide", "subjects": "figures", "mood": "clean", "forbidden": "colour"}'
            seen["user"] = text
            return (
                "NAME: Тёмный слайд\nDESC: Белая линия\nCATEGORY: Инфографика\nAGENT:\n"
                + "Ты отвечаешь одним готовым промптом и ничем больше. " * 10
            )

    monkeypatch.setattr(gpt_client, "get_gpt_client", lambda: FakeClient())
    monkeypatch.setattr(style_analyzer, "get_gpt_client", lambda: FakeClient(), raising=False)

    res = await style_analyzer.build_style_agent(
        [img], user_request="зафиксируй цвет фона", name_hint="Тёмный"
    )
    assert res["name"] == "Тёмный слайд"
    assert res["images"] == 1
    assert res["bytes"] == len(res["agent"].encode("utf-8"))
    assert "зафиксируй цвет фона" in seen["user"]


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
