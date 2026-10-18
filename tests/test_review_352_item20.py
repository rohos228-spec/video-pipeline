"""№20: пустые fields после фильтра не попадают в apply ни для какого target."""

from __future__ import annotations

from app.services.node_write_contract import filter_ops_for_node


def test_empty_kept_fields_dropped_for_project_and_frame_kept() -> None:
    ops = [
        {
            "target": "project",
            "fields": {"промт_картинки": "только картинка"},
        },
        {
            "target": "frame",
            "frame_uuid": "aa",
            "fields": {"место": "кухня", "промт_картинки": "нет"},
        },
    ]
    out = filter_ops_for_node(ops, node_kind="excel_gpt_no_prompts")
    assert len(out) == 1
    assert out[0]["target"] == "frame"
    assert out[0]["fields"] == {"место": "кухня"}
