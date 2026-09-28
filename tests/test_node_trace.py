"""Дневник кадра, сохранение ответов GPT, повтор без GPT, дневник в отчёте."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.apply_ops_batches import run_apply_ops_batched, run_code_passes
from app.services.node_replay import replay_run, resolve_run_dir
from app.services.node_trace import (
    NodeRunRecorder,
    diff_ops,
    latest_diary,
    list_runs,
    read_diary,
)
from app.services.scene_plan import freeze_decision, needs_freeze
from app.services.shots_report import (
    build_shots_report_model,
    render_shots_report_html,
    write_shots_report,
)
from tests.test_scene_plan import PLAN, _hall, _street

UID = "e8ca9ac60d0e4c659e2610b4"
NODE = "n_excel_gpt_fw_shots"
VO = (
    "Он бежал по улице, не разбирая дороги, к своему дому. "
    "Влетел в прихожую, тяжело дыша,"
)


def _chunk() -> list[dict]:
    return [{"uuid": UID, "number": 1, "voiceover_text": VO}]


def _gpt_ops() -> list[dict]:
    return [{
        "frame_uuid": UID,
        "fields": {"кадры": [_street(), _hall()], "площадка": copy.deepcopy(PLAN)},
    }]


def test_diff_ops_fields_dicts_and_shots() -> None:
    before = [{"frame_uuid": "u", "fields": {
        "a": 1,
        "площадка": {"зоны": [], "люди": []},
        "кадры": [{"id": "K1", "план": "ОБЩИЙ"}, {"id": "K2", "план": "СРЕДНИЙ"}],
    }}]
    after = [{"frame_uuid": "u", "fields": {
        "a": 2,
        "площадка": {"зоны": [], "люди": ["Иван"]},
        "кадры": [
            {"id": "K1", "план": "КРУПНЫЙ"},
            {"id": "K1b", "план": "ДЕТАЛЬ"},
            {"id": "K2", "план": "СРЕДНИЙ"},
        ],
    }}]
    got = {(c.field, c.shot, c.shot_id): (c.before, c.after) for c in diff_ops(before, after)}
    assert got[("a", None, "")] == (1, 2)
    assert got[("площадка.люди", None, "")] == ([], ["Иван"])
    assert got[("план", 1, "K1")] == ("ОБЩИЙ", "КРУПНЫЙ")
    assert got[("кадр", 2, "K1b")][1].startswith("добавлен")
    assert len(got) == 4
    assert diff_ops(after, after) == []


def test_diff_ops_duplicate_ids_fall_back_to_position() -> None:
    before = [{"frame_uuid": "u", "fields": {"кадры": [{"id": "K", "x": 1}, {"id": "K", "x": 2}]}}]
    after = [{"frame_uuid": "u", "fields": {"кадры": [{"id": "K", "x": 1}]}}]
    changes = diff_ops(before, after)
    assert [(c.field, c.shot, c.after) for c in changes] == [("кадр", 2, "удалён")]


def test_code_passes_diary_explains_scene_plan_fixes() -> None:
    ops = _gpt_ops()
    res = run_code_passes(ops, _chunk(), kind="shots", all_frames=_chunk())
    by_rule = {}
    for e in res.entries:
        by_rule.setdefault(e["rule"], []).append(e)
    door = by_rule["R-DOOR-STATE"][0]
    assert door["shot"] == 1 and door["field"] == "действие"
    assert "закрыт" in door["after"] and "дверь" in door["note"]
    added = by_rule["R-DOOR-THRESHOLD"][0]
    assert added["shot_id"] == "1-S1-K2" and added["after"].startswith("добавлен")
    inside = by_rule["R-ALREADY-INSIDE"][0]
    assert inside["shot"] == 3 and "уже" in inside["note"]
    freeze = [e for e in by_rule["R-FREEZE-PRECISE"] if e["shot"] == 2]
    assert freeze and "входная дверь" in freeze[0]["note"]
    assert all(e["pass"] and e["kind"] in {"fix", "warn", "info"} for e in res.entries)
    assert len(res.ops[0]["fields"]["кадры"]) == 3


def test_code_passes_same_result_as_before_refactor() -> None:
    """Дневник не меняет ops: два прогона с одного ответа GPT дают одно и то же."""
    a = run_code_passes(_gpt_ops(), _chunk(), kind="shots", all_frames=_chunk())
    b = run_code_passes(_gpt_ops(), _chunk(), kind="shots", all_frames=_chunk())
    assert a.ops == b.ops and a.entries == b.entries


def test_freeze_decision_matches_needs_freeze() -> None:
    shots = [
        {"действие": "Иван открывает дверь", "меняет": [{"предмет": "дверь", "состояние": "открыта"}]},
        {"действие": "Иван идёт по коридору"},
        {"действие": "Ткач кладёт папку на стол", "меняет": [{"предмет": "папка"}]},
    ]
    for sh in shots:
        flag, why = freeze_decision(sh)
        assert flag == needs_freeze(sh)
        assert why


def test_recorder_writes_calls_and_prunes(tmp_path) -> None:
    for i in range(12):
        rec = NodeRunRecorder.start(tmp_path, NODE, kind="shots", all_frames=_chunk())
        rec.record_call(
            call=1, level=1, chunk=_chunk(), reply_text=f"reply {i}",
            ops_gpt=[{"frame_uuid": UID, "fields": {}}], ops_final=[],
            entries=[{"kind": "fix", "rule": "R-UUID", "frame_uuid": UID}],
        )
        rec.finish(ok=True)
    runs = list_runs(tmp_path)
    assert len(runs) == 10
    last = runs[-1]
    assert last["status"] == "ok" and last["calls"] == 1 and last["counts"] == {"fix": 1}
    call_dir = Path(last["dir"]) / "call_01_L1"
    assert (call_dir / "reply.txt").read_text(encoding="utf-8") == "reply 11"
    diary = latest_diary(tmp_path)
    assert [d["rule"] for d in diary] == ["R-UUID"]
    assert diary[0]["node_key"] == NODE and diary[0]["call"] == "call_01_L1"


def test_recorder_never_breaks_node(tmp_path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    rec = NodeRunRecorder.start(blocker, NODE, kind="shots", all_frames=[])
    assert rec.dir is None
    rec.record_call(call=1, level=1, chunk=[], reply_text="", ops_gpt=[], ops_final=[], entries=[])
    rec.finish(ok=False, error="boom")


def _fake_operator(monkeypatch, ops: list[dict]) -> None:
    from app.services.gpt_operator_client import OperatorApiResult

    async def fake_run(**kwargs):
        path = kwargs["input_paths"][0]
        return OperatorApiResult(
            reply_text=json.dumps({"ops": ops}, ensure_ascii=False),
            output_paths=[path],
            apply_ops={"ops": copy.deepcopy(ops)},
        )

    monkeypatch.setattr("app.services.apply_ops_batches.run_operator_api", fake_run)


async def _run_node(tmp_path, *, footer_kind: str, frames: list[dict]):
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    return await run_apply_ops_batched(
        project_dir=tmp_path,
        node_key=NODE,
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=False,
        chunk_size=8,
        footer_kind=footer_kind,
    )


@pytest.mark.asyncio
async def test_live_node_saves_trace_and_replay_matches(tmp_path, monkeypatch) -> None:
    _fake_operator(monkeypatch, _gpt_ops())
    res = await _run_node(tmp_path, footer_kind="shots", frames=_chunk())
    run_dir = resolve_run_dir(tmp_path, node="shots")
    meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    assert meta["status"] == "ok" and meta["kind"] == "shots" and meta["calls"] == 1
    call_dir = next(run_dir.glob("call_*"))
    assert json.loads((call_dir / "ops_gpt.json").read_text(encoding="utf-8")) == _gpt_ops()
    final = json.loads((call_dir / "ops_final.json").read_text(encoding="utf-8"))
    assert final == res.apply_ops["ops"]
    assert "R-DOOR-THRESHOLD" in {e["rule"] for e in read_diary(run_dir)}

    reps = replay_run(run_dir)
    assert len(reps) == 1 and reps[0].same, reps[0].diff

    (call_dir / "ops_final.json").write_text(
        json.dumps([{"frame_uuid": UID, "fields": {"кадры": []}}]), encoding="utf-8"
    )
    assert not replay_run(run_dir)[0].same


@pytest.mark.asyncio
async def test_live_node_stop_is_recorded(tmp_path, monkeypatch) -> None:
    from app.services import adaptive_llm_batches

    monkeypatch.setattr(adaptive_llm_batches, "next_split_level", lambda _level: None)
    same = {"место": "ворота", "смысл_сцены": "одно и то же"}
    _fake_operator(monkeypatch, [
        {"frame_uuid": "aaaa1111", "fields": dict(same)},
        {"frame_uuid": "bbbb2222", "fields": dict(same)},
    ])
    frames = [
        {"uuid": "aaaa1111", "voiceover_text": "жалобы"},
        {"uuid": "bbbb2222", "voiceover_text": "в имениях"},
    ]
    with pytest.raises(RuntimeError, match="скопирован"):
        await _run_node(tmp_path, footer_kind="analytics", frames=frames)
    run = list_runs(tmp_path)[-1]
    assert run["status"] == "failed" and "скопирован" in run["error"]
    run_dir = resolve_run_dir(tmp_path)
    call_dir = next(run_dir.glob("call_*"))
    assert (call_dir / "error.txt").is_file()
    assert not (call_dir / "ops_final.json").exists()
    stops = [e for e in read_diary(run_dir) if e["kind"] == "stop"]
    assert stops and stops[0]["rule"] == "R-ANALYTICS-COLLAPSE"
    rep = replay_run(run_dir)[0]
    assert rep.stopped and rep.same


def _report_frame() -> SimpleNamespace:
    return SimpleNamespace(
        number=1,
        uuid=UID,
        voiceover_text=VO,
        attrs={"кадры": [
            {"id": "1-S1-K1", "действие": "Иван бежит", "сцена": 1},
            {"id": "1-S1-K2", "действие": "Иван открывает дверь", "сцена": 1},
        ]},
    )


def test_report_shows_diary_runs_check_and_rules() -> None:
    diary = [
        {"frame_uuid": UID, "shot": 2, "shot_id": "1-S1-K2", "field": "кадр",
         "before": None, "after": "добавлен: Иван открывает дверь", "rule": "R-DOOR-THRESHOLD",
         "pass": "scene_plan", "kind": "fix", "note": "дверь была закрыта",
         "node_key": NODE},
        {"frame_uuid": UID, "shot": None, "shot_id": "", "field": "площадка.люди",
         "before": None, "after": "[]", "rule": "R-SCENE-PLAN", "pass": "scene_plan",
         "kind": "fix", "note": "", "node_key": NODE},
        {"frame_uuid": "zzzz", "shot": None, "field": "", "rule": "R-UUID",
         "kind": "warn", "note": "чужой uuid", "node_key": NODE},
    ]
    runs = [
        {"node_key": "n_excel_gpt_fw_script", "status": "ok", "calls": 1, "counts": {}},
        {"node_key": "n_excel_gpt_fw_script", "status": "ok", "calls": 1, "counts": {"warn": 2}},
        {"node_key": NODE, "status": "ok", "calls": 1, "counts": {"fix": 2}, "dir": "/x"},
    ]
    check = {"verdict": "fail", "summary": "биты слабые",
             "checks": [{"id": "B1", "ok": False, "note": "нет объекта"}]}
    model = build_shots_report_model([_report_frame()], diary=diary, runs=runs, check=check)
    rows = {r["id"]: r for r in model["shots"]}
    assert [d["rule"] for d in rows["1-S1-K2"]["diary"]] == ["R-DOOR-THRESHOLD"]
    assert rows["1-S1-K1"]["diary"] == []
    assert [d["rule"] for d in model["scenes"][0]["diary"]] == ["R-SCENE-PLAN"]
    assert [d["rule"] for d in model["diary_orphans"]] == ["R-UUID"]

    html_text = render_shots_report_html(model, slug="t")
    assert "Дневник кадра (1)" in html_text
    assert "дверь была закрыта" in html_text
    assert "Что поменяла программа в ячейке (1)" in html_text
    assert "Правки программы без кадра в отчёте (1)" in html_text
    assert "Дневник группы нод" in html_text
    assert "Не ок — вернула на сценарий" in html_text and "нет объекта" in html_text
    assert "Сценарий писался 2 раз(а)" in html_text
    assert "возвращала его на доработку 1 раз(а)" in html_text
    assert "заметила 2" in html_text
    assert "починила 2" in html_text
    assert "Все правила программы" in html_text and "R-AXIS-180" in html_text
    assert "Кто по очереди пишет одно поле" in html_text


def test_report_without_diary_still_renders() -> None:
    html_text = render_shots_report_html(build_shots_report_model([_report_frame()]))
    assert "Дневника ещё нет" in html_text
    assert "Дневник кадра" not in html_text


def test_write_report_loads_trace_and_check(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("app.services.shots_report.find_project_root", lambda: tmp_path)
    rec = NodeRunRecorder.start(tmp_path, NODE, kind="shots", all_frames=[])
    rec.record_call(
        call=1, level=1, chunk=[], reply_text="", ops_gpt=[], ops_final=[],
        entries=[{"frame_uuid": UID, "shot": 2, "shot_id": "1-S1-K2", "field": "план",
                  "before": "ОБЩИЙ", "after": "СРЕДНИЙ", "rule": "R-PLAN-STEP",
                  "pass": "scene_plan", "kind": "fix", "note": ""}],
    )
    rec.finish(ok=True)
    other = NodeRunRecorder.start(tmp_path, "n_excel_gpt_2", kind="", all_frames=[])
    other.finish(ok=True)
    up = tmp_path / "excel_gpt_uploads" / "n_excel_gpt_fw_check_script"
    up.mkdir(parents=True)
    (up / "analysis.json").write_text(json.dumps({"verdict": "pass", "summary": "ок"}), encoding="utf-8")
    project = SimpleNamespace(slug="t", id=7, data_dir=tmp_path)
    paths = write_shots_report(project, [_report_frame()])
    html_text = paths[0].read_text(encoding="utf-8")
    assert "Ок — пропустила дальше" in html_text
    assert "ОБЩИЙ" in html_text and "R-PLAN-STEP" in html_text
    assert "n_excel_gpt_2" not in html_text
