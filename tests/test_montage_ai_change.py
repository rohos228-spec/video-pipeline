"""ИИзменение: GPT rewrite промта из IMAGE_PROMPT + VOICEOVER."""

from __future__ import annotations

import json
from pathlib import Path
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
    write_ai_change_db_card,
)
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
    assert data["frames"][0]["place"] == "кабинет"
    assert "IMAGE_PROMPT" not in path.read_text(encoding="utf-8")


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
    )
    assert out == "UPDATED PROMPT HERE"
    assert "IMAGE_PROMPT:" not in str(captured["text"])
    assert "VOICEOVER:" in str(captured["text"])
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
    async def fake_scope():
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
