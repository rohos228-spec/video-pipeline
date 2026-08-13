"""img_pr batch helpers."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import img_pr_batches as ipb


def test_chunk_frames_size() -> None:
    frames = [SimpleNamespace(n=i) for i in range(90)]
    chunks = ipb.chunk_frames(frames, size=40)
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [40, 40, 10]


def test_parse_img_pr_ops_wraps_style() -> None:
    # Явный noir — легаси обёртка сохранена (после T07 нуар не default).
    reply = """
    {"ops":[
      {"frame_uuid":"aaa","fields":{"промт_картинки":"Background: metro car"}},
      {"frame_uuid":"bbb","fields":{"meaning":"skip"}},
      {"frame_uuid":"ccc","fields":{"image_prompt":"Action: reading"}}
    ]}
    """
    ops = ipb.parse_img_pr_ops(reply, style_id="noir")
    assert len(ops) == 2
    assert {ipb.uuid_of_op(o) for o in ops} == {"aaa", "ccc"}
    body = ops[0]["fields"]["промт_картинки"]
    assert "Archival Noir Watercolor" in body
    assert "Background: metro car" in body
    assert "Final style lock" in body


def test_parse_img_pr_ops_no_style_no_noir_wrap() -> None:
    # Стиль не задан → сцена проходит как есть, никакого молчаливого нуара.
    reply = """
    {"ops":[
      {"frame_uuid":"aaa","fields":{"промт_картинки":"Background: metro car"}}
    ]}
    """
    ops = ipb.parse_img_pr_ops(reply)
    body = ops[0]["fields"]["промт_картинки"]
    assert body == "Background: metro car"
    assert "Archival Noir" not in body


def test_parse_img_pr_ops_skips_wrap_for_plastilin() -> None:
    reply = """
    {"ops":[
      {"frame_uuid":"aaa","fields":{"промт_картинки":"Minimalist Claymation Plasticine 2D-Look Miniature Illustration. Scene of a shop."}}
    ]}
    """
    ops = ipb.parse_img_pr_ops(reply, wrap_style=False)
    body = ops[0]["fields"]["промт_картинки"]
    assert "Claymation" in body
    assert "Archival Noir Watercolor" not in body


def test_wrap_does_not_add_watercolor_over_clay() -> None:
    from app.services.img_pr_style import wrap_scene_with_style

    body = (
        "Minimalist Claymation Plasticine 2D-Look Miniature Illustration. "
        "A clay person sews in a workshop."
    )
    wrapped = wrap_scene_with_style(body)
    assert wrapped == body
    assert "Archival Noir" not in wrapped


def test_plan_batch_size_110_is_three() -> None:
    size = ipb.plan_batch_size(110)
    chunks = ipb.chunk_frames(list(range(110)), size=size)
    assert len(chunks) == 3
    assert all(len(c) >= 36 for c in chunks)


def test_tiny_delivery_does_not_become_onesie() -> None:
    rest = list(range(24))
    chunks = ipb.repartition_remaining(rest, delivered=1, min_size=8)
    assert all(len(c) >= 8 for c in chunks)
    assert len(chunks) <= 3


def test_199_frames_three_batches() -> None:
    size = ipb.plan_batch_size(199)
    chunks = ipb.chunk_frames(list(range(199)), size=size)
    assert len(chunks) == 3
    assert sum(len(c) for c in chunks) == 199


def test_plan_batch_size_small_is_one_batch() -> None:
    size = ipb.plan_batch_size(5)
    chunks = ipb.chunk_frames(list(range(5)), size=size)
    assert len(chunks) == 1
    assert len(chunks[0]) == 5


def test_salvage_broken_json_персонажи_outside_fields() -> None:
    # Битый ответ: лишняя } и персонажи вне fields (как в проде).
    reply = (
        '{"ops":['
        '{"frame_uuid":"aaaaaaaaaaaaaaaaaaaaaaaa","fields":{"промт_картинки":"Background: a"},'
        '"персонажи":"c01"}},'
        '{"frame_uuid":"bbbbbbbbbbbbbbbbbbbbbbbb","fields":{"промт_картинки":"Action: b"}}'
        "]}"
    )
    # extract/salvage может вытащить куски; parse_img_pr_ops чинит fields.
    ops = ipb.parse_img_pr_ops(reply, wrap_style=False)
    assert len(ops) >= 2
    assert {ipb.uuid_of_op(o) for o in ops} >= {
        "aaaaaaaaaaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbbbbbbbbbb",
    }


def test_batch_attach_includes_master_on_every_batch(tmp_path: Path) -> None:
    master = tmp_path / "prompt_img_pr.txt"
    master.write_text("master", encoding="utf-8")
    db1 = tmp_path / "db_frames_b01.json"
    db2 = tmp_path / "db_frames_b02.json"
    db1.write_text("{}", encoding="utf-8")
    db2.write_text("{}", encoding="utf-8")
    vo = tmp_path / "voiceover.txt"
    vo.write_text("vo", encoding="utf-8")

    first = ipb.batch_attach_files(
        batch_i=1, prompt_file=master, db_path=db1, voiceover=vo
    )
    later = ipb.batch_attach_files(
        batch_i=2, prompt_file=master, db_path=db2, voiceover=vo
    )
    assert first == [master, db1, vo]
    assert later == [master, db2]
    assert master in later


def test_checkpoint_roundtrip(tmp_path: Path) -> None:
    project_dir = tmp_path
    ipb.save_checkpoint(
        project_dir,
        done_uuids=["u1", "u2"],
        ops=[{"frame_uuid": "u1", "fields": {"промт_картинки": "x"}}],
    )
    loaded = ipb.load_checkpoint(project_dir)
    assert loaded["done_uuids"] == ["u1", "u2"]
    assert len(loaded["ops"]) == 1
    ipb.clear_checkpoint(project_dir)
    assert ipb.load_checkpoint(project_dir)["done_uuids"] == []


# --- T09: run_img_pr_xlsx — cap continue rounds + гарантия покрытия ---


class _FakeGpt:
    """GPT, который отдаёт ops по db_frames_bXX.json из вложения."""

    def __init__(self, mode: str) -> None:
        assert mode in {"one", "full"}
        self.mode = mode
        self.calls = 0

    async def new_conversation(self) -> None:
        pass

    async def ask_with_files(self, text, files, **kw):
        self.calls += 1
        db = next(f for f in files if f.name.startswith("db_frames"))
        data = json.loads(Path(db).read_text(encoding="utf-8"))
        uuids = [f["uuid"] for f in data["frames"]]
        take = uuids[:1] if self.mode == "one" else uuids
        return json.dumps(
            {
                "ops": [
                    {
                        "frame_uuid": u,
                        "fields": {"промт_картинки": f"Сцена кадра {u}"},
                    }
                    for u in take
                ]
            },
            ensure_ascii=False,
        )


def _patch_img_pr_env(monkeypatch, tmp_path: Path, frames, *, meta=None):
    """Изолированный run_img_pr_xlsx: без DB/xlsx/сети, GPT подменён."""
    from app.services import xlsx_step_runners as xsr

    project = SimpleNamespace(
        id=777, slug="t", data_dir=tmp_path / "data", meta=meta or {}
    )
    tmp_dir = tmp_path / "tmp_gpt"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = tmp_dir / "prompt_img_pr_test.txt"
    prompt_file.write_text("# img_pr master", encoding="utf-8")
    proj_xlsx = tmp_path / "project.xlsx"

    monkeypatch.setattr(xsr, "_ensure_project_xlsx", lambda p: proj_xlsx)
    monkeypatch.setattr(xsr.cx, "tmp_gpt_dir", lambda p: tmp_dir)
    monkeypatch.setattr(
        xsr.cx, "write_img_pr_prompt_file", lambda p, d, ts=None: prompt_file
    )
    monkeypatch.setattr(xsr.cx, "ensure_current_voiceover", lambda p: None)
    monkeypatch.setattr(xsr.cx, "chat_message", lambda p, step, **kw: "msg")

    async def fake_load(project, *, only_uuids=None, skip_uuids=None):
        selected = [
            fr for fr in frames if not (skip_uuids and fr.uuid in skip_uuids)
        ]
        return selected, [], "plan"

    monkeypatch.setattr(xsr, "_load_img_pr_context", fake_load)

    def fake_write(
        project,
        tmp_dir,
        frames,
        cards,
        general_plan,
        *,
        batch_tag="",
        include_characters=True,
    ):
        name = f"db_frames{('_' + batch_tag) if batch_tag else ''}.json"
        path = Path(tmp_dir) / name
        path.write_text(
            json.dumps(
                {
                    "frames": [
                        {"uuid": fr.uuid, "number": fr.number} for fr in frames
                    ]
                }
            ),
            encoding="utf-8",
        )
        return path

    monkeypatch.setattr(xsr, "_write_img_pr_db_frames_for", fake_write)

    async def fake_lock(project_id, step, fn):
        return await fn()

    monkeypatch.setattr(xsr.xgf, "run_under_xlsx_lock", fake_lock)
    monkeypatch.setattr(
        "app.services.step_cancel.raise_if_cancelled", lambda pid: None
    )
    return project


def _frames(n: int):
    return [
        SimpleNamespace(uuid=f"{i:024x}", number=i + 1) for i in range(n)
    ]


async def test_img_pr_runner_fails_after_max_continue_rounds(
    monkeypatch, tmp_path
) -> None:
    from app.services import xlsx_step_runners as xsr

    frames = _frames(30)
    project = _patch_img_pr_env(monkeypatch, tmp_path, frames)
    gpt = _FakeGpt(mode="one")
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client", lambda: gpt
    )

    with pytest.raises(RuntimeError, match="continue") as exc_info:
        await xsr.run_img_pr_xlsx(project)

    # Не бесконечный цикл: стартовые батчи + ровно MAX_CONTINUE_ROUNDS доборов.
    initial = len(ipb.chunk_frames(frames, size=ipb.plan_batch_size(len(frames))))
    assert gpt.calls == initial + ipb.MAX_CONTINUE_ROUNDS
    # Сообщение — с числом недостающих кадров.
    missing_n = len(frames) - gpt.calls  # по 1 op за вызов
    assert f"{missing_n} из {len(frames)}" in str(exc_info.value)
    # Checkpoint сохранился — retry продолжит с него, ops не потеряны.
    ckpt = ipb.load_checkpoint(project.data_dir)
    assert len(ckpt["ops"]) == gpt.calls
    assert len(ckpt["done_uuids"]) == gpt.calls


async def test_img_pr_runner_full_batches_success_knitted(
    monkeypatch, tmp_path
) -> None:
    from app.services import xlsx_step_runners as xsr

    frames = _frames(20)
    project = _patch_img_pr_env(
        monkeypatch, tmp_path, frames, meta={"img_pr_style_id": "knitted"}
    )
    gpt = _FakeGpt(mode="full")
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client", lambda: gpt
    )

    result = await xsr.run_img_pr_xlsx(project)

    got = {ipb.uuid_of_op(op) for op in result.apply_ops}
    assert got == {fr.uuid for fr in frames}
    # STYLE-lock приехал из meta проекта: knitted, не нуар.
    body = result.apply_ops[0]["fields"]["промт_картинки"]
    assert "Knitted Wool Felt Textile" in body
    assert "Archival Noir" not in body


async def test_img_pr_runner_no_style_meta_no_wrap(
    monkeypatch, tmp_path
) -> None:
    from app.services import xlsx_step_runners as xsr

    frames = _frames(10)
    project = _patch_img_pr_env(monkeypatch, tmp_path, frames)
    gpt = _FakeGpt(mode="full")
    monkeypatch.setattr(
        "app.services.gpt_client.get_gpt_client", lambda: gpt
    )

    result = await xsr.run_img_pr_xlsx(project)

    assert len(result.apply_ops) == len(frames)
    body = result.apply_ops[0]["fields"]["промт_картинки"]
    assert body.startswith("Сцена кадра")
    assert "Archival Noir" not in body
