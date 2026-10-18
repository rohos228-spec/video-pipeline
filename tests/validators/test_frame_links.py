"""FrameText и FrameEdge не исчезают. voiceover → vo_archived допустим."""

from __future__ import annotations

import copy

from app.orchestrator.validators.frame_links import check_frame_links


def _side(
    texts: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> dict:
    payload: dict = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "кадры": [{"id": "k-off", "активен": False, "закадр": ""}],
            }
        ]
    }
    if texts is not None:
        payload["frame_texts"] = texts
    if edges is not None:
        payload["frame_edges"] = edges
    return payload


def test_no_deletions_frame_texts_and_edges() -> None:
    texts = [{"id": 1, "frame_id": 10, "kind": "voiceover", "text": "привет"}]
    edges = [{"id": 7, "from_frame_id": 10, "to_frame_id": 11, "type": "next"}]
    before = _side(texts, edges)
    same = copy.deepcopy(before)
    assert check_frame_links(before, same) == []

    archived = _side(
        [{"id": 1, "frame_id": 10, "kind": "vo_archived", "text": "привет"}],
        edges,
    )
    assert check_frame_links(before, archived) == []

    rewritten = _side(
        [{"id": 2, "frame_id": 10, "kind": "vo_archived", "text": "привет"}],
        edges,
    )
    assert check_frame_links(before, rewritten) == []

    gone = _side([], [])
    lost = check_frame_links(before, gone)
    assert {(item.code, item.severity, item.id) for item in lost} == {
        ("frame_text_deleted", "error", "1"),
        ("frame_edge_deleted", "error", "7"),
    }

    inactive = copy.deepcopy(before)
    inactive["сцены"][0]["кадры"][0]["закадр"] = "хвост не в живом шоте"
    assert check_frame_links(before, inactive) == []
