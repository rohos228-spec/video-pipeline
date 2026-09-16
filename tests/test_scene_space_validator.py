"""CANON §6 validator: every rule_id, CLI exit codes."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.generation_options import OUTSEE_PROMPT_MAX_CHARS
from app.services.scene_space.validate import (
    compute_screen_pos_map,
    format_validation_md,
    has_negative_marker,
    has_style_marker,
    issues_exit_code,
    load_forbidden_phrases,
    split_prompt_positive,
    validate_scene,
)


def _load_cli():
    path = Path(__file__).resolve().parents[1] / "scripts" / "scene_space_validate.py"
    spec = importlib.util.spec_from_file_location("scene_space_validate_cli", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cli = _load_cli()

OK_PROMPT = (
    "PROMPT: a wooden table in a quiet room, one lamp, no letters\n"
    "NEGATIVE PROMPT: extra limbs, blur, watermark"
)


def _space(**extra):
    space = {
        "plan": [
            {"id": "c01", "x": 0.0, "y": 2.0, "facing": 0},
            {"id": "c02", "x": 1.0, "y": 2.0, "facing": 180},
            {"id": "cam", "x": 0.5, "y": 0.0, "facing": 0},
        ],
        "obstacles": [],
        "axes": [{"pair": ["c01", "c02"], "side_locked": "B"}],
        "value_charge": {"start": "+", "end": "-"},
        "input_hash": "test",
        "monotony_surface": "ground",
    }
    space.update(extra)
    return space


def _screen(plan=None):
    plan = plan or _space()["plan"]
    return compute_screen_pos_map(plan, "c01", "c02")


def _row(n: int, **extra):
    plan = extra.pop("plan", None) or _space()["plan"]
    row = {
        "uuid": f"{n:024x}",
        "scene_id": "fix:test",
        "shot_order": n,
        "axis_pair": "c01|c02",
        "axis_side": "B",
        "screen_pos": extra.pop("screen_pos", _screen(plan)),
        "screen_dir": "none",
        "shot_size": extra.pop("shot_size", "MS"),
        "angle_v": extra.pop("angle_v", "eye-level"),
        "angle_h": extra.pop("angle_h", "frontal"),
        "beat_role": extra.pop("beat_role", "beat"),
        "crossing_method": extra.pop("crossing_method", None),
        "space_delta_json": extra.pop("space_delta_json", {}),
        "reestablish_due": extra.pop("reestablish_due", 0),
        "manual": extra.pop("manual", 0),
    }
    row.update(extra)
    return row


def _frame(n: int, prompt: str = OK_PROMPT, accent: str | None = None, **extra):
    attrs = extra.pop("attrs", {})
    if accent is not None:
        attrs = {**attrs, "accent": accent}
    return {
        "uuid": f"{n:024x}",
        "image_prompt": extra.pop("image_prompt", prompt),
        "animation_prompt": extra.pop("animation_prompt", prompt),
        "attrs": attrs,
        **extra,
    }


def _frames(*ns, **kwargs):
    return {f"{n:024x}": _frame(n, **kwargs) for n in ns}


def _has(issues, rule_id, level=None):
    return any(
        i["rule_id"] == rule_id and (level is None or i["level"] == level) for i in issues
    )


def test_issue_shape_and_exit_codes():
    issues = validate_scene("fix:test", [], _space(), {})
    assert isinstance(issues, list)
    for item in issues:
        assert set(item) >= {"level", "rule_id", "shot_order", "message"}
    assert issues_exit_code([]) == 0
    assert issues_exit_code([{"level": "warning", "rule_id": "no_turn"}]) == 1
    assert issues_exit_code(
        [
            {"level": "warning", "rule_id": "no_turn"},
            {"level": "error", "rule_id": "size_same"},
        ]
    ) == 2


def test_missing_uuid():
    rows = [_row(1)]
    issues = validate_scene("fix:test", rows, _space(), {})
    assert _has(issues, "missing_uuid", "error")


def test_pair_norm_rejects_unsorted():
    rows = [_row(1, axis_pair="c02|c01")]
    issues = validate_scene("fix:test", rows, _space(), _frames(1))
    assert _has(issues, "pair_norm", "error")


def test_size_same_error():
    rows = [
        _row(1, shot_size="MS", space_delta_json={"cam": {"x": 0.5, "y": 0.0}}),
        _row(
            2,
            shot_size="MS",
            space_delta_json={"cam": {"x": -1.5, "y": 0.0}},
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert _has(issues, "size_same", "error")


def test_size_adjacent_warning():
    rows = [
        _row(1, shot_size="MS"),
        _row(2, shot_size="MCU", space_delta_json={"cam": {"x": -1.5, "y": 0.0}}),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert _has(issues, "size_adjacent", "warning")


def test_size_smash_without_insert():
    rows = [
        _row(1, shot_size="CU"),
        _row(
            2,
            shot_size="WS",
            beat_role="beat",
            space_delta_json={"cam": {"x": -1.5, "y": 0.0}},
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert _has(issues, "size_smash", "warning")


def test_size_smash_insert_ok():
    rows = [
        _row(1, shot_size="CU"),
        _row(
            2,
            shot_size="WS",
            beat_role="insert",
            space_delta_json={"cam": {"x": -1.5, "y": 0.0}},
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert not _has(issues, "size_smash")


def test_silent_side_cross():
    plan_b = [
        {"id": "c01", "x": 0.0, "y": 2.0, "facing": 0},
        {"id": "c02", "x": 1.0, "y": 2.0, "facing": 180},
        {"id": "cam", "x": 0.5, "y": 3.5, "facing": 0},
    ]
    rows = [
        _row(1, axis_side="B", screen_pos=_screen()),
        _row(
            2,
            axis_side="A",
            crossing_method=None,
            screen_pos=_screen(plan_b),
            space_delta_json={"cam": {"x": 0.5, "y": 3.5}},
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert _has(issues, "side_cross_silent", "error") or _has(issues, "side_lock", "error")


def test_crossing_method_allows_side_change():
    plan_b = [
        {"id": "c01", "x": 0.0, "y": 2.0, "facing": 0},
        {"id": "c02", "x": 1.0, "y": 2.0, "facing": 180},
        {"id": "cam", "x": 0.5, "y": 3.5, "facing": 0},
    ]
    rows = [
        _row(1, axis_side="B", shot_size="WS", screen_pos=_screen()),
        _row(
            2,
            axis_side="A",
            crossing_method="camera_move",
            shot_size="FS",
            screen_pos=_screen(plan_b),
            space_delta_json={"cam": {"x": 0.5, "y": 3.5}},
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert not _has(issues, "side_cross_silent")


def test_screen_pos_mismatch():
    rows = [_row(1, screen_pos={"c01": "R", "c02": "L"})]
    # default geometry is c01 L, c02 R for cam below
    if _screen() == {"c01": "R", "c02": "L"}:
        rows = [_row(1, screen_pos={"c01": "L", "c02": "R"})]
    issues = validate_scene("fix:test", rows, _space(), _frames(1))
    assert _has(issues, "screen_pos", "error")


def test_screen_dir_reverse_without_turn():
    rows = [
        _row(1, screen_dir="L→R", shot_size="WS"),
        _row(
            2,
            screen_dir="R→L",
            shot_size="FS",
            beat_role="beat",
            space_delta_json={"cam": {"x": -1.5, "y": 0.0}},
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert _has(issues, "screen_dir", "error")


def test_screen_dir_with_turn_ok():
    rows = [
        _row(1, screen_dir="L→R", shot_size="WS"),
        _row(
            2,
            screen_dir="R→L",
            shot_size="FS",
            beat_role="insert",
            space_delta_json={
                "cam": {"x": -1.5, "y": 0.0},
                "turn": {"subject": "c02"},
            },
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert not _has(issues, "screen_dir")


def test_angle_30_same_names_no_cam_move():
    rows = [
        _row(1, angle_h="frontal", angle_v="eye-level"),
        _row(
            2,
            shot_size="FS",
            angle_h="frontal",
            angle_v="eye-level",
            space_delta_json={},
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert _has(issues, "angle_30", "error")


def test_reestablish_due_unpaid():
    rows = [
        _row(1, shot_size="CU", reestablish_due=1),
        _row(
            2,
            shot_size="CU",
            space_delta_json={"cam": {"x": -1.5, "y": 0.0}},
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert _has(issues, "reestablish", "error")


def test_reestablish_paid_by_ws():
    rows = [
        _row(1, shot_size="CU", reestablish_due=1),
        _row(
            2,
            shot_size="WS",
            space_delta_json={"cam": {"x": -1.5, "y": 0.0}},
        ),
    ]
    issues = validate_scene("fix:test", rows, _space(), _frames(1, 2))
    assert not _has(issues, "reestablish", "error")


def test_degenerate_camera_on_axis():
    space = _space()
    space["plan"] = [
        {"id": "c01", "x": 0.0, "y": 0.0, "facing": 0},
        {"id": "c02", "x": 2.0, "y": 0.0, "facing": 180},
        {"id": "cam", "x": 1.0, "y": 0.0, "facing": 0},
    ]
    rows = [_row(1, screen_pos={"c01": "L", "c02": "R"}, plan=space["plan"])]
    issues = validate_scene("fix:test", rows, space, _frames(1))
    assert _has(issues, "degenerate", "error")


def test_schema_unknown_shot_size():
    rows = [_row(1, shot_size="VLS")]
    issues = validate_scene("fix:test", rows, _space(), _frames(1))
    assert _has(issues, "schema", "error")


def test_prompt_len():
    huge = "PROMPT: " + ("x" * (OUTSEE_PROMPT_MAX_CHARS + 10)) + "\nNEGATIVE PROMPT: blur"
    issues = validate_scene(
        "fix:test",
        [_row(1)],
        _space(),
        {f"{1:024x}": _frame(1, prompt=huge)},
    )
    assert _has(issues, "prompt_len", "error")


def test_prompt_forbidden():
    bad = "PROMPT: давайте разберёмся how the table works\nNEGATIVE PROMPT: blur"
    issues = validate_scene(
        "fix:test",
        [_row(1)],
        _space(),
        {f"{1:024x}": _frame(1, prompt=bad)},
    )
    assert _has(issues, "prompt_forbidden", "error")


def test_prompt_forbidden_ignores_negative():
    text = (
        "PROMPT: a quiet wooden table in a room\n"
        "NEGATIVE PROMPT: давайте разберёмся, extra limbs"
    )
    issues = validate_scene(
        "fix:test",
        [_row(1)],
        _space(),
        {f"{1:024x}": _frame(1, prompt=text)},
    )
    assert not _has(issues, "prompt_forbidden")


def test_prompt_style_missing():
    issues = validate_scene(
        "fix:test",
        [_row(1)],
        _space(),
        {f"{1:024x}": _frame(1, prompt="just a table, no markers")},
    )
    assert _has(issues, "prompt_style", "error")


def test_no_turn_warning():
    space = _space(value_charge={"start": "+", "end": "+"})
    issues = validate_scene("fix:test", [_row(1)], space, _frames(1))
    assert _has(issues, "no_turn", "warning")


def test_interest_warnings_on_thin_scene():
    issues = validate_scene("fix:test", [_row(1)], _space(), _frames(1, accent="hands"))
    assert _has(issues, "interest_sizes", "warning")
    assert _has(issues, "interest_turning_point", "warning")
    assert _has(issues, "interest_reaction", "warning")


def test_interest_accent_repeat():
    rows = [
        _row(1, shot_size="WS"),
        _row(2, shot_size="MS", space_delta_json={"cam": {"x": -1.5, "y": 0.0}}),
        _row(3, shot_size="CU", space_delta_json={"cam": {"x": 0.5, "y": -2.0}}),
    ]
    frames = {
        f"{1:024x}": _frame(1, accent="hands"),
        f"{2:024x}": _frame(2, accent="hands"),
        f"{3:024x}": _frame(3, accent="eyes"),
    }
    issues = validate_scene("fix:test", rows, _space(), frames)
    assert _has(issues, "interest_accent", "warning")


def test_prompt_helpers_contract_section_4():
    text = "PROMPT: cat\nNEGATIVE: blur"
    assert has_style_marker(text)
    assert has_negative_marker(text)
    assert "NEGATIVE" not in split_prompt_positive(text)
    phrases = load_forbidden_phrases()
    assert any("давайте разберёмся" in p or "секрет прост" in p.casefold() for p in phrases)


def test_cli_fixtures_writes_report(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "FIXTURE_DIR", tmp_path / "missing")
    out = tmp_path / "VALIDATION.md"
    monkeypatch.setattr(cli, "REPORT_PATH", out)
    code = cli.main(["--fixtures", "--out", str(out)])
    assert out.is_file()
    assert code == 0
    body = out.read_text(encoding="utf-8")
    assert body.startswith("# VALIDATION")


def test_cli_fixtures_exit_2_on_error(tmp_path, monkeypatch):
    folder = tmp_path / "fx"
    folder.mkdir()
    payload = {
        "scene_id": "fix:bad",
        "space_json": _space(),
        "frames_space": [_row(1, shot_size="MS"), _row(2, shot_size="MS")],
        "frames": [_frame(1), _frame(2)],
    }
    (folder / "bad.json").write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(cli, "FIXTURE_DIR", folder)
    out = tmp_path / "VALIDATION.md"
    code = cli.main(["--fixtures", "--out", str(out)])
    assert code == 2
    assert "`size_same`" in out.read_text(encoding="utf-8")


def test_cli_scene_without_store_exits_2(tmp_path, monkeypatch):
    async def boom(scene_id: str):
        raise RuntimeError("store missing: cannot load --scene")

    monkeypatch.setattr(cli, "_load_from_store", boom)
    out = tmp_path / "VALIDATION.md"
    code = cli.main(["--scene", "p13:s4", "--out", str(out)])
    assert code == 2


def test_format_validation_md_roundtrip():
    md = format_validation_md(
        [
            (
                "fix:dialogue",
                [
                    {
                        "level": "error",
                        "rule_id": "size_same",
                        "shot_order": 2,
                        "message": "одинаковая крупность",
                    }
                ],
            )
        ]
    )
    assert "| `size_same` |" in md


def test_compute_screen_pos_stream1_example():
    plan = [
        {"id": "c01", "x": 0.0, "y": 2.0, "facing": 0},
        {"id": "c02", "x": 1.0, "y": 2.0, "facing": 180},
        {"id": "cam", "x": 0.5, "y": 0.0, "facing": 0},
    ]
    assert compute_screen_pos_map(plan, "c01", "c02") == {"c01": "L", "c02": "R"}
