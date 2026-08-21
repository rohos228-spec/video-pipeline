from app.services.shot_menu import build_shot_menu, group_vo_cells


def test_adjacent_vo_cells_are_not_glued() -> None:
    frames = [
        {"id": 1, "number": 1, "sort_key": 10, "voiceover_text": "Первая ячейка", "duration_seconds": 2},
        {"id": 2, "number": 2, "sort_key": 20, "voiceover_text": "Вторая ячейка", "duration_seconds": 3},
    ]
    cells = group_vo_cells(frames)
    assert len(cells) == 2
    assert cells[0]["voiceover"] == "Первая ячейка"
    assert cells[1]["voiceover"] == "Вторая ячейка"
    assert [s["label"] for s in cells[0]["shots"]] == ["1.1"]
    assert [s["label"] for s in cells[1]["shots"]] == ["2.1"]


def test_empty_vo_shots_attach_to_parent_cell() -> None:
    frames = [
        {
            "id": 1,
            "number": 1,
            "sort_key": 10,
            "voiceover_text": "Что, если самый страшный адрес",
            "duration_seconds": 2.4,
            "attrs": {"shot01_action": "Людмила заслоняет", "place": "SET_32"},
        },
        {
            "id": 2,
            "number": 2,
            "sort_key": 20,
            "voiceover_text": "",
            "duration_seconds": 2.1,
            "attrs": {"shot01_action": "Соседка хватает рукав", "place": "SET_37"},
        },
        {
            "id": 3,
            "number": 3,
            "sort_key": 30,
            "voiceover_text": "Новокузнецк, дом № 53",
            "duration_seconds": 3.0,
            "place": "SET_22",
        },
    ]
    cells = group_vo_cells(frames)
    assert len(cells) == 2
    assert len(cells[0]["shots"]) == 2
    assert cells[0]["shots"][0]["voiceover_in_shot"] != "—"
    assert cells[0]["shots"][1]["voiceover_in_shot"] == "—"
    assert cells[0]["shots"][1]["fields"]["action"] == "Соседка хватает рукав"
    assert cells[0]["loc"] == "SET_32"
    assert len(cells[1]["shots"]) == 1
    assert cells[1]["voiceover"].startswith("Новокузнецк")


def test_leading_empty_shots_do_not_eat_next_vo() -> None:
    frames = [
        {"id": 1, "number": 1, "voiceover_text": "", "duration_seconds": 1},
        {"id": 2, "number": 2, "voiceover_text": "Настоящий закадр", "duration_seconds": 2},
    ]
    cells = group_vo_cells(frames)
    assert len(cells) == 2
    assert cells[0]["voiceover"] == ""
    assert cells[1]["voiceover"] == "Настоящий закадр"


def test_shot_menu_is_menu_not_work_step() -> None:
    from app.orchestrator.graph.planner import is_side_sink_node_type
    from app.orchestrator.node_registry import (
        is_side_node_type,
        is_ui_menu_node_type,
        is_work_node_type,
    )

    assert is_ui_menu_node_type("shot_menu")
    assert is_side_node_type("shot_menu")
    assert is_side_sink_node_type("shot_menu")
    assert not is_work_node_type("shot_menu")


def test_build_shot_menu_summary() -> None:
    frames = [
        {"id": 1, "number": 1, "voiceover_text": "aaa", "duration_seconds": 1.5},
        {"id": 2, "number": 2, "voiceover_text": "", "duration_seconds": 2.5},
        {"id": 3, "number": 3, "voiceover_text": "bb", "duration_seconds": 4},
    ]
    menu = build_shot_menu(frames)
    assert menu["summary"]["vo_cells"] == 2
    assert menu["summary"]["shots"] == 3
    assert menu["summary"]["duration_sec"] == 8.0
    assert menu["summary"]["vo_chars"] == 5
    assert any(t["key"] == "vo" and t["pinned"] for t in menu["tracks"])
    assert "vo" in menu["default_tracks"]
