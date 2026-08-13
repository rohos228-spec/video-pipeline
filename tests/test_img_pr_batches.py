"""img_pr batch helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import img_pr_batches as ipb


def test_chunk_frames_size() -> None:
    frames = [SimpleNamespace(n=i) for i in range(90)]
    chunks = ipb.chunk_frames(frames, size=40)
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [40, 40, 10]


def test_parse_img_pr_ops_wraps_style() -> None:
    reply = """
    {"ops":[
      {"frame_uuid":"aaa","fields":{"промт_картинки":"Background: metro car"}},
      {"frame_uuid":"bbb","fields":{"meaning":"skip"}},
      {"frame_uuid":"ccc","fields":{"image_prompt":"Action: reading"}}
    ]}
    """
    ops = ipb.parse_img_pr_ops(reply)
    assert len(ops) == 2
    assert {ipb.uuid_of_op(o) for o in ops} == {"aaa", "ccc"}
    body = ops[0]["fields"]["промт_картинки"]
    assert "Archival Noir Watercolor" in body
    assert "Background: metro car" in body
    assert "Final style lock" in body


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


def test_199_frames_batch_respects_size() -> None:
    chunks = ipb.chunk_frames(list(range(199)))
    assert all(len(c) <= ipb._FRAMES_PER_BATCH for c in chunks)
    assert len(chunks) == (199 + ipb._FRAMES_PER_BATCH - 1) // ipb._FRAMES_PER_BATCH


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
