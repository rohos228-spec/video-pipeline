"""img_pr batch helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.services import img_pr_batches as ipb


def test_chunk_frames_size() -> None:
    frames = [SimpleNamespace(n=i) for i in range(13)]
    chunks = ipb.chunk_frames(frames, size=6)
    assert len(chunks) == 3
    assert [len(c) for c in chunks] == [6, 6, 1]


def test_parse_img_pr_ops_extracts_prompts() -> None:
    reply = """
    вот ops:
    {"ops":[
      {"frame_uuid":"aaa","fields":{"промт_картинки":"STYLE…"}},
      {"frame_uuid":"bbb","fields":{"meaning":"skip"}},
      {"frame_uuid":"ccc","fields":{"image_prompt":"ok"}}
    ]}
    """
    ops = ipb.parse_img_pr_ops(reply)
    assert len(ops) == 2
    assert {ipb.uuid_of_op(o) for o in ops} == {"aaa", "ccc"}


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
