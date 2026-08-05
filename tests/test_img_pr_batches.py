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


def test_199_frames_is_five_batches_not_thirty_four() -> None:
    chunks = ipb.chunk_frames(list(range(199)))
    assert len(chunks) == 5
    assert len(chunks) < 10


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
