"""Реалистичный сид (scripts/eval_seed.py) и контекст планировщика: что видит модель."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest_asyncio
from sqlalchemy import func, select

from app.models import Frame
from app.services import prompt_library
from app.services.action_layer import planner, planner_context as pc, planner_rules as rules
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env  # noqa: F401

FIXTURE = Path(__file__).parent / "fixtures" / "action_planner_eval.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]


def load_seed():
    spec = importlib.util.spec_from_file_location(
        "eval_seed", Path(__file__).parent.parent / "scripts" / "eval_seed.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest_asyncio.fixture
async def seeded(env, tmp_path, monkeypatch):  # noqa: F811
    ensure_builtin_actions()
    monkeypatch.setattr(prompt_library, "PROMPTS_ROOT", prompt_library.PROMPTS_ROOT)
    info = await load_seed().seed_realistic(project_id=2, factory=env.factory,
                                            prompts_dir=tmp_path / "prompts")
    info.env = env
    return info


def prompt_ctx(facts: dict, text: str, n: int = 24) -> dict:
    msgs, _info = planner.build_prompt(text, {"facts": facts, "version": "v"}, registry, 20, n)
    return json.loads(msgs[1]["content"])["project_context"]


def seeded_target(info) -> str:
    return next(p["target"] for p in info.proposals if p["tier"] == "T2")


async def test_seed_fills_every_fact_the_model_sees(seeded) -> None:
    f = (await planner.load_context(seeded.project_id))["facts"]
    assert f["frame_count"] == 30 and f["max_frame_number"] == 30 and f["status"] == "frames_ready"
    rows = {r[0]: r for r in f["frames"]}
    assert len(rows) == 30 and rows[7][1] == seeded.frame_ids[7] != 7  # frame_id != номер
    assert rows[1][2] == "p" and rows[2][2] == "c" and rows[4][2] == "p"   # сцены 1-3 и 4-5
    assert rows[7][3:] == [1, "1", "1"]                                      # один шот, картинка и видео
    assert rows[4][3:] == [2, "12", "12"]                                    # два шота с файлами
    assert rows[14][4:] == ["-", "-"] and rows[25][4:] == ["-", "-"]        # без файлов
    assert f["queue"]["image"] == 3 and f["queue"]["video"] == 1
    assert [3, "image_regen", 1] in f["queue"]["ops"]
    kinds = {n["id"]: n.get("kind") for n in f["nodes"]}
    assert kinds["n_excel_gpt_fw_shots"] == "shots" and kinds["n_excel_gpt_fw_report"] == "report"
    assert {n["id"]: n.get("prompt") for n in f["nodes"]}["n_excel_gpt_fw_shots"] == "scenes_to_frames_ru"
    assert [p["n"] for p in f["proposals"]] == [1, 2, 3, 4, 5]
    assert {p["n"]: p["status"] for p in f["proposals"]}[3] == "proposed"
    assert all(len(p["id"]) == 32 and len(p["digest"]) == 64 for p in f["proposals"])
    assert {v["target"]: v["v"] for v in f["versions"]}[seeded_target(seeded)] == 3
    d = f["defaults"]
    assert d["image_resolution"] == "2k" and d["video_generator"] and d["image_generator"]
    assert d["step_code"] == "split" and d["prompt_variant"] == "default" and d["slot_id"] == "main"
    assert f["prompt_overrides"] == {"script": "main", "excel_gpt": "main"}


async def test_seed_writes_files_and_prompt_variants(seeded) -> None:
    imgs, vids = pc.media_presence(seeded.data_dir)
    assert imgs[7] == {1} and imgs[4] == {1, 2} and 14 not in imgs and 25 not in imgs
    assert vids[7] == {1} and vids[4] == {1, 2} and 13 not in vids
    names = prompt_library.list_prompts("script")
    assert "main" in names and "draft" in names
    assert "draft" not in prompt_library.list_prompts("excel_gpt")  # draft только у шага script
    assert seeded.prompts_root == prompt_library.PROMPTS_ROOT


async def test_seed_is_repeatable_and_matches_the_fixture_variables(seeded) -> None:
    again = await load_seed().seed_realistic(project_id=2, factory=seeded.env.factory,
                                             prompts_dir=seeded.prompts_root)
    assert again.frame_ids == seeded.frame_ids
    async with seeded.env.factory() as s:
        assert await s.scalar(select(func.count(Frame.id)).where(Frame.project_id == 2)) == 30
    v = load_seed().expected_vars(2)
    assert v["frame.22"] == seeded.frame_ids[22]
    for p in seeded.proposals:
        assert v[f"proposal.{p['n']}.id"] == p["id"] and v[f"proposal.{p['n']}.digest"] == p["digest"]
    assert v["target.shots_prompt"] == seeded_target(seeded) and v["version.shots_prompt"] == 3


def test_fixture_placeholders_resolve_and_ids_exist_in_the_seed() -> None:
    v = load_seed().expected_vars(2)
    ids = {v[f"frame.{n}"] for n in range(1, 31)}

    def walk(node, case_id):
        if isinstance(node, str) and node.startswith("$"):
            assert node[1:] in v, (case_id, node)
        elif isinstance(node, dict):
            for k, val in node.items():
                if k.endswith("frame_id") and isinstance(val, int):
                    assert val in ids, (case_id, k, val)
                walk(val, case_id)
        elif isinstance(node, list):
            for item in node:
                walk(item, case_id)

    for c in CASES:
        walk(c["steps"], c["id"])


async def test_selected_frame_is_resolved_by_board_id_or_number(seeded) -> None:
    by_id = (await planner.load_context(2, selected_frame=seeded.frame_ids[5]))["facts"]["selected"]
    by_no = (await planner.load_context(2, selected_frame=5))["facts"]["selected"]
    assert by_id == by_no == {"number": 5, "frame_id": seeded.frame_ids[5],
                              "scene_head_id": seeded.frame_ids[4]}
    assert "selected" not in (await planner.load_context(2, selected_frame=999999))["facts"]
    assert "selected" not in (await planner.load_context(2))["facts"]


async def test_context_version_ignores_files_queue_and_proposals_but_not_structure(seeded) -> None:
    before = (await planner.load_context(2))["version"]
    (seeded.data_dir / "scenes" / "frame_025_seed.png").write_bytes(b"x")  # картинка появилась
    assert (await planner.load_context(2))["version"] == before
    async with seeded.env.factory() as s:
        s.add(Frame(project_id=2, number=31, voiceover_text="н", attrs={}))
        await s.commit()
    assert (await planner.load_context(2))["version"] != before


async def test_prompt_context_contains_only_what_the_offered_actions_need(seeded) -> None:
    facts = (await planner.load_context(2, selected_frame=5))["facts"]
    board = pc.prompt_context(facts, ["montage.get_board", "project.stop"], registry,
                              "покажи состояние доски монтажа")
    assert set(board) <= {"project_id", "selected_scene", "selected_frame", "status", "frame_count",
                          "frames", "selected"}
    assert "proposals" not in board and "queue" not in board and "nodes" not in board
    queue = prompt_ctx(facts, "убери из очереди правки кадра 3")
    assert queue["queue"]["ops"] and "proposals" not in queue and isinstance(queue["frames"], str)
    assert queue["frames"].startswith("1,") and ";4," in queue["frames"]
    change = prompt_ctx(facts, "примени предложение 3")
    assert [p["n"] for p in change["proposals"]][-1] == 5 and change["versions"] and change["nodes"]
    assert all("type" not in n for n in change["nodes"])
    gen = prompt_ctx(facts, "сгенерируй картинки для кадров 3, 4 и 5")
    assert "image_generator" not in gen.get("defaults", {}) and "queue" not in gen  # генератор, разрешение и лимиты сервер подставляет сам
    prompts = prompt_ctx(facts, "удали вариант мастер-промта draft")
    assert "step_code" not in prompts.get("defaults", {})  # шаг промта берётся из слов запроса, а не из статуса проекта
    assert prompts["prompts"]["script"]["sel"] == "main" and "draft" in prompts["prompts"]["script"]["names"]
    assert "draft" not in prompts["prompts"].get("excel_gpt", {}).get("names", [])
    reset = pc.prompt_context(facts, ["node.reset_from_step"], registry, "сбрось шаг")
    assert reset["defaults"] == {"step_code": "split"}
    only_apply = pc.prompt_context(facts, ["change.apply"], registry, "примени")
    assert "proposals" in only_apply and "versions" not in only_apply and "nodes" not in only_apply
    only_rollback = pc.prompt_context(facts, ["change.rollback"], registry, "откати")
    assert "versions" in only_rollback and "proposals" not in only_rollback
    msgs, _i = planner.build_prompt("хочу задать громкость музыки", {"facts": facts, "version": "v"},
                                    registry, 20, 24)
    notes = json.loads(msgs[1]["content"]).get("notes", {})
    assert "montage.set_assemble_params" in notes and all(k in rules.ACTION_NOTES or k == "upload.*" for k in notes)
    shot = prompt_ctx(facts, "в меню съёмки поставь закадр ячейки кадра 4: «Новый текст»")
    assert set(shot["frame_uuids"]) == {"4", "5"}  # названный и выбранный кадр, не все 30


def test_frame_rows_window_is_bounded_and_centered() -> None:
    frames = [(n, 100 + n, {}, None) for n in range(1, 201)]
    rows, note = pc.frame_rows(frames, {}, {}, selected=150)
    assert len(rows) == pc.MAX_FRAMES and "из 200" in note and any(r[0] == 150 for r in rows)
    rows, note = pc.frame_rows(frames[:10], {}, {}, selected=3)
    assert len(rows) == 10 and note == ""
    assert pc.scene_of([[1, 1, "p"], [2, 2, "c"], [3, 3, "p"]], 2) == [[1, 1, "p"], [2, 2, "c"]]
    assert pc.scene_of([[1, 1, "p"]], 9) == [] and pc.scene_of([], None) == []


def test_prompt_stays_small() -> None:
    assert len(planner.SYSTEM) < 3600  # правила занимают не больше ~1 тыс. токенов
    ensure_builtin_actions()
    facts = {"project_id": 2, "selected_scene": None, "selected_frame": None, "status": "idle",
             "frame_count": 30, "nodes": [], "queue": {"image": 0, "video": 0, "ops": []},
             "frames": [[n, 1200 + n, "p", 1, "1", "1"] for n in range(1, 31)], "defaults": {}}
    sizes = []
    for c in CASES:
        _m, info = planner.build_prompt(c["text"], {"facts": facts, "version": "v"}, registry, 20, 24)
        sizes.append(info["chars"])
    assert sum(sizes) / len(sizes) < 7500 and max(sizes) < 12500
