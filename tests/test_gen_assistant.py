"""gen_assistant: парсинг ответа LLM и обвязка-валидация количества."""

from __future__ import annotations

import json

import pytest

from app.services.gen_assistant import (
    MAX_COUNT,
    generate_prompts,
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


@pytest.mark.asyncio
async def test_generate_rejects_template_echo_no_generation(monkeypatch):
    """Если LLM вернула копипасту правил — генерация не стартует."""
    from app.services import gpt_client

    core = (
        "---\nname: infographic-hero-object\n"
        "**Герой любой:** початок, кроссовок, гриб.\nHero: [ГЕРОЙ]\n"
    ) * 30

    class _Fake:
        async def ask_with_files(self, text, files, **kwargs):
            return '{"prompts": [' + json.dumps(core[:3500]) + "]}"

    monkeypatch.setattr(gpt_client, "get_gpt_client", lambda: _Fake())
    with pytest.raises(ValueError, match="не собрал промпт"):
        await generate_prompts(
            request="двигатель V8 в разрезе", agent_text=core, aspect="16:9", count=1
        )


@pytest.mark.asyncio
async def test_generate_validates_empty_request():
    with pytest.raises(ValueError, match="Пустой запрос"):
        await generate_prompts(request="  ", agent_text=CORE, count=1)


@pytest.mark.asyncio
async def test_generate_validates_empty_agent():
    with pytest.raises(ValueError, match="текст агента пуст"):
        await generate_prompts(request=REQ, agent_text="", count=1)


@pytest.mark.asyncio
async def test_generate_attaches_agent_prompt_file(monkeypatch):
    """Как у пайплайн-агентов: мастер-промт — первый .md, не пустой system=."""
    from app.services import gpt_client

    calls: list[dict] = []

    class _Fake:
        async def ask_with_files(self, text, files, **kwargs):
            paths = list(files)
            master = ""
            if paths:
                master = paths[0].read_text(encoding="utf-8")
            calls.append(
                {"text": text, "files": paths, "master": master, "kwargs": kwargs}
            )
            body = CORE + " — развёрнутый кадр: кот в плаще на мокрой крыше ночью"
            return '{"prompts": ["' + body + '"]}'

    monkeypatch.setattr(gpt_client, "get_gpt_client", lambda: _Fake())
    res = await generate_prompts(request=REQ, agent_text=CORE, aspect="9:16", count=1)
    assert res["source"] == "llm"
    assert len(calls) == 1
    files = calls[0]["files"]
    assert files, "агент должен получить файл промпта"
    assert files[0].suffix.lower() in {".md", ".txt"}
    master = calls[0]["master"]
    assert "Ты — агент визуальных промптов" in master
    assert CORE in master
    assert REQ in master
    assert REQ in calls[0]["text"]
    assert "нельзя копировать" in master.lower()
    assert not calls[0]["kwargs"].get("system")


@pytest.mark.asyncio
async def test_generate_fails_without_llm(monkeypatch):
    """Без ключа/LLM — ошибка, генерация не запускается."""
    from app.services import gpt_client

    def _boom():
        raise RuntimeError("no api key")

    monkeypatch.setattr(gpt_client, "get_gpt_client", _boom)
    with pytest.raises(ValueError, match="не собран"):
        await generate_prompts(request=REQ, agent_text=CORE, aspect="9:16", count=MAX_COUNT)


@pytest.mark.asyncio
async def test_generate_retry_recovers_real_prompt(monkeypatch):
    """Первый ответ — эхо правил, повтор — готовый visual-промпт → генерация ок."""
    from app.services import gpt_client

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

    class _Fake:
        async def ask_with_files(self, text, files, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return '{"prompts": [' + json.dumps(core[:3500]) + "]}"
            return '{"prompts": [' + json.dumps(filled) + "]}"

    monkeypatch.setattr(gpt_client, "get_gpt_client", lambda: _Fake())
    res = await generate_prompts(
        request="двигатель V8 в разрезе", agent_text=core, aspect="16:9", count=1
    )
    assert res["source"] == "llm"
    assert len(calls) == 2
    assert "двигатель" in res["prompts"][0].lower()
    assert "[ГЕРОЙ]" not in res["prompts"][0]
