"""db_frames.json snapshot for img_pr after scene_grammar."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.db_frames_context import (
    build_excel_gpt_db_context,
    build_img_pr_db_context,
    collapse_script_writer_frames,
)


def test_img_pr_db_context_picks_scene_grammar_keys() -> None:
    fr = SimpleNamespace(
        number=1,
        uuid="abc123",
        voiceover_text="закадр кусок — в JSON как SoT, в промт не копировать",
        meaning="",
        animation_prompt="камера неподвижна, стол редакции",
        attrs={
            "place": "метро вагон",
            "lighting": "fluorescent overhead",
            "scene_lighting": "fluorescent overhead",
            "shot01_bg": "серый пол и поручни",
            "shot01_action": "пассажир читает газету",
            "shot01_description": "средний план, камера на уровне груди",
            "accent": "развёрнутая газета",
            "scene_sense": "пассажиры держатся за поручни",
            "scene_feature": "Средний план персонажа",
            "visual_type": "Кинематографический реализм",
            "shot01_props": "газета, поручень",
            "characters": "c01",
            "noise": "ignore me",
        },
    )
    ctx = build_img_pr_db_context(
        project_id=53,
        slug="aum",
        frames=[fr],
        characters=[{"id": "c01", "имя": "Asahara", "внешность": "…"}],
        general_plan="эпоха 1995",
        include_field_map=True,
    )
    assert ctx["source"] == "db_v2"
    assert ctx["general_plan"] == "эпоха 1995"
    assert len(ctx["frames"]) == 1
    row = ctx["frames"][0]
    assert row["uuid"] == "abc123"
    assert row["voiceover_text"].startswith("закадр")
    assert "камера неподвижна" in row["animation_prompt"]
    assert row["place"] == "метро вагон"
    assert row["shot01_bg"].startswith("серый")
    assert row["lighting"] == "fluorescent overhead"
    assert row["персонажи"] == "c01"
    assert row["characters"] == "c01"
    assert "noise" not in row
    assert ctx["characters"][0]["id"] == "c01"
    assert "shot01_bg" in ctx["field_map"]


def test_img_pr_db_context_skips_frames_without_uuid() -> None:
    bad = SimpleNamespace(number=2, uuid="", voiceover_text="x", meaning="", attrs={})
    ctx = build_img_pr_db_context(
        project_id=1,
        slug="x",
        frames=[bad],
        characters=[],
    )
    assert ctx["frames"] == []


def test_excel_gpt_context_is_slim_and_keeps_vo_snippet() -> None:
    fr = SimpleNamespace(
        number=1, uuid="ab" * 12, voiceover_text="слово " * 200,
        meaning="m", attrs={"place": "кухня", "noise": "X" * 5000, "shot01_bg": "стол"},
    )
    from app.services.db_frames_context import build_excel_gpt_db_context
    ctx = build_excel_gpt_db_context(project_id=14, slug="x", frames=[fr], characters=[])
    row = ctx["frames"][0]
    assert "noise" not in row
    assert "attrs" not in row
    assert row["place"] == "кухня"
    assert len(row["voiceover_text"]) <= 400
    blob = json.dumps(ctx)
    assert len(blob) < 20_000


def test_excel_gpt_check_context_is_slim_with_scene_registry() -> None:
    fr = SimpleNamespace(
        number=2,
        uuid="cd" * 12,
        voiceover_text="закадр " * 200,
        meaning="смысл " * 200,
        attrs={"place": "коридор", "noise": "Y" * 5000, "shot01_bg": "лампа"},
    )
    from app.services.db_frames_context import build_excel_gpt_check_context

    ctx = build_excel_gpt_check_context(
        project_id=61,
        slug="cinema",
        frames=[fr],
        characters=[{"id": "c01", "имя": "Hero", "type": "character", "attrs": {}}],
        scene_registry=[{"id": "scene_01", "loc": "loc01"}],
    )
    assert ctx["source"] == "db_v2"
    assert ctx["scene_registry"] == [{"id": "scene_01", "loc": "loc01"}]
    assert ctx["characters"][0]["id"] == "c01"
    row = ctx["frames"][0]
    assert row["uuid"] == "cd" * 12
    assert "attrs" not in row
    assert "noise" not in row
    assert row["place"] == "коридор"
    assert row["shot01_bg"] == "лампа"
    assert len(row["voiceover_text"]) <= 400
    assert len(row["meaning"]) <= 500
    assert len(json.dumps(ctx)) < 20_000


def _vo_frame(uid: str, number: int, vo: str, *, parent: str, shot: int):
    return SimpleNamespace(
        uuid=uid,
        number=number,
        voiceover_text=vo,
        attrs={
            "camera_subdivide": {
                "parent_uuid": parent,
                "shot_index": shot,
                "role": "vo_parent" if shot <= 1 else "shot",
            }
        },
    )


def test_script_writer_collapses_shot_children_into_vo_cells() -> None:
    """188 шотов с фрагментами → 2 ячейки закадра, полный текст на родителе."""
    frames = [
        _vo_frame("p1", 1, "один два три", parent="p1", shot=1),
        _vo_frame("c1", 2, "четыре пять", parent="p1", shot=2),
        _vo_frame("c2", 3, "шесть", parent="p1", shot=3),
        _vo_frame("p2", 4, "семь восемь", parent="p2", shot=1),
        _vo_frame("c3", 5, "девять", parent="p2", shot=2),
    ]
    clipped = [
        {"uuid": "p1", "voiceover_text": "один два…", "main_action": "x"},
        {"uuid": "c1", "voiceover_text": "четыре пять"},
        {"uuid": "c2", "voiceover_text": "шесть"},
        {"uuid": "p2", "voiceover_text": "семь восемь"},
        {"uuid": "c3", "voiceover_text": "девять"},
    ]
    rows = collapse_script_writer_frames(frames, clipped)
    assert [r["uuid"] for r in rows] == ["p1", "p2"]
    assert rows[0]["voiceover_text"] == "один два три четыре пять шесть"
    assert rows[1]["voiceover_text"] == "семь восемь девять"
    assert "main_action" in rows[0]


def test_script_writer_keeps_plain_vo_frames_without_subdivide() -> None:
    frames = [
        SimpleNamespace(uuid="a", number=1, voiceover_text="hello", attrs={}),
        SimpleNamespace(uuid="b", number=2, voiceover_text="", attrs={}),
        SimpleNamespace(uuid="c", number=3, voiceover_text="world", attrs=None),
    ]
    rows = collapse_script_writer_frames(frames)
    assert [r["uuid"] for r in rows] == ["a", "c"]
    assert rows[0]["voiceover_text"] == "hello"


def test_excel_gpt_db_context_exposes_image_prompt_for_skip() -> None:
    frames = [
        SimpleNamespace(
            number=1,
            uuid="a" * 24,
            voiceover_text="vo",
            meaning="",
            image_prompt="already filled prompt",
            animation_prompt="already filled anim",
            attrs={"shot01_action": "шаг"},
        ),
        SimpleNamespace(
            number=2,
            uuid="b" * 24,
            voiceover_text="vo2",
            meaning="",
            image_prompt="",
            animation_prompt="",
            attrs={},
        ),
    ]
    ctx = build_excel_gpt_db_context(
        project_id=60, slug="x", frames=frames, characters=[]
    )
    assert ctx["frames"][0]["image_prompt"].startswith("already")
    assert ctx["frames"][0]["animation_prompt"].startswith("already")
    assert ctx["frames"][0]["shot01_action"] == "шаг"
    assert "image_prompt" not in ctx["frames"][1]
    from app.services.apply_ops_batches import (
        SKIP_PROMPTS_AND_ACTION,
        _pending_frames,
    )

    pending = _pending_frames(
        ctx["frames"], dense=False, skip_if_field=SKIP_PROMPTS_AND_ACTION
    )
    assert [f["uuid"] for f in pending] == ["b" * 24]


def test_excel_gpt_force_full_strips_old_prompts_keeps_vo() -> None:
    """Ручной ▶ не должен кормить GPT старыми image/anim — иначе копия 1-в-1."""
    fr = SimpleNamespace(
        number=1,
        uuid="a" * 24,
        voiceover_text="у ворот канцелярии толпа",
        meaning="",
        image_prompt="Москва, середина XVIII века, приёмная канцелярии",
        animation_prompt="камера медленно наезжает на стол",
        attrs={
            "shot01_action": "рука раскладывает две жалобы",
            "place": "канцелярия",
            "camera_subdivide": {
                "role": "shot",
                "vo_shot": "у ворот канцелярии толпа",
            },
        },
    )
    ctx = build_excel_gpt_db_context(
        project_id=60, slug="x", frames=[fr], characters=[], strip_prompts=True
    )
    row = ctx["frames"][0]
    assert "image_prompt" not in row
    assert "animation_prompt" not in row
    assert "shot01_action" not in row
    assert "place" not in row
    assert row["voiceover_text"] == "у ворот канцелярии толпа"
    assert row["vo_shot"] == "у ворот канцелярии толпа"


def test_excel_gpt_keeps_bits_action_shots_and_full_vo() -> None:
    long_vo = "А" * 450
    chain = "1. двор — бегает\n(кусок один,)\n" + ("x" * 80)
    shots = [
        {"id": "1-K1", "parent_id": None, "порядок": 1, "закадр": "кусок один,"},
        {"id": "1-K2", "parent_id": "1-K1", "порядок": 2, "закадр": "кусок два."},
    ]
    fr = SimpleNamespace(
        number=1,
        uuid="d" * 24,
        voiceover_text=long_vo,
        meaning="",
        image_prompt="old",
        animation_prompt="old-mov",
        attrs={
            "биты": [{"порядок": 1, "глагол": "бегает"}],
            "main_action": chain,
            "кадры": shots,
            "place": "двор",
        },
    )
    ctx = build_excel_gpt_db_context(
        project_id=60,
        slug="x",
        frames=[fr],
        characters=[],
        strip_prompts=True,
        full_vo=True,
    )
    row = ctx["frames"][0]
    assert row["voiceover_text"] == long_vo
    assert "image_prompt" not in row
    assert "place" not in row
    bits = row["биты"]
    if isinstance(bits, str):
        bits = json.loads(bits)
    assert bits[0]["глагол"] == "бегает"
    assert row["main_action"] == chain
    stored = row["кадры"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored[1]["id"] == "1-K2"


def test_force_full_action_strips_old_main_action_keeps_bits() -> None:
    """▶ fw_action: биты — вход, старая цепь сцен не должна попасть в GPT."""
    from app.services.db_frames_context import force_full_strip_output_keys

    fr = SimpleNamespace(
        number=1,
        uuid="e" * 24,
        voiceover_text="После брака с Глебом Салтыковым",
        meaning="",
        image_prompt="",
        animation_prompt="",
        attrs={
            "биты": [{"порядок": 1, "глагол": "женится"}],
            "main_action": "1. двор — бегает\n(старая цепь,)",
            "кадры": [{"id": "1-K1", "порядок": 1}],
        },
    )
    keys = force_full_strip_output_keys("n_excel_gpt_fw_action")
    ctx = build_excel_gpt_db_context(
        project_id=60,
        slug="x",
        frames=[fr],
        characters=[],
        strip_prompts=True,
        strip_output_keys=keys,
        full_vo=True,
    )
    row = ctx["frames"][0]
    assert "main_action" not in row
    assert "главное_действие" not in row
    bits = row["биты"]
    if isinstance(bits, str):
        bits = json.loads(bits)
    assert bits[0]["глагол"] == "женится"
    stored = row["кадры"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert stored[0]["id"] == "1-K1"


def test_force_full_shots_strips_old_kadry_keeps_action() -> None:
    from app.services.db_frames_context import force_full_strip_output_keys

    fr = SimpleNamespace(
        number=1,
        uuid="f" * 24,
        voiceover_text="После брака с Глебом Салтыковым",
        meaning="",
        image_prompt="",
        animation_prompt="",
        attrs={
            "биты": [{"порядок": 1, "глагол": "женится"}],
            "main_action": "1. церковь — венчание с Глебом\n(После брака)",
            "кадры": [{"id": "1-K1", "порядок": 1, "шаблон": "T0"}],
        },
    )
    keys = force_full_strip_output_keys("n_excel_gpt_fw_shots")
    ctx = build_excel_gpt_db_context(
        project_id=60,
        slug="x",
        frames=[fr],
        characters=[],
        strip_prompts=True,
        strip_output_keys=keys,
        full_vo=True,
    )
    row = ctx["frames"][0]
    assert "кадры" not in row
    assert row["main_action"].startswith("1. церковь")
    bits = row["биты"]
    if isinstance(bits, str):
        bits = json.loads(bits)
    assert bits[0]["глагол"] == "женится"


def test_force_full_script_strips_old_bits() -> None:
    from app.services.db_frames_context import force_full_strip_output_keys

    fr = SimpleNamespace(
        number=1,
        uuid="g" * 24,
        voiceover_text="После брака с Глебом",
        meaning="",
        image_prompt="",
        animation_prompt="",
        attrs={"биты": [{"порядок": 1, "глагол": "старый"}]},
    )
    keys = force_full_strip_output_keys("n_excel_gpt_fw_script")
    ctx = build_excel_gpt_db_context(
        project_id=60,
        slug="x",
        frames=[fr],
        characters=[],
        strip_prompts=True,
        strip_output_keys=keys,
        full_vo=True,
    )
    row = ctx["frames"][0]
    assert "биты" not in row
    assert row["voiceover_text"] == "После брака с Глебом"


def test_force_full_frames_keeps_shots_strips_prompts() -> None:
    from app.services.db_frames_context import force_full_strip_output_keys

    assert force_full_strip_output_keys("n_excel_gpt_fw_frames") == ()
    assert force_full_strip_output_keys("n_excel_gpt_fw_qc") == ()


def test_excel_gpt_exposes_vo_shot_copy_not_as_voiceover() -> None:
    fr = SimpleNamespace(
        number=2,
        uuid="c" * 24,
        voiceover_text="",
        meaning="",
        image_prompt="",
        animation_prompt="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": "p" * 24,
                "vo_shot": "фрагмент шота из копии",
            }
        },
    )
    ctx = build_excel_gpt_db_context(
        project_id=60, slug="x", frames=[fr], characters=[]
    )
    row = ctx["frames"][0]
    assert "voiceover_text" not in row
    assert row["vo_shot"] == "фрагмент шота из копии"
    assert row["закадр_шота"] == "фрагмент шота из копии"
