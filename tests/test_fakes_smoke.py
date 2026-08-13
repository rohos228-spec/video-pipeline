"""T19: smoke для tests/fakes — эмуляционный контур должен сам быть зелёным."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.fakes.gpt import (
    FakeChaos,
    FakeGptClient,
    FakeScenario,
    make_fake_operator_api,
)
from tests.fakes.install import collect_patches, install_fakes_monkeypatch
from tests.fakes.media import (
    fake_generate_image_with_retries,
    fake_generate_video_with_retries,
)


@pytest.mark.asyncio
async def test_fake_gpt_routes_all_step_intents() -> None:
    g = FakeGptClient()
    plan = await g.ask_fresh('{"ops":[...общий_план...]}')
    assert "общий_план" in plan
    assert "replace_frames" in await g.ask_fresh("сделай replace_frames")
    assert "закадровый_текст" in await g.ask_fresh("верни закадровый_текст")

    reply = await g.ask_with_files(
        "Батч 1/1\nАдресация:\nкадр 1 = abcdef0123456789\nкадр 2 = f0f0f0f0f0f0f0f0\n",
        [],
    )
    ops = json.loads(reply)["ops"]
    assert [o["frame_uuid"] for o in ops] == ["abcdef0123456789", "f0f0f0f0f0f0f0f0"]
    assert all("промт_картинки" in o["fields"] for o in ops)

    anim = await g.ask_anim_pr_batch("ID изображения: [ID: P1-F1-aaaa]\n", [])
    assert "текст анимации" in anim


@pytest.mark.asyncio
async def test_fake_gpt_chaos_hooks() -> None:
    g = FakeGptClient(
        chaos=FakeChaos(img_pr_partial=True, img_pr_unknown_uuid=True)
    )
    reply = await g.ask_with_files(
        "Адресация:\nкадр 1 = abcdef0123456789\nкадр 2 = f0f0f0f0f0f0f0f0\n", []
    )
    ops = json.loads(reply)["ops"]
    assert len(ops) == 2  # 1 partial + 1 ghost
    assert ops[1]["frame_uuid"] == "deadbeefdeadbeefdeadbeef"

    g2 = FakeGptClient(chaos=FakeChaos(fail_call_numbers={2}))
    await g2.ask_fresh("общий_план")  # call 1 ок
    with pytest.raises(RuntimeError, match="fake chaos"):
        await g2.ask_fresh("общий_план")  # call 2 падает


@pytest.mark.asyncio
async def test_fake_operator_covers_all_uuids(tmp_path: Path) -> None:
    ctx = tmp_path / "db_frames.json"
    ctx.write_text(
        json.dumps({"frames": [{"uuid": "aa11"}, {"uuid": "bb22"}, {"uuid": "cc33"}]}),
        encoding="utf-8",
    )
    op = make_fake_operator_api()
    res = await op(
        project_dir=tmp_path,
        node_key="n1",
        role="assist",
        output_mode="project_file",
        prompt="x",
        accompanying="",
        input_paths=[ctx],
    )
    assert [o["frame_uuid"] for o in res.apply_ops["ops"]] == ["aa11", "bb22", "cc33"]


@pytest.mark.asyncio
async def test_fake_media_writes_real_files(tmp_path: Path) -> None:
    img = await fake_generate_image_with_retries(
        None, None, prompt="p", out_path=tmp_path / "scenes" / "f1.png"
    )
    assert img.file_path.is_file() and img.file_path.stat().st_size > 100
    vid = await fake_generate_video_with_retries(
        None, None, prompt="p", out_path=tmp_path / "videos" / "c1.mp4"
    )
    assert vid.file_path.is_file() and vid.file_path.read_bytes().startswith(
        b"\x00\x00\x00\x18ftyp"
    )


def test_install_covers_step_module_bindings(monkeypatch) -> None:
    """Ранние биндинги `from ... import get_gpt_client` тоже подменены."""
    import app.orchestrator.steps.generate_images as gi
    import app.orchestrator.steps.make_animation_prompts as map_

    fake = install_fakes_monkeypatch(monkeypatch, scenario=FakeScenario())
    assert gi.get_gpt_client() is fake
    assert map_.get_gpt_client() is fake
    assert gi.generate_image_with_retries is fake_generate_image_with_retries


def test_collect_patches_finds_all_targets() -> None:
    fake = FakeGptClient()
    op = make_fake_operator_api()
    patched = {(getattr(obj, "__name__", str(obj)), name) for obj, name, _ in collect_patches(fake, op)}
    mods = {m for m, _ in patched}
    assert "app.orchestrator.steps.generate_images" in mods
    assert "app.orchestrator.steps.generate_videos" in mods
    assert "app.services.gpt_operator_client" in mods
