"""Автосценарий NII 67: стоп-лист, визуализация, биты и параллельный старт."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services import nii67_agent as ag
from app.services import nii67_scenario as sc
from app.services.nii67_card import default_card
from app.services.nii67_scenario import (
    ScenarioGenerationError,
    build_system_prompt,
    generate_scenarios,
    stoplist_errors,
    validate_door_scenario,
    visualization_errors,
)

ROOT = Path("prompts/templates/nii67")
CATS = ["absolute_zero", "artwork"]
OUTS = ["escape", "transformation"]


@pytest.fixture(autouse=True)
def _tmp_history(tmp_path, monkeypatch):
    monkeypatch.setattr(sc, "HISTORY_PATH", tmp_path / "nii67_history.json")


def _changes(**over: str) -> dict[str, str]:
    base = {
        "pose": "корпус наклоняется вперёд",
        "light": "лампа сверху светит ярче",
        "body": "колено поднимается над полом",
        "scale": "герой в полный рост рядом с одним предметом",
        "environment": "пол и стены комнаты",
    }
    base.update(over)
    return base


def _frame(start: str, end: str, camera: str, feel: str, action: str, **env: str) -> dict:
    return {
        "start_frame": start,
        "end_frame": end,
        "camera": camera,
        "camera_feel": feel,
        "action": action,
        "characters_on": ["Герой"],
        "visual_changes": _changes(**env),
    }


def door_kitchen() -> dict:
    return {
        "self_scores": {"emotion": 9, "interest": 8, "stakes": 8, "concept": 8},
        "twist": "стол, на который герой прыгает, тоже покрывается шипами",
        "category": "absolute_zero",
        "mechanic": "капли растут в стеклянные шипы",
        "theme": "Мокрая кухня, где капли на полу встают стеклянными шипами",
        "law": "Каждая капля воды на полу твердеет в стеклянный шип ростом с ладонь.",
        "outcome": "escape",
        "outcome_process": (
            "Герой прыгает на сухой стол, стеклянные шипы из капель вырастают до колена "
            "и закрывают пол, он перекатывается к сухому проёму."
        ),
        "door_design": "Деревянная дверь с каплями воды и короткими стеклянными шипами по краю",
        "door_anim": "Капли на двери раздуваются и твердеют, пока дверь не покрывается стеклянными шипами",
        "illustration": "Герой стоит на столе над стеклянными шипами, сухой проём открыт за спиной",
        "finale_line": "На ботинках героя блестят стеклянные крошки",
        "beats": [
            {
                "id": "entry",
                "text": "Герой шагает на мокрый пол кухни и сразу поднимает ногу: под ступнёй встаёт стеклянный шип.",
                "frames": [_frame(
                    "Ступня героя над мокрой плиткой",
                    "Под ступнёй капля встаёт коротким стеклянным шипом",
                    "far", "handheld_soft",
                    "Герой шагает на мокрый пол и поднимает ногу",
                    environment="мокрый пол кухни",
                )],
            },
            {
                "id": "law",
                "text": "Капля у носка твердеет и вырастает в стеклянный шип ростом с ладонь.",
                "frames": [_frame(
                    "Маленькая капля лежит у носка",
                    "На месте капли стоит стеклянный шип ростом с ладонь",
                    "mid", "handheld_soft",
                    "Капля твердеет и вырастает в шип у носка",
                    environment="плитка у носка героя",
                )],
            },
            {
                "id": "threat",
                "text": "Шипы из соседних капель вырастают до колена и закрывают шаг к двери.",
                "frames": [_frame(
                    "Несколько капель лежат на пути к двери",
                    "Шипы до колена закрывают шаг к двери",
                    "side", "handheld_action",
                    "Шипы вырастают до колена и закрывают проход",
                    environment="путь к двери перекрыт шипами",
                )],
            },
            {
                "id": "peak",
                "text": (
                    "Герой прыгает на столешницу, стеклянные шипы снизу вырастают "
                    "и царапают край ботинка, он перекатывается к дальнему краю стола."
                ),
                "frames": [
                    _frame(
                        "Герой стоит на полу среди коротких шипов",
                        "Герой уже на столешнице, шипы тянутся к ботинку",
                        "mid", "handheld_action",
                        "Герой прыгает на стол, шипы вырастают снизу",
                        environment="столешница над полем шипов",
                    ),
                    _frame(
                        "Герой на ближнем краю стола",
                        "Герой у дальнего края стола, шипы ниже столешницы",
                        "POV", "handheld_action",
                        "Герой перекатывается по столу к проёму",
                        environment="дальняя кромка стола и сухой проём",
                    ),
                ],
            },
            {
                "id": "outcome",
                "text": "Герой перекатывается по столу и выпадает в сухой проём, шипы остаются на полу внизу.",
                "frames": [_frame(
                    "Герой на краю стола над шипами",
                    "Герой в сухом проёме, шипы остались на полу",
                    "close", "handheld_intimate",
                    "Герой выпадает в сухой проём",
                    environment="сухой проём и пол с шипами внизу",
                )],
            },
            {
                "id": "hook",
                "text": "Герой стоит в сухом проёме, внизу поле стеклянных шипов, одна нога ещё на столе.",
                "frames": [_frame(
                    "Герой ещё касается стола ногой",
                    "Герой стоит в проёме, нога уже не на столе, внизу шипы",
                    "far", "handheld_soft",
                    "Герой стоит в проёме над шипами",
                    environment="проём, стол и поле стеклянных шипов",
                )],
            },
        ],
    }


def door_paper() -> dict:
    return {
        "self_scores": {"emotion": 8, "interest": 9, "stakes": 8, "concept": 8},
        "twist": "пиджак героя сам оказывается бумажным листом",
        "category": "artwork",
        "mechanic": "листы складываются в клювы",
        "theme": "Мастерская бумаги, где листы сами складываются в клювы",
        "law": "Каждый лист бумаги на столе складывается в клюв и растёт до размера руки.",
        "outcome": "transformation",
        "outcome_process": (
            "Пиджак героя складывается как лист бумаги и растёт до размера руки, "
            "тело становится плоским листом бумаги."
        ),
        "door_design": "Дверь оклеена листами бумаги, из листа торчит сложенный клюв",
        "door_anim": "Листы на двери сами складываются в клювы и растут до размера руки",
        "illustration": "Герой стоит плоским листом бумаги, плечо сложено клювом",
        "finale_line": "Плечо героя сложено бумажным клювом размером с руку",
        "beats": [
            {
                "id": "entry",
                "text": "Герой шагает к столу, и ближайший лист бумаги сам складывается в клюв.",
                "frames": [_frame(
                    "Лист бумаги лежит плоско на столе",
                    "Лист сложился в клюв и смотрит на героя",
                    "far", "handheld_soft",
                    "Герой шагает к столу, лист складывается в клюв",
                    environment="стол мастерской и один лист бумаги",
                )],
            },
            {
                "id": "law",
                "text": "Лист складывается в клюв и растёт до размера руки прямо на столе.",
                "frames": [_frame(
                    "Клюв из бумаги размером с палец",
                    "Клюв из бумаги вырос до размера руки",
                    "side", "handheld_action",
                    "Лист складывается в клюв и растёт до размера руки",
                    environment="столешница мастерской",
                )],
            },
            {
                "id": "threat",
                "text": "Клюв тянется к пиджаку и начинает складывать ткань, как лист.",
                "frames": [_frame(
                    "Бумажный клюв лежит на столе в стороне от героя",
                    "Клюв упирается в полу пиджака и сгибает ткань",
                    "mid", "handheld_soft",
                    "Клюв тянет полу пиджака и сгибает ткань",
                    environment="герой у стола, клюв у пиджака",
                )],
            },
            {
                "id": "peak",
                "text": (
                    "Пиджак героя складывается в огромный бумажный клюв и растёт до размера руки, "
                    "пока герой пытается оторвать ткань от стола."
                ),
                "frames": [
                    _frame(
                        "Пиджак ещё висит обычно",
                        "Пола пиджака сложилась бумажным клювом",
                        "close", "handheld_intimate",
                        "Пиджак складывается в бумажный клюв на груди",
                        environment="грудь героя и стол с листами",
                    ),
                    _frame(
                        "Клюв на груди размером с ладонь",
                        "Клюв на груди вырос до размера руки",
                        "POV", "handheld_action",
                        "Бумажный клюв растёт до размера руки",
                        environment="грудь крупно, стол уходит вниз",
                    ),
                ],
            },
            {
                "id": "outcome",
                "text": "Тело героя становится плоским листом бумаги с клювом вместо плеча.",
                "frames": [_frame(
                    "Герой ещё объёмный, клюв только на пиджаке",
                    "Тело героя стало плоским листом бумаги с клювом",
                    "mid", "handheld_action",
                    "Тело становится плоским листом бумаги",
                    environment="мастерская и плоский силуэт у стола",
                )],
            },
            {
                "id": "hook",
                "text": "Плоский бумажный герой стоит у стола, плечо сложено клювом размером с руку.",
                "frames": [_frame(
                    "Герой ещё опирается ладонью на стол",
                    "Герой стоит плоским листом, ладонь уже не на столе",
                    "far", "handheld_soft",
                    "Герой стоит плоским листом бумаги у стола",
                    environment="мастерская, стол и плоский герой",
                )],
            },
        ],
    }


def _blob(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False)


def test_templates_drive_prompt_and_limits() -> None:
    rules = json.loads((ROOT / "v2" / "self_check.json").read_text(encoding="utf-8"))
    assert rules["stagger_s"] == 5
    assert rules["max_attempts"] == 3
    v1 = json.loads((ROOT / "v1" / "frame_contract.json").read_text(encoding="utf-8"))
    v2 = json.loads((ROOT / "v2" / "frame_contract.json").read_text(encoding="utf-8"))
    assert set(v1["required"]) == set(v2["required"])
    prompt = build_system_prompt()
    assert "Жёсткое правило визуализации" in prompt
    assert "чёрная дыра" in prompt
    assert "два close подряд" in prompt
    assert "entry" in prompt and "hook" in prompt


def test_stoplist_rejects_themes_and_close_copies() -> None:
    items = json.loads((ROOT / "v2" / "stoplist.json").read_text(encoding="utf-8"))["items"]
    assert len(items) >= 14
    for item in items:
        for sample in item["examples"]:
            hits = stoplist_errors(sample)
            assert f"stoplist:{item['id']}" in hits, sample
    assert stoplist_errors(_blob(door_kitchen())) == []
    assert stoplist_errors(_blob(door_paper())) == []


def test_visualization_validator_and_it_is_first() -> None:
    groups = json.loads((ROOT / "v2" / "visualization.json").read_text(encoding="utf-8"))["forbidden"]
    assert groups[0]["id"] == "sound"
    for group in groups:
        for sample in group["examples"]:
            hits = visualization_errors(sample)
            assert hits and hits[0] == f"visual:{group['id']}", sample
    assert visualization_errors(_blob(door_kitchen())) == []
    assert visualization_errors(_blob(door_paper())) == []
    dirty = door_kitchen()
    dirty["theme"] = "Героя затягивает чёрная дыра и он слышит вой"
    dirty["law"] = "Героя затягивает чёрная дыра и он слышит вой."
    errors = validate_door_scenario(dirty)
    assert errors[0].startswith("visual:")
    assert any(code.startswith("stoplist:black_hole") for code in errors)
    visual_at = next(i for i, code in enumerate(errors) if code.startswith("visual:"))
    stop_at = next(i for i, code in enumerate(errors) if code.startswith("stoplist:"))
    assert visual_at < stop_at


def test_beat_and_frame_structure() -> None:
    assert validate_door_scenario(door_kitchen()) == []
    assert validate_door_scenario(door_paper()) == []

    short = door_kitchen()
    short["beats"] = short["beats"][:-1]
    assert "beats_count" in validate_door_scenario(short)

    crowded_frames = door_kitchen()
    crowded_frames["beats"][3]["frames"] = crowded_frames["beats"][3]["frames"] * 4
    assert any(code.startswith("frames_count:peak") for code in validate_door_scenario(crowded_frames))

    same = door_kitchen()
    same["beats"][0]["frames"][0]["end_frame"] = same["beats"][0]["frames"][0]["start_frame"]
    assert "no_change" in validate_door_scenario(same)

    missing = door_kitchen()
    missing["beats"][0]["frames"][0]["visual_changes"]["pose"] = ""
    assert "visual_changes:pose" in validate_door_scenario(missing)

    bad_cam = door_kitchen()
    bad_cam["beats"][0]["frames"][0]["camera"] = "drone"
    assert "bad_camera" in validate_door_scenario(bad_cam)

    closes = door_kitchen()
    for beat in closes["beats"]:
        for frame in beat["frames"]:
            frame["camera"] = "close"
    assert "double_close" in validate_door_scenario(closes)

    dull = door_kitchen()
    dull["theme"] = "Город"
    assert "theme_weak" in validate_door_scenario(dull)

    unlinked = door_kitchen()
    unlinked["outcome_process"] = "Уходит"
    assert "outcome_unlinked" in validate_door_scenario(unlinked)


async def _instant(_delay: float) -> None:
    await asyncio.sleep(0)


def _payload(door_id: str) -> dict:
    return door_kitchen() if door_id == "door1" else door_paper()


async def test_parallel_stagger_fills_card_and_frames() -> None:
    events: list[tuple] = []
    clock = {"t": 0.0}

    async def sleep(delay: float) -> None:
        events.append(("sleep", clock["t"], delay))
        clock["t"] += delay
        await asyncio.sleep(0)

    async def llm(prompt: str, *, system: str, door_id: str) -> str:
        events.append(("start", door_id, clock["t"]))
        await asyncio.sleep(0)
        events.append(("end", door_id, clock["t"]))
        assert "Жёсткое правило визуализации" in system
        assert door_id in prompt
        return json.dumps(_payload(door_id), ensure_ascii=False)

    card = default_card()
    card["gg"]["name"] = "Миша"
    card["intro"]["video_name"] = "intro.mp4"
    card["portal"]["image_name"] = "doors.png"
    card["portal"]["video_name"] = "morph.mp4"
    filled, agent = await generate_scenarios(card, llm=llm, sleep=sleep, categories_pick=CATS, outcomes_pick=OUTS)

    assert events[0] == ("start", "door1", 0.0)
    sleep_at = next(i for i, event in enumerate(events) if event[0] == "sleep")
    end_at = next(i for i, event in enumerate(events) if event[0] == "end" and event[1] == "door1")
    assert events[sleep_at] == ("sleep", 0.0, 5.0)
    assert sleep_at < end_at
    assert ("start", "door2", 5.0) in events

    assert filled["intro"]["video_name"] == "intro.mp4"
    assert filled["portal"]["image_name"] == "doors.png"
    assert filled["portal"]["video_name"] == "morph.mp4"
    assert "стеклян" in filled["portal"]["left_design"]
    assert "бумаг" in filled["portal"]["right_design"]
    assert filled["doors"][0]["theme"].startswith("Мокрая кухня")
    assert filled["doors"][1]["theme"].startswith("Мастерская")
    assert "капля" in filled["doors"][0]["theme_note"].lower() or "Каждая" in filled["doors"][0]["theme_note"]
    assert filled["doors"][0]["blocks"][-1]["kind"] == "exit"
    assert "шипами" in filled["doors"][0]["blocks"][-1]["text"]
    assert "Миша" in filled["finale"]["standard"]
    assert "крошки" in filled["finale"]["standard"]
    assert "клювом" in filled["finale"]["standard"]

    assert agent["template_version"] == "v2"
    assert agent["errors"] == []
    assert agent["rewrite_log"] == []
    frames = agent["frames"]
    assert [frame["slot_id"] for frame in frames[:2]] == ["intro", "doors"]
    assert frames[-1]["slot_id"] == "finale"
    for frame in frames:
        assert ag.validate_frame(frame) == []
        assert ag.validate_scene(frame) == []
        assert frame["start_frame"].strip() != frame["end_frame"].strip()
    assert ag.camera_errors(frames) == []
    intro = frames[0]
    assert any(ref.get("role") == "source_video" and ref["attached"] for ref in intro["refs"])
    hooks = [frame for frame in frames if frame["slot_id"].endswith(".hook.0")]
    assert len(hooks) == 2
    assert all(any(ref.get("role") == "illustration" for ref in frame["refs"]) for frame in hooks)
    shared = sum(frame["duration_hint"] for frame in frames if frame["door"] is None)
    door1 = sum(frame["duration_hint"] for frame in frames if frame["door"] == "door1")
    assert abs(shared + door1 - 60) < 1
    assert "Миша" in intro["action"]
    assert "поза" in ag.render_plan(agent)


async def test_regenerates_up_to_three_times() -> None:
    prompts: list[str] = []

    async def llm(prompt: str, *, system: str, door_id: str) -> str:
        if door_id == "door1":
            prompts.append(prompt)
            if len(prompts) < 3:
                bad = door_kitchen()
                bad["law"] = "Герой слышит гул и думает о вечности."
                return json.dumps(bad, ensure_ascii=False)
        return json.dumps(_payload(door_id), ensure_ascii=False)

    filled, agent = await generate_scenarios(default_card(), llm=llm, sleep=_instant, categories_pick=CATS, outcomes_pick=OUTS)
    assert len(prompts) == 3
    retry = next(line for line in prompts[1].splitlines() if line.startswith("- "))
    assert retry.startswith("- visual:")
    assert "visual:sound" in prompts[1]
    assert filled["doors"][0]["theme"].startswith("Мокрая кухня")
    assert agent["rewrite_log"][0]["errors"][0].startswith("visual:")

    calls = {"door1": 0}

    async def always_bad(prompt: str, *, system: str, door_id: str) -> str:
        if door_id == "door1":
            calls["door1"] += 1
            bad = door_kitchen()
            bad["theme"] = "Героя затягивает чёрная дыра и он слышит вой"
            bad["law"] = "Героя затягивает чёрная дыра и он слышит вой."
            return json.dumps(bad, ensure_ascii=False)
        return json.dumps(door_paper(), ensure_ascii=False)

    with pytest.raises(ScenarioGenerationError) as caught:
        await generate_scenarios(default_card(), llm=always_bad, sleep=_instant, categories_pick=CATS, outcomes_pick=OUTS)
    assert calls["door1"] == 3
    assert caught.value.attempts == 3
    assert "Дверь 1" in str(caught.value)


async def test_same_theme_regenerates_second_door() -> None:
    seen = {"door2": 0}

    async def llm(prompt: str, *, system: str, door_id: str) -> str:
        if door_id == "door2":
            seen["door2"] += 1
            if seen["door2"] == 1:
                copied = door_paper()
                copied["theme"] = door_kitchen()["theme"]
                return json.dumps(copied, ensure_ascii=False)
            assert "Мокрая кухня" in prompt
        return json.dumps(_payload(door_id), ensure_ascii=False)

    filled, _agent = await generate_scenarios(default_card(), llm=llm, sleep=_instant, categories_pick=CATS, outcomes_pick=OUTS)
    assert seen["door2"] == 2
    assert filled["doors"][0]["theme"] != filled["doors"][1]["theme"]
    assert filled["doors"][1]["theme"].startswith("Мастерская")


def test_action_verb_check_by_endings() -> None:
    for ok in [
        "Герой переступает порог",
        "Огромная рука ударяет героя в грудь",
        "Герой касается стены ладонью",
        "Герой выдёргивает ногу из щели",
        "Шкаф опрокидывается на героя",
        "Лестница сворачивается кольцом",
    ]:
        assert sc._physical(ok), ok
    for bad in [
        "Герой думает о доме",
        "Герой слышит шаги и вспоминает детство",
        "Герой понимает, что дверь закрыта",
        "Свет и цвет комнаты",
        "Тишина",
    ]:
        assert not sc._physical(bad), bad
    assert sc.nonvisual_verbs("Он чувствует холод") == ["чувствует"]


def test_overused_motif_is_banned() -> None:
    for text in [
        "Капля превращает героя в прозрачный кристалл",
        "Оранжерея из чёрного стекла с бассейнами рассола",
        "Террасный зал, по ступеням течёт смола",
        "От капли воды герой превращается в стекло",
    ]:
        assert "stoplist:overused_petrify" in stoplist_errors(text), text


def test_iconic_world_framing_in_prompt() -> None:
    prompt = build_system_prompt()
    assert "природа" in prompt.lower()
    assert "law" in prompt
    assert "digital" in prompt and "flat_2d" in prompt
    assert "ЦЕЛАЯ РЕАЛЬНОСТЬ" in prompt
    assert visualization_errors("Герой кричит, рот широко открыт") == []


def test_category_diversity_and_history(tmp_path) -> None:
    path = tmp_path / "h.json"
    picked: list[str] = []
    for _ in range(3):
        pair = sc.pick_categories(2, path=path)
        assert pair[0] != pair[1]
        picked += pair
    assert len(set(picked)) == 6
    assert all(cid in sc.category_ids() for cid in picked)

    doc = door_kitchen()
    assert validate_door_scenario(doc, category="absolute_zero") == []
    assert "category_mismatch" in validate_door_scenario(doc, category="machines")
    bad = door_kitchen()
    bad["category"] = "nope"
    assert "category_invalid" in validate_door_scenario(bad)
    recent = [{"theme": doc["theme"], "mechanic": "что-то другое совсем"}]
    assert "theme_repeat" in validate_door_scenario(door_kitchen(), recent=recent)
    assert "mechanic_same" in validate_door_scenario(
        door_kitchen(), other_mechanic="капли растут в стеклянные шипы"
    )


async def test_history_recorded_and_used_in_prompt(tmp_path) -> None:
    path = tmp_path / "h.json"
    prompts: list[str] = []

    async def llm(prompt: str, *, system: str, door_id: str) -> str:
        prompts.append(prompt)
        assert "КАТЕГОРИИ МИРОВ" in system
        return json.dumps(_payload(door_id), ensure_ascii=False)

    await generate_scenarios(default_card(), llm=llm, sleep=_instant, categories_pick=CATS, outcomes_pick=OUTS, history_path=path)
    hist = sc.load_history(path)
    assert [item["category"] for item in hist] == CATS
    assert hist[0]["theme"].startswith("Мокрая кухня")
    prompts.clear()
    with pytest.raises(ScenarioGenerationError):
        await generate_scenarios(default_card(), llm=llm, sleep=_instant, categories_pick=CATS, outcomes_pick=OUTS, history_path=path)
    assert any("Недавние миры" in p and "Мокрая кухня" in p for p in prompts)
    assert any("Категория мира строго: absolute_zero" in p for p in prompts)


def test_finale_follows_outcomes() -> None:
    assert sc.finale_line_errors("death", "Герой стоит в комнате невредимый") == ["finale_mismatch"]
    assert sc.finale_line_errors("death", "У порога лежит обгоревший ботинок") == []
    assert sc.finale_line_errors("death", "Герой не вернулся; у порога лежит только ботинок") == []
    assert sc.finale_line_errors("escape", "Герой погиб") == ["finale_mismatch"]
    dead = door_paper()
    dead["outcome"] = "death"
    dead["finale_line"] = "Герой вернулся в комнату целым"
    assert "finale_mismatch" in validate_door_scenario(dead)

    dead["finale_line"] = "У порога лежит смятый бумажный клюв"
    fin = sc.build_finale("Миша", door_kitchen(), dead)
    text = fin["standard"]
    assert "Правая дверь — Миша не вернулся" in text and "Левая дверь — Миша спасся" in text
    assert text.count("Миша спасся") == 1
    dead2 = dict(dead, finale_line="Миша не вернулся; у порога лежит клюв")
    assert sc.build_finale("Миша", dead2, dead2)["standard"].count("не вернулся") == 2
    both = sc.build_finale("Миша", dead, dead)["standard"]
    assert "Комната пуста" in both
    assert "стоит в комнате" not in both
    frames = sc.build_frames(
        {"gg": {"name": "Миша"}, "intro": {}, "portal": {}, "finale": {"standard": both}},
        {"door1": dead, "door2": dead},
    )
    fin_frame = frames[-1]
    assert "пустой" in fin_frame["action"] and "вываливается" not in fin_frame["action"]


def test_repair_fixes_format_without_retry() -> None:
    doc = door_kitchen()
    doc["beats"] = list(reversed(doc["beats"]))
    for beat in doc["beats"]:
        for frame in beat["frames"]:
            frame["camera"] = "close-up"
            frame["camera_feel"] = "shaky"
    fixes = sc.repair_door_doc(doc)
    assert "beats_reordered" in fixes and "camera_repaired" in fixes and "feel_repaired" in fixes
    assert [b["id"] for b in doc["beats"]] == ["entry", "law", "threat", "peak", "outcome", "hook"]
    assert validate_door_scenario(doc) == []
    wide = door_kitchen()
    wide["beats"][0]["frames"][0]["camera"] = "Wide"
    sc.repair_door_doc(wide)
    assert wide["beats"][0]["frames"][0]["camera"] == "far"


def test_twist_required_and_microscale_dropped() -> None:
    doc = door_kitchen()
    doc["twist"] = ""
    assert "twist_missing" in validate_door_scenario(doc)
    assert "microscale" not in sc.category_ids()
    assert "ПОВОРОТ" in build_system_prompt()


def test_icon_repeat_blocked() -> None:
    doc = door_kitchen()
    doc["icon"] = "в желудке с кислотой"
    recent = [{"theme": "другое", "mechanic": "x", "category": "body_inside", "icon": "в желудке с кислотой"}]
    assert "theme_repeat" in validate_door_scenario(doc, recent=recent)
    doc["icon"] = "в лёгком на вдохе"
    assert "theme_repeat" not in validate_door_scenario(doc, recent=recent)


def test_icon_library_and_outcome_plan() -> None:
    import random
    lib = sc.icon_library()
    assert sum(len(v) for v in lib.values()) >= 60
    assert set(lib) == set(sc.category_ids())
    names = " ".join(it["name"] for v in lib.values() for it in v)
    assert stoplist_errors(names) == []
    assert "Помпе" not in names
    rng = random.Random(1)
    counts = {"death": 0, "escape": 0, "transformation": 0}
    for _ in range(600):
        cid = rng.choice(sc.category_ids())
        out, cands = sc.plan_door(cid, [], rng=rng)
        counts[out] += 1
        assert cands and all(out in next(i for i in lib[cid] if i["name"] == c)["outcomes"] for c in cands)
    assert all(v > 60 for v in counts.values())
    recent = [{"theme": "x", "icon": "внутри старой 8-битной видеоигры", "mechanic": "", "category": "digital"}]
    for _ in range(30):
        _o, cands = sc.plan_door("digital", recent, rng=rng)
        assert "внутри старой 8-битной видеоигры" not in cands
    o1, _ = sc.plan_door("dream", [], rng=rng)
    o2, _ = sc.plan_door("dream", [], avoid_outcome=o1, rng=rng)
    assert o1 != o2


def test_creature_attack_banned() -> None:
    assert sc.creature_attack("Огромная пасть хищника смыкается на герое")
    assert not sc.creature_attack("Кожа героя превращается в пиксели и осыпается квадратами")
    doc = door_paper()
    doc["beats"][3]["text"] = "Чудовище бросается на героя и кусает его за руку, пока листы складываются."
    assert "creature_attack" in validate_door_scenario(doc)


async def test_provider_retry_does_not_burn_attempts(monkeypatch) -> None:
    monkeypatch.setattr(sc, "PROVIDER_RETRY_S", 0.0)
    calls = {"n": 0}

    async def flaky(prompt: str, *, system: str, door_id: str) -> str:
        calls["n"] += 1
        if door_id == "door1" and calls["n"] <= 2:
            raise RuntimeError("upstream 502")
        return json.dumps(_payload(door_id), ensure_ascii=False)

    filled, agent = await generate_scenarios(default_card(), llm=flaky, sleep=_instant, categories_pick=CATS, outcomes_pick=OUTS)
    assert agent["rewrite_log"] == []


def test_self_score_floor_and_pair_balance() -> None:
    import random
    doc = door_kitchen()
    doc["self_scores"] = {"emotion": 7, "interest": 9, "stakes": 8, "concept": 8}
    assert "self_score_low:emotion" in validate_door_scenario(doc)
    del doc["self_scores"]
    assert "self_score_missing" in validate_door_scenario(doc)
    rng = random.Random(3)
    for _ in range(200):
        c1, c2 = rng.sample(sc.category_ids(), 2)
        o1, _ = sc.plan_door(c1, [], rng=rng)
        o2, _ = sc.plan_door(c2, [], avoid_outcome=o1, rng=rng)
        assert {o1, o2} & {"death", "transformation"}
    for cid in ("no_light", "tv_screen", "atom_scale"):
        assert cid not in sc.category_ids()
    assert "snow_globe" in sc.category_ids() and "heavy_gravity" in sc.category_ids()


def test_v10_judge_escape_cooldown() -> None:
    import asyncio, random
    doc = door_kitchen()
    doc["outcome"] = "escape"
    for b in doc["beats"]:
        if b["id"] == "outcome":
            b["text"] = "Герой хватается за раму и вываливается в комнату."
    assert "escape_generic" in validate_door_scenario(doc)
    doc = door_kitchen()
    doc["outcome"] = "transformation"
    doc["outcome_process"] = "Тело героя становится полупрозрачным сиянием из света."
    assert "transform_not_solid" in validate_door_scenario(doc)
    doc = door_kitchen()
    doc["outcome_process"] = "Абракадабра случается с ним окончательно навсегда."
    assert "outcome_linked_to_law" in sc.repair_door_doc(doc)

    async def fake(prompt, *, system, door_id):
        return '{"family":"cold","active_struggle":false,"world_trick_escape":false}'
    v = asyncio.run(sc.judge_door(fake, door_kitchen(), "door1"))
    assert v["family"] == "cold"
    assert sc.judge_errors(v, "death", []) == ["death_passive"]
    assert sc.judge_errors(v, "escape", [{"family": "cold"}]) == ["escape_generic", "family_repeat:cold"]
    hist = [{"category": "mirror_world", "theme": "x"}] + [{"category": "moon", "theme": "y"}] * 3
    path = __import__('pathlib').Path(__file__).parent / "_tmp_cool.json"
    path.write_text(__import__("json").dumps(hist), encoding="utf-8")
    try:
        for _ in range(20):
            assert "mirror_world" not in sc.pick_categories(2, path=path, rng=random.Random(_))
            path.write_text(__import__("json").dumps(hist), encoding="utf-8")
    finally:
        path.unlink()


async def test_two_stage_plan_then_storyboard_mocked(tmp_path) -> None:
    """Этап 1 — план, этап 2 — раскадровка строго по плану. Только мок, без платных вызовов."""
    from app.services.nii67_card import normalize_card, plan_ready
    from app.services.nii67_scenario import generate_plan

    calls: list[tuple[str, str]] = []

    async def plan_llm(prompt: str, *, system: str, door_id: str) -> str:
        calls.append((door_id, prompt))
        return json.dumps({"world": f"Мир {door_id}", "happens": "Герой видит мир, мир меняет его тело, поворот, пик.", "outcome": "escape"}, ensure_ascii=False)

    card = default_card()
    plan = await generate_plan(card, llm=plan_llm, categories_pick=CATS, outcomes_pick=["death", "transformation"], history_path=tmp_path / "h.json")
    assert {c[0] for c in calls} == {"door1-plan", "door2-plan"}
    assert plan["door1"]["outcome"] == "death" and plan["door2"]["outcome"] == "transformation"
    assert plan["door1"]["category"] == CATS[0] and plan_ready(plan) and plan["finale"]

    # оператор правит план руками
    plan["door1"]["world"] = "ОПЕРАТОРСКИЙ МИР: кухня-ледник"
    card["plan"] = plan
    card = normalize_card(card)
    assert card["plan"]["door1"]["world"] == "ОПЕРАТОРСКИЙ МИР: кухня-ледник"

    prompts: dict[str, str] = {}

    async def sb_llm(prompt: str, *, system: str, door_id: str) -> str:
        prompts[door_id] = prompt
        assert not door_id.endswith("-judge")
        return json.dumps(_payload(door_id), ensure_ascii=False)

    async def sleep(_: float) -> None:
        return None

    card["plan"]["door1"]["outcome"] = OUTS[0]
    card["plan"]["door2"]["outcome"] = OUTS[1]
    filled, agent = await generate_scenarios(card, llm=sb_llm, sleep=sleep, plan=card["plan"], history_path=tmp_path / "h.json")
    assert "УТВЕРЖДЁННЫЙ ПЛАН" in prompts["door1"] and "ОПЕРАТОРСКИЙ МИР" in prompts["door1"]
    assert "Недавние миры" not in prompts["door1"]
    assert filled["plan"]["door1"]["world"].startswith("ОПЕРАТОРСКИЙ")
    assert filled["finale"]["notes"] == card["plan"]["finale"]
    assert agent["frames"]
    assert set(prompts) == {"door1", "door2"}
