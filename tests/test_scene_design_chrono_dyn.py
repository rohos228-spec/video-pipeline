"""chrono_dyn: волны агентов, required_shots, варианты промптов."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.node_groups import NODE_GROUPS, get_node_group
from app.services.scene_design import agents as ag
from app.services.scene_design.camera_expand import required_shots_for_beat


def test_chrono_dyn_group_in_palette() -> None:
    g = get_node_group("scene_design_fanout_chrono_dyn")
    assert g is not None
    assert g.group_id in NODE_GROUPS
    variants = {n.prompt_variant for n in g.nodes if n.prompt_variant}
    assert "sd_skeleton" in variants
    assert "sd_action_chrono_dyn" in variants
    assert "sd_camera_chrono_dyn" in variants
    assert "sd_assemble_chrono_dyn" in variants
    assert g.project_meta.get("scene_design_variant") == "chrono_dyn"
    assert g.project_meta.get("scene_design_skeleton") is True
    assert g.entry_keys == ("skeleton",)
    assert g.exit_key == "assemble"
    assert all(n.operator_config is None for n in g.nodes)
    assert len(g.nodes) == 7
    edge_pairs = {(a, b) for a, b, _k in g.internal_edges}
    assert ("skeleton", "characters") in edge_pairs
    assert ("skeleton", "world") in edge_pairs
    assert ("skeleton", "style") in edge_pairs
    assert ("action", "camera") in edge_pairs
    assert ("camera", "assemble") in edge_pairs
    assert not any(a.startswith("check_") or b.startswith("check_") for a, b in edge_pairs)


def test_uses_chrono_dyn_from_meta() -> None:
    p = SimpleNamespace(meta={"scene_design_variant": "chrono_dyn"})
    assert ag.uses_chrono_dyn(p) is True
    assert ag.uses_chrono_dyn(SimpleNamespace(meta={})) is False


def test_skeleton_wave0_when_flag() -> None:
    p = SimpleNamespace(
        meta={"scene_design_variant": "chrono_dyn", "scene_design_skeleton": True}
    )
    assert ag.uses_skeleton(p) is True
    waves = ag.agent_waves(p)
    assert waves[0] == ("skeleton",)
    assert "action" in waves[2]
    assert waves[-1] == ("camera",)


def test_validate_chrono_dyn_action_rejects_flat_two_phases() -> None:
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "action": "crowd goes",
                    "subject": "crowd",
                    "orientation": "side",
                    "info_change": "a",
                },
                {
                    "phase_index": 2,
                    "action": "door stays",
                    "subject": "prop",
                    "orientation": "object",
                    "info_change": "b",
                },
            ],
        }
        for i in range(1, 8)
    ]
    with pytest.raises(ag.SceneDesignAgentError, match="плотности"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_validate_chrono_dyn_action_accepts_dense_cnn() -> None:
    beats = ("setup", "develop", "develop", "turn", "payoff")
    acts = (
        "Санитар усаживает Александра на кушетку",
        "Врач щёлкает фонариком у его глаз",
        "Людмила перехватывает запястье сына",
        "Участковый захлопывает блокнот",
        "Александр вырывает руку и прячет лицо в ладони",
    )
    locs = ("loc03", "loc06", "loc07", "loc04", "loc02")
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "location": locs[i - 1],
            "время_сек": 12.0,
            "связь_с_прошлой": "продолжение прошлого бита" if i > 1 else "",
            "крючок_в_следующую": "открытый жест в следующую сцену",
            "цепь_действия": [
                {
                    "phase_index": p,
                    "beat": beats[p - 1],
                    "action": acts[p - 1],
                    "subject": f"c0{(p % 3) + 1}",
                    "orientation": "face",
                    "переход_к_следующей": "cut_on_action",
                }
                for p in range(1, 6)
            ],
        }
        for i in range(1, 6)
    ]
    ag.validate_chrono_dyn_action_scenes(scenes)


def test_validate_chrono_dyn_action_rejects_unlinked_scenes() -> None:
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "цепь_действия": [
                {
                    "phase_index": p,
                    "beat": (
                        "setup"
                        if p == 1
                        else ("payoff" if p == 5 else "develop")
                    ),
                    "action": f"одно действие {p}",
                    "subject": "c01",
                    "orientation": "face",
                    "переход_к_следующей": "cut",
                }
                for p in range(1, 6)
            ],
        }
        for i in range(1, 6)
    ]
    with pytest.raises(ag.SceneDesignAgentError, match="связь_с_прошлой|крючок"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_validate_chrono_dyn_rejects_compound_actions() -> None:
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "связь_с_прошлой": "было" if i > 1 else "",
            "крючок_в_следующую": "дальше",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "beat": "setup",
                    "action": (
                        "Соседи встречаются у лифта, забирают письма "
                        "из ящиков и проходят мимо двери 357"
                    ),
                    "subject": "c01",
                    "orientation": "side",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 2,
                    "beat": "develop",
                    "action": "Александр морщится от запаха",
                    "subject": "c01",
                    "orientation": "face",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 3,
                    "beat": "develop",
                    "action": "Он стоит у двери",
                    "subject": "c01",
                    "orientation": "side",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 4,
                    "beat": "turn",
                    "action": "Людмила смотрит на дверь",
                    "subject": "c02",
                    "orientation": "face",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 5,
                    "beat": "payoff",
                    "action": "Александр оборачивается",
                    "subject": "c01",
                    "orientation": "face",
                    "переход_к_следующей": "sound_bridge",
                },
            ],
        }
        for i in range(1, 6)
    ]
    with pytest.raises(ag.SceneDesignAgentError, match="нескольк|действи"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_validate_chrono_dyn_rejects_metaphor_folder_slop() -> None:
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "время_сек": 8.0,
            "связь_с_прошлой": "продолжение" if i > 1 else "",
            "крючок_в_следующую": "дальше",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "beat": "setup",
                    "action": "Александр вытягивает руки к трём папкам на столе",
                    "subject": "c01",
                    "orientation": "object",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 2,
                    "beat": "develop",
                    "action": "Александр роняет руки между медицинской картой и папкой",
                    "subject": "c01",
                    "orientation": "object",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 3,
                    "beat": "payoff",
                    "action": "Александр прижимает карточку к груди больничной пижамы",
                    "subject": "c01",
                    "orientation": "object",
                    "переход_к_следующей": "cut_on_action",
                },
            ],
        }
        for i in range(1, 5)
    ]
    with pytest.raises(ag.SceneDesignAgentError, match="метафор|нейрослоп|папк"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def _juicy_phase(p: int, *, action: str, beat: str = "develop") -> dict:
    return {
        "phase_index": p,
        "beat": beat,
        "action": action,
        "subject": "c01",
        "orientation": "side",
        "переход_к_следующей": "cut_on_action",
    }


def test_validate_chrono_dyn_rejects_door_conveyor() -> None:
    """V11: слишком много фаз про дверь/засов (полный срез ≥40 фаз)."""
    scenes = []
    for i in range(1, 16):
        scenes.append(
            {
                "id_scene": f"scene_{i:02d}",
                "location": "loc01",
                "время_сек": 9.0,
                "связь_с_прошлой": "продолжение" if i > 1 else "",
                "крючок_в_следующую": "дальше",
                "цепь_действия": [
                    _juicy_phase(1, action="Людмила захлопывает дверь квартиры № 357", beat="setup"),
                    _juicy_phase(2, action="Людмила задвигает второй дверной засов", beat="develop"),
                    _juicy_phase(3, action="Соседка суёт заявление в щель под дверью", beat="payoff"),
                ],
            }
        )
    with pytest.raises(ag.SceneDesignAgentError, match="двер|засов|конвейер"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_validate_chrono_dyn_rejects_all_static_camera() -> None:
    shots = [
        {
            "id_scene": "scene_01",
            "phase_index": i,
            "крупность": "средний план",
            "движение": "static",
            "мотив": "просто так",
        }
        for i in range(1, 21)
    ]
    with pytest.raises(ag.SceneDesignAgentError, match="static|камер"):
        ag.validate_chrono_dyn_camera_shots(shots)


def test_validate_chrono_dyn_rejects_soy_locomotion() -> None:
    """V10: «проходит мимо / лестница / отводит взгляд» — брак."""
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "время_сек": 9.0,
            "связь_с_прошлой": "продолжение" if i > 1 else "",
            "крючок_в_следующую": "дальше",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "beat": "setup",
                    "action": "Лидия проходит мимо двери квартиры № 357 с сумкой",
                    "subject": "c01",
                    "orientation": "side",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 2,
                    "beat": "develop",
                    "action": "Лидия поднимается по лестнице к квартире",
                    "subject": "c01",
                    "orientation": "back",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 3,
                    "beat": "payoff",
                    "action": "Лидия отводит взгляд от двери, когда открывается лифт",
                    "subject": "c01",
                    "orientation": "side",
                    "переход_к_следующей": "sound_bridge",
                },
            ],
        }
        for i in range(1, 5)
    ]
    # 4×3 = 12 soy / 12 phases = 100% ≥ 8%
    with pytest.raises(ag.SceneDesignAgentError, match="соя|проходит мимо|лестница"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_validate_chrono_dyn_rejects_passive_standing() -> None:
    scenes = [
        {
            "id_scene": f"scene_{i:02d}",
            "связь_с_прошлой": "продолжение" if i > 1 else "",
            "крючок_в_следующую": "дальше",
            "цепь_действия": [
                {
                    "phase_index": p,
                    "beat": (
                        "setup"
                        if p == 1
                        else ("payoff" if p == 5 else "develop")
                    ),
                    "action": (
                        "стоит у двери"
                        if p <= 2
                        else f"Александр делает жест {p}"
                    ),
                    "subject": "c01",
                    "orientation": "face",
                    "переход_к_следующей": "cut_on_action",
                }
                for p in range(1, 6)
            ],
        }
        for i in range(1, 4)
    ]
    # 3 scenes × 2 passive = 6/15 = 40% ≥ 8%
    with pytest.raises(ag.SceneDesignAgentError, match="соя|пассив|стоит|смотрит"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_validate_chrono_dyn_rejects_year_jumps_inside_scene() -> None:
    scenes = [
        {
            "id_scene": "scene_01",
            "связь_с_прошлой": "",
            "крючок_в_следующую": "дальше",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "beat": "setup",
                    "action": "В 1992 году Александр входит в отделение",
                    "subject": "c01",
                    "orientation": "side",
                    "info_change": "a",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 2,
                    "beat": "develop",
                    "action": "В 1995 году он выходит с сумкой",
                    "subject": "c01",
                    "orientation": "side",
                    "info_change": "b",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 3,
                    "beat": "payoff",
                    "action": "Папка закрыта на столе",
                    "subject": "c01",
                    "orientation": "object",
                    "info_change": "c",
                    "переход_к_следующей": "sound_bridge",
                },
            ],
        }
    ]
    with pytest.raises(ag.SceneDesignAgentError, match="год"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_normalize_chrono_dyn_phase_budget_merges_thin_phases() -> None:
    scenes = [
        {
            "id_scene": "scene_01",
            "время_сек": 3.9,
            "связь_с_прошлой": "",
            "крючок_в_следующую": "дальше",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "beat": "setup",
                    "action": "Судья поднимает взгляд от папки дела",
                    "subject": "c01",
                    "orientation": "side",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 2,
                    "beat": "payoff",
                    "action": "Прокурор закрывает блокнот хлопком",
                    "subject": "c02",
                    "orientation": "side",
                    "переход_к_следующей": "sound_bridge",
                },
            ],
        }
    ]
    fixed = ag.normalize_chrono_dyn_phase_budget(scenes)
    assert len(fixed) == 1
    assert len(fixed[0]["цепь_действия"]) == 1
    assert fixed[0]["цепь_действия"][0]["beat"] == "payoff"


def test_repair_chrono_dyn_year_jumps_splits_scene() -> None:
    scenes = [
        {
            "id_scene": "scene_01",
            "время_сек": 9.0,
            "связь_с_прошлой": "",
            "крючок_в_следующую": "дальше",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "beat": "setup",
                    "action": "В 1992 году Александр входит в отделение",
                    "subject": "c01",
                    "orientation": "side",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 2,
                    "beat": "develop",
                    "action": "В 1995 году он выходит с сумкой",
                    "subject": "c01",
                    "orientation": "side",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 3,
                    "beat": "payoff",
                    "action": "Закрывает дверь подъезда",
                    "subject": "c01",
                    "orientation": "side",
                    "переход_к_следующей": "sound_bridge",
                },
            ],
        }
    ]
    fixed = ag.repair_chrono_dyn_year_jumps(scenes)
    assert len(fixed) == 2
    assert len(fixed[0]["цепь_действия"]) == 1
    assert len(fixed[1]["цепь_действия"]) == 2
    ag.validate_chrono_dyn_action_scenes(fixed)


def test_validate_chrono_dyn_rejects_location_collage() -> None:
    scenes = [
        {
            "id_scene": "scene_01",
            "связь_с_прошлой": "",
            "крючок_в_следующую": "дальше",
            "цепь_действия": [
                {
                    "phase_index": 1,
                    "beat": "setup",
                    "action": "Врач в больнице ставит диагноз",
                    "subject": "c01",
                    "orientation": "side",
                    "info_change": "a",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 2,
                    "beat": "develop",
                    "action": "На кухне квартиры раскладывают справки",
                    "subject": "c02",
                    "orientation": "object",
                    "info_change": "b",
                    "переход_к_следующей": "cut_on_action",
                },
                {
                    "phase_index": 3,
                    "beat": "payoff",
                    "action": "В милиции ставят штамп на карточку",
                    "subject": "c08",
                    "orientation": "object",
                    "info_change": "c",
                    "переход_к_следующей": "sound_bridge",
                },
            ],
        }
    ]
    with pytest.raises(ag.SceneDesignAgentError, match="локац"):
        ag.validate_chrono_dyn_action_scenes(scenes)


def test_required_shots_chrono_dyn_uses_phases() -> None:
    n = required_shots_for_beat(
        {"крупность": "MS", "набор": "SET_01"},
        {"SET_01": (3, 5)},
        duration_sec=12.0,
        chrono_dyn=True,
        action_phases=4,
    )
    assert n == 4


def test_required_shots_legacy_forces_ladder() -> None:
    n = required_shots_for_beat(
        {"крупность": "MS", "набор": ""},
        {},
        duration_sec=12.0,
        chrono_dyn=False,
        action_phases=0,
    )
    assert n >= 3


@pytest.mark.asyncio
async def test_run_category_agents_camera_after_action() -> None:
    from app.services.scene_design import runner

    order: list[str] = []

    async def fake_one(project, name, context, *, timeout):
        order.append(name)
        if name == "action":
            assert "CHARACTERS SLICE" in context
            return {"scenes": [{"id_scene": "scene_01", "цепь_действия": ["a", "b"]}]}
        if name == "camera":
            assert "ACTION SCENES" in context
            return {"shot_plan": [{"цитата": "x", "крупность": "MS"}]}
        return {ag.LIST_KEY[name]: [{"id": "x"}]}

    project = SimpleNamespace(id=1, meta={"scene_design_variant": "chrono_dyn"})

    with (
        patch.object(runner, "load_checkpoint", return_value=None),
        patch.object(runner, "save_checkpoint", MagicMock()),
        patch.object(runner, "_run_one_agent", side_effect=fake_one),
    ):
        await runner.run_category_agents(project, "CTX", timeout=10)

    # chrono_dyn: верхние 3 → action → camera
    for top in ("characters", "world", "style"):
        assert order.index(top) < order.index("action")
    assert order.index("action") < order.index("camera")
    assert ag.agent_waves(project) == (
        ag.CHRONO_WAVE1_AGENTS,
        ag.CHRONO_WAVE2_AGENTS,
        ag.CHRONO_WAVE3_AGENTS,
    )
