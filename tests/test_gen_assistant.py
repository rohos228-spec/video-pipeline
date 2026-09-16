"""gen_assistant: парсинг ответа LLM и обвязка-валидация количества."""

from __future__ import annotations

import json

import pytest

from app.services.gen_assistant import (
    MAX_COUNT,
    extract_agent_core,
    generate_prompts,
    is_stub_agent_text,
    is_unfilled_prompt,
    local_visual_prompt,
    parse_prompts_reply,
    sanitize_prompts,
)

CORE = "Trash polka grunge poster, ink splash, blood-red accents"
REQ = "Кот в плаще стоит на крыше ночью"


def test_parse_json_contract():
    raw = '{"prompts": ["' + CORE + ' — кадр А, ночь, дождь", "' + CORE + ' — кадр Б, крупный план"]}'
    out = parse_prompts_reply(raw)
    assert len(out) == 2
    assert "кадр А" in out[0]


def test_parse_json_inside_markdown_fence():
    raw = 'Вот результат:\n```json\n{"prompts": ["' + CORE + ' — вариант один", "' + CORE + ' — вариант два"]}\n```'
    out = parse_prompts_reply(raw)
    assert len(out) == 2


def test_parse_numbered_fallback():
    raw = f"1. {CORE} — первый кадр\n2. {CORE} — второй кадр\n3. {CORE} — третий кадр"
    out = parse_prompts_reply(raw)
    assert len(out) == 3


def test_parse_empty():
    assert parse_prompts_reply("") == []
    assert parse_prompts_reply("   ") == []


def test_sanitize_does_not_pad_missing():
    out, incomplete = sanitize_prompts(
        [f"{CORE} — только один промпт"], count=3, core=CORE, request=REQ, aspect="9:16"
    )
    assert len(out) == 1
    assert incomplete is True


def test_sanitize_trims_extra():
    many = [f"{CORE} — вариант {i} с длинным описанием кадра" for i in range(6)]
    out, incomplete = sanitize_prompts(many, count=2, core=CORE, request=REQ, aspect="9:16")
    assert len(out) == 2
    assert incomplete is False


def test_sanitize_keeps_dupes_drops_short():
    """Дубли разрешены (запрос «одинаковые/схожие»), короткие — отсев. Без заглушек."""
    dup = f"{CORE} — одинаковый промпт номер один"
    out, incomplete = sanitize_prompts(
        [dup, dup, "коротко"], count=3, core=CORE, request=REQ, aspect="9:16"
    )
    assert out[0] == dup
    assert out[1] == dup
    assert len(out) == 2
    assert incomplete is True


def test_sanitize_prepends_missing_core():
    out, _ = sanitize_prompts(
        ["Описание кадра без ядра стиля, но достаточно длинное"],
        count=1,
        core=CORE,
        request=REQ,
        aspect="9:16",
    )
    assert out[0].startswith(CORE)


def test_sanitize_keeps_llm_prompt_when_core_is_long_template():
    """Длинный шаблон агента нельзя приклеивать в начало — иначе 4000-срез
    съедает заполненный промпт и в генератор уходит пустой [ГЕРОЙ]."""
    core = "---\nname: infographic-hero-object\n" + ("шаблон слот [ГЕРОЙ] " * 400)
    filled = (
        "Magazine poster infographic, landscape sheet, editorial style. "
        "Hero: one photorealistic corn cob кукуруза, half cut open, studio lit. "
        "Headline кукуруза. Bottom strip of maize varieties."
    )
    out, incomplete = sanitize_prompts(
        [filled], count=1, core=core, request="кукуруза в разрезе", aspect="16:9"
    )
    assert incomplete is False
    assert "кукуруза" in out[0]
    assert "[ГЕРОЙ]" not in out[0]
    assert not out[0].lstrip().startswith("---")


def test_sanitize_rejects_template_echo_does_not_fill_stub():
    """Эхо правил агента нельзя слать в генератор и нельзя подменять сырым запросом."""
    core = (
        "---\nname: infographic-hero-object\n"
        "**Герой любой:** початок, двигатель, кроссовок.\n"
        "Hero: one photorealistic [ГЕРОЙ]\n"
    ) * 40
    echo = core[:3500]
    out, incomplete = sanitize_prompts(
        [echo], count=1, core=core, request="двигатель V8 в разрезе", aspect="16:9"
    )
    assert out == []
    assert incomplete is True


def test_sanitize_keeps_long_english_visual_without_russian_tokens():
    """Английский visual-промпт нельзя выкидывать из‑за «приседает» → squat."""
    core = "---\nname: infographic-hero-object\n" + ("шаблон слот " * 400)
    filled = (
        "Minimal line-art presentation slide, 16:9. Background is one flat solid colour. "
        "Two outline human figures: one in a deep squat, one doing a pull-up on a bar. "
        "Captions list three benefits under each exercise. Dense grotesque type, bold headline. "
        "No photorealism, no third colour, wide margins, aligned baseline grid, 2px stroke. "
        "Headline at the top, two columns, bottom caption strip, no watermark, no logo. "
        "Icons are line pictograms of the same stroke weight as the figures throughout."
    )
    assert len(filled) >= 400
    out, incomplete = sanitize_prompts(
        [filled],
        count=1,
        core=core,
        request="мне нужно иллюстрацию человек приседает и человек подтягивается",
        aspect="16:9",
    )
    assert incomplete is False
    assert out[0].startswith("Minimal")
    assert "not example objects" not in out[0].lower()
    from app.services.gen_assistant import is_unfilled_prompt

    req = "мне нужно иллюстрацию человек приседает и человек подтягивается"
    stub = (
        f"{req}. Photorealistic magazine poster infographic of this subject only, "
        "not example objects from the style guide. Aspect ratio: 16:9."
    )
    assert is_unfilled_prompt(stub, req) is True
    out, incomplete = sanitize_prompts(
        [stub], count=1, core=CORE, request=req, aspect="16:9"
    )
    assert out == []
    assert incomplete is True


class _ChatRes:
    def __init__(self, text: str):
        self.text = text


@pytest.mark.asyncio
async def test_generate_template_echo_does_not_start(monkeypatch):
    """Эхо правил от LLM — ошибка, генерация не стартует."""
    from app.services import gpt_api

    core = (
        "---\nname: infographic-hero-object\n"
        "**Герой любой:** початок, кроссовок, гриб.\nHero: [ГЕРОЙ]\n"
    ) * 30

    async def _fake(**kwargs):
        return _ChatRes('{"prompts": [' + json.dumps(core[:3500]) + "]}")

    monkeypatch.setattr(gpt_api, "chat", _fake)
    with pytest.raises(ValueError, match="не собран"):
        await generate_prompts(
            request="двигатель V8 в разрезе", agent_text=core, aspect="16:9", count=1
        )


@pytest.mark.asyncio
async def test_generate_rejects_stub_agent():
    stub = (
        "Агент отвечает одним готовым промптом и ничем больше.\n\n"
        "Каждый промпт начинай с дословно скопированного ядра, не меняй в нём "
        "ни слова и не переводи его:\n"
        "Flat white line graphics on #1E1235.\n\n"
        "После ядра допиши английские фразы."
    )
    assert is_stub_agent_text(stub) is True
    with pytest.raises(ValueError, match="заглушка запрещена"):
        await generate_prompts(request=REQ, agent_text=stub, count=1)


@pytest.mark.asyncio
async def test_generate_validates_empty_request():
    with pytest.raises(ValueError, match="Пустой запрос"):
        await generate_prompts(request="  ", agent_text=CORE, count=1)


@pytest.mark.asyncio
async def test_generate_validates_empty_agent():
    with pytest.raises(ValueError, match="текст агента пуст"):
        await generate_prompts(request=REQ, agent_text="", count=1)


@pytest.mark.asyncio
async def test_generate_sends_agent_as_system(monkeypatch):
    """Агент уходит system-текстом, без файлового вложения."""
    from app.services import gpt_api

    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        body = CORE + " — развёрнутый кадр: кот в плаще на мокрой крыше ночью"
        return _ChatRes('{"prompts": ["' + body + '"]}')

    monkeypatch.setattr(gpt_api, "chat", _fake)
    res = await generate_prompts(request=REQ, agent_text=CORE, aspect="9:16", count=1)
    assert res["source"] == "llm"
    assert len(calls) == 1
    master = calls[0]["system"]
    assert "Ты — агент визуальных промптов" in master
    assert CORE in master
    assert REQ in master
    assert REQ in calls[0]["prompt"]
    assert "нельзя копировать" in master.lower()
    assert not calls[0].get("input_paths")


@pytest.mark.asyncio
async def test_generate_includes_ref_handles_in_master(monkeypatch):
    """Имена @imageN уходят в мастер/user, чтобы агент оставлял теги в промпте."""
    from app.services import gpt_api

    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        body = CORE + " — кадр по @image1, кот в плаще"
        return _ChatRes('{"prompts": ["' + body + '"]}')

    monkeypatch.setattr(gpt_api, "chat", _fake)
    res = await generate_prompts(
        request=REQ,
        agent_text=CORE,
        aspect="9:16",
        count=1,
        ref_labels=["@image1 — кот.png", "@image2 — фон.jpg"],
    )
    assert res["source"] == "llm"
    master = calls[0]["system"]
    assert "@image1 — кот.png" in master
    assert "@image2 — фон.jpg" in master
    assert "РЕФЕРЕНСЫ" in master
    assert "@image1 — кот.png" in calls[0]["prompt"]
    assert "@image1" in res["prompts"][0]


@pytest.mark.asyncio
async def test_generate_without_llm_does_not_start(monkeypatch):
    """Без ключа/LLM — ошибка, генерация не стартует."""
    from app.services import gpt_api

    async def _boom(**kwargs):
        raise RuntimeError("no api key")

    monkeypatch.setattr(gpt_api, "chat", _boom)
    with pytest.raises(ValueError, match="не собран"):
        await generate_prompts(request=REQ, agent_text=CORE, aspect="9:16", count=MAX_COUNT)


def test_is_unfilled_prompt_rejects_subject_wrapper():
    req = "ПОКАЖИ 5 ПЛЮСОВ ПОДТЯГИВАНИЙ"
    junk = (
        "Flat white line graphics on #1E1235.\n\n"
        f"Subject of this image (the only topic): {req}. "
        "Depict this request as one finished scene in the style above."
    )
    assert is_unfilled_prompt(junk, req) is True


def test_extract_agent_core_from_written_agent():
    agent = (
        "Агент отвечает одним готовым промптом и ничем больше.\n\n"
        "Каждый промпт начинай с дословно скопированного ядра, не меняй в нём "
        "ни слова и не переводи его:\n"
        "Flat white line graphics on #1E1235, 2px outline, no fill, editorial slide.\n\n"
        "После ядра допиши английские фразы.\n\n"
        "В финальной строке-негативе обязательно: photorealism, gradients."
    )
    core = extract_agent_core(agent)
    assert "#1E1235" in core
    assert "Агент отвечает" not in core
    prompt = local_visual_prompt(
        request="приседания и подтягивания", agent_text=agent, aspect="16:9"
    )
    assert prompt.startswith("Flat white")
    assert "приседания" in prompt
    assert "Subject of this image" in prompt
    assert "photorealism" in prompt


def test_local_prompt_strips_reference_topic_and_builds_scene():
    agent = (
        "Агент отвечает одним готовым промптом.\n\n"
        "Каждый промпт начинай с дословно скопированного ядра, не меняй в нём "
        "ни слова и не переводи его:\n"
        "Thin white line graphics on #1E1235. "
        "Фитнес, сила, плиометрика, мышцы, нервная система, биомеханика. "
        "Schematic outline icons, flat lighting.\n\n"
        "После ядра допиши английские фразы.\n\n"
        "В финальной строке-негативе обязательно: photorealism."
    )
    prompt = local_visual_prompt(
        request="покажи 6 плюсов подтягиваний", agent_text=agent, aspect="16:9"
    )
    assert "плиометрика" not in prompt
    assert "фитнес" not in prompt.lower()
    assert "подтягиван" in prompt
    assert "Subject of this image" in prompt
    assert "lonely caption" in prompt
    assert not prompt.strip().endswith("покажи 6 плюсов подтягиваний")


@pytest.mark.asyncio
async def test_generate_retry_recovers_real_prompt(monkeypatch):
    """Первый ответ — эхо правил, повтор — готовый visual-промпт → генерация ок."""
    from app.services import gpt_api

    core = (
        "---\nname: infographic-hero-object\n"
        "**Герой любой:** початок, кроссовок, гриб.\nHero: [ГЕРОЙ]\n"
    ) * 30
    filled = (
        "Magazine poster infographic, landscape sheet, editorial science style. "
        "Hero: one photorealistic V8 engine in cutaway, studio-lit metal, oil sheen. "
        "Headline двигатель V8. Callouts for pistons, crankshaft, valves."
    )
    calls: list[int] = []

    async def _fake(**kwargs):
        calls.append(1)
        if len(calls) == 1:
            return _ChatRes('{"prompts": [' + json.dumps(core[:3500]) + "]}")
        return _ChatRes('{"prompts": [' + json.dumps(filled) + "]}")

    monkeypatch.setattr(gpt_api, "chat", _fake)
    res = await generate_prompts(
        request="двигатель V8 в разрезе", agent_text=core, aspect="16:9", count=1
    )
    assert res["source"] == "llm"
    assert len(calls) == 2
    assert "двигатель" in res["prompts"][0].lower()
    assert "[ГЕРОЙ]" not in res["prompts"][0]


def test_custom_styles_disk_roundtrip(tmp_path, monkeypatch):
    from app.settings import settings
    from app.web.routers import gen_assistant as ga

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(ga, "_seed_styles_path", lambda: tmp_path / "no-seed.json")
    monkeypatch.setattr(ga, "_seed_agents_path", lambda: tmp_path / "no-agents.json")
    assert ga._read_custom_styles() == []
    payload = {
        "id": "custom_test",
        "categoryId": "infographic",
        "name": "Тест",
        "promptCore": "ядро стиля достаточно длинное для сохранения",
        "art": "infographic",
        "file": "custom",
        "desc": "desc",
        "color": "cyan",
        "tags": ["custom"],
    }
    path = tmp_path / "gen_assistant_styles.json"
    path.write_text(json.dumps({"styles": [payload]}, ensure_ascii=False), encoding="utf-8")
    got = ga._read_custom_styles()
    assert len(got) == 1
    assert got[0]["name"] == "Тест"


def test_seed_styles_and_agents_merge(tmp_path, monkeypatch):
    from app.settings import settings
    from app.web.routers import gen_assistant as ga

    seed = tmp_path / "seed.json"
    seed.write_text(
        json.dumps(
            {
                "styles": [
                    {
                        "id": "custom_mtwuuy1z",
                        "name": "Грифельная",
                        "promptCore": "агент-ядро стиля " + ("x" * 40),
                        "artUrl": "/gen-styles/custom_mtwuuy1z.jpg",
                        "cover": "/gen-styles/custom_mtwuuy1z.jpg",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    disk = tmp_path / "gen_assistant_styles.json"
    disk.write_text(
        json.dumps(
            {
                "styles": [
                    {
                        "id": "custom_mtwuuy1z",
                        "name": "Грифельная",
                        "promptCore": "короче",
                        "artUrl": "/api/files?path=C:\\local\\cover.png",
                    },
                    {
                        "id": "custom_extra",
                        "name": "Ещё",
                        "promptCore": "второй агент достаточно длинный",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(ga, "_seed_styles_path", lambda: seed)
    monkeypatch.setattr(ga, "_seed_agents_path", lambda: tmp_path / "no-agents.json")
    got = {s["id"]: s for s in ga._read_custom_styles()}
    assert "custom_mtwuuy1z" in got
    assert got["custom_mtwuuy1z"]["promptCore"].startswith("агент-ядро")
    assert got["custom_mtwuuy1z"]["artUrl"] == "/gen-styles/custom_mtwuuy1z.jpg"
    assert got["custom_extra"]["name"] == "Ещё"
    agents = ga._read_agent_overrides()
    assert agents["custom_mtwuuy1z"].startswith("агент-ядро")
    assert agents["custom_extra"] == "второй агент достаточно длинный"


def test_save_style_cover_writes_portable_url(tmp_path, monkeypatch):
    from app.settings import settings
    from app.web.routers import gen_assistant as ga

    repo = tmp_path / "repo"
    (repo / "web" / "out" / "gen-styles").mkdir(parents=True)
    (repo / "web" / "public" / "gen-styles").mkdir(parents=True)
    src = tmp_path / "generations" / "shot.png"
    src.parent.mkdir()
    src.write_bytes(b"\x89PNG fake")
    (tmp_path / "gen_assistant_styles.json").write_text(
        json.dumps(
            {
                "styles": [
                    {
                        "id": "custom_mu3s8rvy",
                        "name": "текст",
                        "promptCore": "ядро стиля достаточно длинное",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(ga, "find_project_root", lambda: repo)
    monkeypatch.setattr(ga, "_seed_styles_path", lambda: tmp_path / "no-seed.json")
    monkeypatch.setattr(ga, "_seed_agents_path", lambda: tmp_path / "no-agents.json")

    url = ga.save_style_cover(style_id="custom_mu3s8rvy", src=src)
    assert url == "/gen-styles/custom_mu3s8rvy.png"
    stored = tmp_path / "gen_styles" / "custom_mu3s8rvy.png"
    assert stored.is_file()
    assert stored.read_bytes().startswith(b"\x89PNG")
    assert ga.resolve_style_cover("custom_mu3s8rvy.png") == stored
    assert ga.resolve_style_cover("../secret.png") is None
    got = {s["id"]: s for s in ga._read_custom_styles()}
    assert got["custom_mu3s8rvy"]["cover"] == url
    assert got["custom_mu3s8rvy"]["artUrl"] == url

