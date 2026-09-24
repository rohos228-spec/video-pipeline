"""ИИзменение: GPT rewrite промта из IMAGE_PROMPT + VOICEOVER."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_ai_change import (
    build_ai_change_user_message,
    character_ids_from_prompt,
    load_img_pr_master,
    load_img_pr_rules,
    rewrite_prompt_via_gpt,
    strip_ai_change_reply,
    system_for_kind,
    trim_style_encyclopedia,
    write_ai_change_db_card,
)
from app.services.montage_board_meta import normalize_queue_ops
from app.services.montage_board_apply import (
    _IMAGE_OP_TYPES,
    _VIDEO_OP_TYPES,
    _run_op_with_short_sessions,
    order_montage_pending_ops,
)


def test_build_user_message_labels_fields() -> None:
    msg = build_ai_change_user_message(voiceover_text="Он открывает ящик.")
    assert "IMAGE_PROMPT:" not in msg
    assert "VOICEOVER:" in msg
    assert "db_frames.json" in msg
    assert "Он открывает ящик." in msg
    assert "полный промт" in msg.lower() or "только промт" in msg.lower()


def test_strip_ai_change_reply_fences_and_prefix() -> None:
    assert strip_ai_change_reply("```\nhello world\n```") == "hello world"
    assert strip_ai_change_reply("Prompt: camera pans left") == "camera pans left"
    assert strip_ai_change_reply("Промт: тихий поворот") == "тихий поворот"
    assert strip_ai_change_reply('"quoted prompt"') == "quoted prompt"


def test_system_video_has_hard_bans() -> None:
    sys = system_for_kind("video")
    assert "музык" in sys.lower()
    assert "silent" in sys.lower() or "речи" in sys


def test_system_image_locks_plan_and_objects() -> None:
    sys = system_for_kind("image")
    assert "картинк" in sys.lower()
    assert "STYLE" in sys
    assert "JSON" in sys
    assert "агент" in sys.lower() or "вложенн" in sys.lower()
    assert "сетк" in sys.lower() or "клонир" in sys.lower()
    assert "6 коротких" in sys or "словар" in sys.lower()
    assert "Scene feature / shot scale" in sys
    assert "coverage_parent" in sys
    assert "ДЕТАЛЬ" in sys


def test_trim_style_encyclopedia_keeps_character_block() -> None:
    scene = "Reference: character sheet for c02 — official at the left desk."
    style = "STYLE: " + ("watercolor noir dictionary word " * 80)
    out = trim_style_encyclopedia(f"{scene}\n\n{style}", max_style=200)
    assert out.startswith("Reference: character sheet for c02")
    assert "STYLE:" in out
    assert len(out) < len(scene) + 220


def test_system_does_not_embed_agent_text() -> None:
    sys = system_for_kind(
        "image",
        img_pr_rules="STYLE LOCK: watercolor only. Этот длинный агент не должен быть в system.",
    )
    assert "Этот длинный агент не должен быть в system" not in sys


def test_load_img_pr_master_empty_on_none() -> None:
    assert load_img_pr_master(None) == (None, "")
    assert load_img_pr_rules(None) == ""


def test_user_message_asks_llm_to_write_full_prompt() -> None:
    msg = build_ai_change_user_message(voiceover_text="y")
    low = msg.lower()
    assert "агент" in low
    assert "style" in low
    assert "не json" in low
    assert "OPERATOR_CHANGE" not in msg


def test_user_message_includes_operator_instruction() -> None:
    msg = build_ai_change_user_message(
        voiceover_text="Он открывает ящик.",
        instruction="сделай крупнее руки, холодный свет",
        action="рука выводит строки",
    )
    assert "OPERATOR_CHANGE:" in msg
    assert "сделай крупнее руки, холодный свет" in msg
    assert "Он открывает ящик." in msg
    assert "ACTION:" in msg
    assert "рука выводит строки" in msg
    assert "OPERATOR_CHANGE" in msg
    # Текст оператора важнее старого действия кадра, если они расходятся.
    low = msg.lower()
    assert "важнее" in low or "расход" in low or "новое действие" in low


def test_user_message_includes_camera_and_child_coverage() -> None:
    msg = build_ai_change_user_message(
        voiceover_text="дверь закрывается",
        action="c02 тянет ручку",
        camera={"план": "ДЕТАЛЬ", "ракурс": "3/4", "движение": "панорама"},
        coverage_role="child",
    )
    assert "CAMERA:" in msg
    assert "план: ДЕТАЛЬ" in msg
    assert "ракурс: 3/4" in msg
    assert "движение: панорама" in msg
    assert "Scene feature / shot scale" in msg
    assert "coverage_parent" in msg
    assert "A0" in msg


def test_normalize_keeps_ai_change_instruction() -> None:
    queued = normalize_queue_ops(
        [
            {
                "type": "image_ai_change",
                "frame_number": 3,
                "shot": 1,
                "instruction": "руки крупнее",
            }
        ]
    )
    assert queued == [
        {
            "type": "image_ai_change",
            "frame_number": 3,
            "shot": 1,
            "instruction": "руки крупнее",
        }
    ]


def test_strip_takes_prompt_from_apply_ops_json() -> None:
    raw = '{"ops":[{"frame_uuid":"ab","fields":{"промт_картинки":"watercolor scene"}}]}'
    assert strip_ai_change_reply(raw) == "watercolor scene"


def test_write_ai_change_db_card_has_frame_fields(tmp_path: Path) -> None:
    project = Project(id=9, slug="card-test", topic="t", hero_mode="auto")
    fr = Frame(
        project_id=9,
        number=72,
        uuid="340ef477ea3d463a95e6bae4",
        voiceover_text="фрагмент закадра",
        attrs={"characters": "c05", "place": "кабинет", "shot01_action": "печать"},
    )
    path = write_ai_change_db_card(project, fr, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["frames"][0]["characters"] == "c05"
    assert "place" not in data["frames"][0]
    assert data["frames"][0]["shot01_action"] == "печать"
    assert "IMAGE_PROMPT" not in path.read_text(encoding="utf-8")


def test_write_ai_change_db_card_uses_shot_action_not_scene_chain(
    tmp_path: Path,
) -> None:
    """В запросе ИИзменения — действие ЭТОГО кадра, не цепь всей сцены."""
    project = Project(id=9, slug="card-act", topic="t", hero_mode="auto")
    fr = Frame(
        project_id=9,
        number=2,
        uuid="340ef477ea3d463a95e6bae5",
        voiceover_text="достал папку",
        attrs={
            "действие": "рука выводит строки заявления",
            "главное_действие": (
                "1. кабинет — сидит за столом\n"
                "(вошёл и сел,)\n"
                "2. кабинет — рука пишет\n"
                "(достал папку)"
            ),
            "кадры": [
                {"id": "1-K1", "действие": "сидит за столом"},
                {"id": "1-K2", "действие": "рука выводит строки заявления"},
            ],
            "camera_subdivide": {"role": "shot", "shot_id": "1-K2"},
        },
    )
    path = write_ai_change_db_card(project, fr, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    row = data["frames"][0]
    assert row["shot01_action"] == "рука выводит строки заявления"
    assert row["действие"] == "рука выводит строки заявления"


def test_write_ai_change_db_card_child_gets_parent_and_camera(
    tmp_path: Path,
) -> None:
    project = Project(id=9, slug="card-child", topic="t", hero_mode="auto")
    parent = Frame(
        project_id=9,
        number=153,
        uuid="a" * 24,
        voiceover_text="идут к дому",
        image_prompt="parent still of the street and open door",
        attrs={
            "действие": "c01 идёт рядом с c02 по тротуару",
            "place": "улица у дома",
            "shot01_bg": "фасад и открытая дверь",
            "camera_subdivide": {
                "coverage_kind": "parent",
                "shot_id": "153-K1",
                "крупность": "ОБЩИЙ",
                "ракурс": "3/4",
                "движение": "следование",
            },
        },
    )
    child = Frame(
        project_id=9,
        number=156,
        uuid="b" * 24,
        voiceover_text="дверь закрывается",
        attrs={
            "действие": "c02 тянет дверь за ручку",
            "place": "улица у дома",
            "camera_subdivide": {
                "role": "shot",
                "coverage_kind": "child",
                "shot_id": "153-K4",
                "coverage_parent_id": "153-K1",
                "coverage_parent_number": 153,
                "крупность": "ДЕТАЛЬ",
                "ракурс": "3/4",
                "движение": "панорама",
            },
        },
    )
    path = write_ai_change_db_card(
        project, child, tmp_path, all_frames=[parent, child]
    )
    row = json.loads(path.read_text(encoding="utf-8"))["frames"][0]
    assert row["coverage_role"] == "child"
    assert row["план"] == "ДЕТАЛЬ"
    assert row["ракурс"] == "3/4"
    assert row["движение"] == "панорама"
    assert "ДЕТАЛЬ" in row["shot01_description"]
    assert "3/4" in row["shot01_description"]
    parent_snap = row["coverage_parent"]
    assert parent_snap["number"] == 153
    assert parent_snap["shot_id"] == "153-K1"
    assert parent_snap["план"] == "ОБЩИЙ"
    assert "place" not in parent_snap
    assert "shot01_bg" not in parent_snap
    assert "open door" in parent_snap["image_prompt_head"]
    assert "сидит за столом" not in str(row.get("main_action") or "")
    assert "сидит за столом" not in str(row.get("главное_действие") or "")
    kadry = row.get("кадры") or []
    assert len(kadry) <= 1
    if kadry:
        assert kadry[0]["действие"] == "рука выводит строки заявления"


def test_character_ids_from_prompt() -> None:
    assert character_ids_from_prompt("sheet for c05 and c01, then c05 again") == ["c05", "c01"]


def test_order_ops_includes_ai_change_types() -> None:
    assert "image_ai_change" in _IMAGE_OP_TYPES
    assert "video_ai_change" in _VIDEO_OP_TYPES
    ordered = order_montage_pending_ops(
        [
            {"type": "video_ai_change", "frame_number": 2, "shot": 1},
            {"type": "image_ai_change", "frame_number": 1, "shot": 1},
        ]
    )
    assert ordered[0]["type"] == "image_ai_change"
    assert ordered[1]["type"] == "video_ai_change"


@pytest.mark.asyncio
async def test_rewrite_prompt_via_gpt_uses_system_and_strips(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            captured["text"] = text
            captured["files"] = list(files)
            captured["system"] = kwargs.get("system")
            captured["auto_pack"] = kwargs.get("auto_pack")
            return "```\nUPDATED PROMPT HERE\n```"

    monkeypatch.setattr(
        "app.services.montage_ai_change.get_gpt_client",
        lambda: FakeGpt(),
    )
    master = tmp_path / "img_prompts_trash_polka_watercolor.md"
    master.write_text("STYLE LOCK watercolor", encoding="utf-8")
    card = tmp_path / "db_frames.json"
    card.write_text('{"frames":[]}', encoding="utf-8")
    out = await rewrite_prompt_via_gpt(
        voiceover_text="door opens",
        kind="video",
        project_id=31,
        img_pr_path=master,
        img_pr_variant="img_prompts_trash_polka_watercolor",
        db_card_path=card,
        instruction="холодный свет",
        action="рука пишет",
    )
    assert out == "UPDATED PROMPT HERE"
    assert "IMAGE_PROMPT:" not in str(captured["text"])
    assert "VOICEOVER:" in str(captured["text"])
    assert "OPERATOR_CHANGE:" in str(captured["text"])
    assert "холодный свет" in str(captured["text"])
    assert "ACTION:" in str(captured["text"])
    assert "рука пишет" in str(captured["text"])
    assert captured["files"] == [master, card]
    assert captured["auto_pack"] is False
    assert "музык" in str(captured["system"]).lower()
    assert "STYLE LOCK watercolor" not in str(captured["system"])


@pytest.mark.asyncio
async def test_rewrite_returns_llm_text_without_pipeline_style(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            return "LLM scene. STYLE: from agent only."

    monkeypatch.setattr(
        "app.services.montage_ai_change.get_gpt_client",
        lambda: FakeGpt(),
    )
    master = tmp_path / "img_prompts_trash_polka_watercolor.md"
    master.write_text("agent", encoding="utf-8")
    out = await rewrite_prompt_via_gpt(
        voiceover_text="vo",
        kind="image",
        img_pr_path=master,
        img_pr_variant="img_prompts_trash_polka_watercolor",
    )
    assert out == "LLM scene. STYLE: from agent only."
    assert "Archival Noir Watercolor Grunge Dossier Poster Illustration" not in out


@pytest.mark.asyncio
async def test_rewrite_prompt_via_gpt_binds_vibecode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            from app.services.llm_override import current_override

            ov = current_override()
            captured["provider"] = None if ov is None else ov.provider
            captured["kind"] = None if ov is None else ov.kind
            captured["model"] = None if ov is None else ov.model_id
            return "scene. STYLE: lock."

    monkeypatch.setattr(
        "app.services.montage_ai_change.get_gpt_client",
        lambda: FakeGpt(),
    )
    out = await rewrite_prompt_via_gpt(voiceover_text="vo", kind="image")
    assert out == "scene. STYLE: lock."
    assert captured["provider"] == "vibecode"
    assert captured["kind"] == "text"
    assert captured["model"]


@pytest.mark.asyncio
async def test_rewrite_uses_image_prompts_node_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            from app.services.llm_override import current_override

            ov = current_override()
            captured["model"] = None if ov is None else ov.model_id
            captured["provider"] = None if ov is None else ov.provider
            return "ok prompt"

    monkeypatch.setattr(
        "app.services.montage_ai_change.get_gpt_client",
        lambda: FakeGpt(),
    )
    project = SimpleNamespace(
        id=33,
        meta={
            "canvas_graph": {
                "nodes": [
                    {
                        "id": "n_img_pr",
                        "type": "image_prompts",
                        "data": {"modelId": "gpt-5.5"},
                    }
                ]
            }
        },
    )
    out = await rewrite_prompt_via_gpt(
        voiceover_text="vo",
        kind="image",
        project=project,
        project_id=33,
    )
    assert out == "ok prompt"
    assert captured["provider"] == "vibecode"
    assert captured["model"] == "gpt-5.5"


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "ai_change.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_run_op_video_ai_change_passes_gpt_prompt_to_prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))

    project = Project(id=201, slug="ai-change", topic="t", hero_mode="auto")
    project.data_dir.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (project.data_dir / "videos").mkdir(parents=True, exist_ok=True)
    # минимальный стартовый кадр для prepare_video_regen
    png = project.data_dir / "scenes" / "frame_001.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 300_000)

    fr = Frame(
        project_id=201,
        number=1,
        voiceover_text="Он медленно открывает дверь.",
        image_prompt="man at wooden door, noir lighting",
        animation_prompt="old video prompt",
    )
    session.add(project)
    session.add(fr)
    await session.commit()

    # session_scope → наша тестовая сессия
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_scope(*_a, **_k):
        yield session

    monkeypatch.setattr(
        "app.services.montage_board_apply.session_scope",
        fake_scope,
    )
    monkeypatch.setattr(
        "app.services.montage_ai_change.rewrite_prompt_via_gpt",
        AsyncMock(return_value="AI VIDEO PROMPT NO NEW OBJECTS"),
    )
    # Перепривязать импорт в apply-модуле
    monkeypatch.setattr(
        "app.services.montage_board_apply.rewrite_prompt_via_gpt",
        AsyncMock(return_value="AI VIDEO PROMPT NO NEW OBJECTS"),
    )

    prepare_calls: list[dict] = []

    async def fake_prepare(session_, project_, frame_number, **kwargs):
        prepare_calls.append({"frame_number": frame_number, **kwargs})
        prep = MagicMock()
        prep.file_path = project.data_dir / "videos" / "clip_001_x.mp4"
        prep.prompt_text = kwargs.get("new_prompt") or ""
        return prep

    monkeypatch.setattr(
        "app.services.montage_board_apply.prepare_video_regen",
        fake_prepare,
    )
    monkeypatch.setattr(
        "app.services.montage_board_apply.execute_video_regen",
        AsyncMock(return_value=project.data_dir / "videos" / "clip_001_x.mp4"),
    )
    monkeypatch.setattr(
        "app.services.montage_board_apply._finalize_video_with_retry",
        AsyncMock(return_value={"ok": True}),
    )

    class _Slot:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "app.services.montage_board_apply.acquire_image_slot",
        lambda: _Slot(),
    )

    # файл «готов», чтобы execute не требовал реального mp4
    out = project.data_dir / "videos" / "clip_001_x.mp4"
    out.write_bytes(b"\x00" * 100_000)

    result = await _run_op_with_short_sessions(
        201,
        {"type": "video_ai_change", "frame_number": 1, "shot": 1},
        board={},
    )
    assert result["ok"] is True
    assert prepare_calls
    assert prepare_calls[0]["mode"] == "edit_prompt"
    assert prepare_calls[0]["new_prompt"] == "AI VIDEO PROMPT NO NEW OBJECTS"


@pytest.mark.asyncio
async def test_run_op_image_ai_change_passes_instruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    project = Project(id=202, slug="ai-note", topic="t", hero_mode="auto")
    project.data_dir.mkdir(parents=True, exist_ok=True)
    (project.data_dir / "scenes").mkdir(parents=True, exist_ok=True)
    png = project.data_dir / "scenes" / "frame_001.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 300_000)
    session.add_all(
        [
            project,
            Frame(
                project_id=202,
                number=1,
                voiceover_text="vo",
                image_prompt="old",
                attrs={"действие": "рука выводит строки"},
            ),
        ]
    )
    await session.commit()

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def fake_scope(*_a, **_k):
        yield session

    rewrite = AsyncMock(return_value="AGENT PROMPT")
    monkeypatch.setattr("app.services.montage_board_apply.session_scope", fake_scope)
    monkeypatch.setattr("app.services.montage_board_apply.rewrite_prompt_via_gpt", rewrite)

    async def fake_prepare(session_, project_, frame_number, **kwargs):
        prep = MagicMock()
        prep.file_path = project.data_dir / "scenes" / "frame_001.png"
        prep.prompt_text = kwargs.get("new_prompt") or ""
        return prep

    monkeypatch.setattr(
        "app.services.montage_board_apply.prepare_image_regen",
        fake_prepare,
    )
    monkeypatch.setattr(
        "app.services.montage_board_apply.execute_image_regen",
        AsyncMock(return_value=png),
    )
    monkeypatch.setattr(
        "app.services.montage_board_apply._finalize_image_with_retry",
        AsyncMock(return_value={"ok": True}),
    )

    class _Slot:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "app.services.montage_board_apply.acquire_image_slot",
        lambda: _Slot(),
    )

    result = await _run_op_with_short_sessions(
        202,
        {
            "type": "image_ai_change",
            "frame_number": 1,
            "shot": 1,
            "instruction": "руки крупнее, холодный свет",
        },
        board={},
    )
    assert result["ok"] is True
    assert rewrite.await_args.kwargs["instruction"] == "руки крупнее, холодный свет"
    assert rewrite.await_args.kwargs["action"] == "рука выводит строки"
    assert rewrite.await_args.kwargs.get("camera") == {}
    assert rewrite.await_args.kwargs.get("coverage_role") == ""
