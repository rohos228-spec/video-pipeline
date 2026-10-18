"""Неблокирующее ревью #352: английские ключи меню камеры не выкидываются."""

from __future__ import annotations


def test_english_camera_menu_keys_survive_prompt_filter() -> None:
    from app.services.node_write_contract import filter_ops_for_node

    ops = [
        {
            "frame_uuid": "u1",
            "fields": {"shot_size": "крупный", "move": "наезд", "set": "A"},
        }
    ]
    out = filter_ops_for_node(ops, node_kind="excel_gpt_prompts")
    assert len(out) == 1
    assert out[0]["fields"] == {"shot_size": "крупный", "move": "наезд", "set": "A"}
