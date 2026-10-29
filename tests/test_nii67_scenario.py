"""Автосценарий NII 67: стоп-лист, визуализация, биты и параллельный старт."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.services import nii67_agent as ag
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
    unlinked["outcome_process"] = "Герой просто уходит в соседнюю комнату без следа на теле"
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
    filled, agent = await generate_scenarios(card, llm=llm, sleep=sleep)

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

    filled, agent = await generate_scenarios(default_card(), llm=llm, sleep=_instant)
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
        await generate_scenarios(default_card(), llm=always_bad, sleep=_instant)
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

    filled, _agent = await generate_scenarios(default_card(), llm=llm, sleep=_instant)
    assert seen["door2"] == 2
    assert filled["doors"][0]["theme"] != filled["doors"][1]["theme"]
    assert filled["doors"][1]["theme"].startswith("Мастерская")
