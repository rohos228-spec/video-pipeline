"""ИИзменение: GPT rewrite промта из IMAGE_PROMPT + VOICEOVER."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_ai_change import (
    build_ai_change_user_message,
    rewrite_prompt_via_gpt,
    strip_ai_change_reply,
    system_for_kind,
)
from app.services.montage_board_apply import (
    _IMAGE_OP_TYPES,
    _VIDEO_OP_TYPES,
    _run_op_with_short_sessions,
    order_montage_pending_ops,
)


def test_build_user_message_labels_fields() -> None:
    msg = build_ai_change_user_message(
        image_prompt="dark room, man at desk",
        voiceover_text="Он открывает ящик.",
    )
    assert "IMAGE_PROMPT:" in msg
    assert "dark room, man at desk" in msg
    assert "VOICEOVER:" in msg
    assert "Он открывает ящик." in msg
    assert "ТОЛЬКО обновлённым промтом" in msg


def test_strip_ai_change_reply_fences_and_prefix() -> None:
    assert strip_ai_change_reply("```\nhello world\n```") == "hello world"
    assert strip_ai_change_reply("Prompt: camera pans left") == "camera pans left"
    assert strip_ai_change_reply("Промт: тихий поворот") == "тихий поворот"
    assert strip_ai_change_reply('"quoted prompt"') == "quoted prompt"


def test_system_video_has_hard_bans() -> None:
    sys = system_for_kind("video")
    assert "новых объектов" in sys.lower() or "новых объект" in sys
    assert "музык" in sys.lower()
    assert "silent" in sys.lower() or "речи" in sys


def test_system_image_is_image_prompt() -> None:
    sys = system_for_kind("image")
    assert "картинк" in sys.lower()
    assert "VOICEOVER" in sys


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeGpt:
        async def ask_with_files(self, text, files, **kwargs):
            captured["text"] = text
            captured["system"] = kwargs.get("system")
            return "```\nUPDATED PROMPT HERE\n```"

    monkeypatch.setattr(
        "app.services.montage_ai_change.get_gpt_client",
        lambda: FakeGpt(),
    )
    out = await rewrite_prompt_via_gpt(
        image_prompt="room",
        voiceover_text="door opens",
        kind="video",
        project_id=31,
    )
    assert out == "UPDATED PROMPT HERE"
    assert "IMAGE_PROMPT:" in str(captured["text"])
    assert "музык" in str(captured["system"]).lower()


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
