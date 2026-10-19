from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

from app.services.action_layer.spec import ensure_builtin_actions, registry

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "action_planner_eval.json"


def script():
    spec = importlib.util.spec_from_file_location("add_eval_case_under_test", ROOT / "scripts" / "add_eval_case.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def m():
    ensure_builtin_actions()
    return script()


@pytest.fixture
def fx(tmp_path):
    path = tmp_path / "eval.json"
    shutil.copy(FIXTURE, path)
    return path


def plan_case(**over) -> dict:
    case = {"id": "swap_k9", "text": "поменяй шоты в девятом кадре, пожалуйста", "workflow": "swap", "context": {},
            "outcome": "plan", "steps": [{"action_id": "montage.swap_shots",
                                          "params": {"frame_number": 9, "kind": "both"}}], "also_offered": []}
    case.update(over)
    return case


def ids(path: Path) -> list[str]:
    return [c["id"] for c in json.loads(path.read_text(encoding="utf-8"))["cases"]]



def test_every_existing_fixture_case_passes_the_validator(m) -> None:
    cases = m.load_fixture(FIXTURE)["cases"]
    bad = {c["id"]: p for c in cases if (p := m.validate_case(c, cases, registry, check_duplicates=False))}
    assert not bad, bad


def test_fixture_file_is_in_the_canonical_format_so_appending_changes_one_entry(m) -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    assert m.dump_fixture(json.loads(text)) == text


def test_adding_a_case_only_appends_to_the_file(m, fx) -> None:
    before = fx.read_text(encoding="utf-8")
    errors, _warn = m.add_case(plan_case(), fx)
    assert errors == []
    after = fx.read_text(encoding="utf-8")
    old_tail = before[: before.rindex("\n ]\n}")]
    assert after.startswith(old_tail + ",\n")
    assert ids(fx)[-1] == "swap_k9" and len(ids(fx)) == len(json.loads(before)["cases"]) + 1
    added = json.loads(after)["cases"][-1]
    assert list(added) == ["id", "text", "workflow", "context", "outcome", "steps", "also_offered"]



def test_clarify_and_refuse_cases_are_accepted(m, fx) -> None:
    ok = {"id": "vague_fix", "text": "поправь там что-нибудь", "workflow": "ambiguous", "outcome": "clarify",
          "clarify_contains": ["какой"]}
    assert m.add_case(ok, fx)[0] == []
    no = {"id": "no_push", "text": "залей всё на github", "workflow": "out_of_catalog", "outcome": "refuse"}
    assert m.add_case(no, fx)[0] == []
    last = json.loads(fx.read_text(encoding="utf-8"))["cases"][-2:]
    assert [c["outcome"] for c in last] == ["clarify", "refuse"] and last[0]["steps"] == [] and last[0]["context"] == {}


def test_plan_needs_steps_and_other_outcomes_forbid_them(m, fx) -> None:
    errs, _ = m.add_case(plan_case(steps=[]), fx)
    assert any("хотя бы один шаг" in e for e in errs)
    errs, _ = m.add_case(plan_case(id="x1", outcome="refuse"), fx)
    assert any("шагов быть не должно" in e for e in errs)
    errs, _ = m.add_case(plan_case(id="x2", outcome="maybe"), fx)
    assert any("outcome" in e for e in errs)
    errs, _ = m.add_case(plan_case(id="x3", clarify_contains=["слово"]), fx)
    assert any("clarify_contains" in e for e in errs)
    errs, _ = m.add_case(plan_case(id="x4", outcome="clarify", steps=[], explanation_contains=["слово"]), fx)
    assert any("explanation_contains" in e for e in errs)



def test_duplicate_id_and_text_are_refused_and_the_file_is_untouched(m, fx) -> None:
    before = fx.read_bytes()
    first = json.loads(before)["cases"][0]
    errs, _ = m.add_case(plan_case(id=first["id"]), fx)
    assert any("уже есть" in e for e in errs)
    errs, _ = m.add_case(plan_case(id="fresh_id", text=first["text"].upper()), fx)
    assert any("такая фраза уже есть" in e for e in errs)
    assert fx.read_bytes() == before


def test_second_add_of_the_same_id_is_a_duplicate(m, fx) -> None:
    assert m.add_case(plan_case(), fx)[0] == []
    errs, _ = m.add_case(plan_case(text="другая фраза про шоты кадра девять"), fx)
    assert any("уже есть" in e for e in errs)
    assert ids(fx).count("swap_k9") == 1


@pytest.mark.parametrize("bad", ["", "Swap", "a b", "кириллица", "x" * 80, "-start", None])
def test_bad_ids_are_refused(m, fx, bad) -> None:
    errs, _ = m.add_case(plan_case(id=bad), fx)
    assert any(e.startswith("id:") for e in errs)



def test_unknown_action_and_unknown_param_and_bad_value_are_refused(m, fx) -> None:
    errs, _ = m.add_case(plan_case(steps=[{"action_id": "montage.nope", "params": {}}]), fx)
    assert any("нет в реестре" in e for e in errs)
    errs, _ = m.add_case(plan_case(steps=[{"action_id": "montage.swap_shots", "params": {"frame": 9}}]), fx)
    assert any("нет параметра 'frame'" in e and "frame_number" in e for e in errs)
    errs, _ = m.add_case(plan_case(steps=[{"action_id": "montage.swap_shots",
                                           "params": {"frame_number": "девять"}}]), fx)
    assert any("montage.swap_shots" in e and "frame_number" in e for e in errs)
    errs, _ = m.add_case(plan_case(steps=[{"action_id": "montage.swap_shots",
                                           "params": {"frame_number": 9, "kind": "всё"}}]), fx)
    assert errs
    errs, _ = m.add_case(plan_case(also_offered=["montage.nope"]), fx)
    assert any("also_offered" in e for e in errs)
    errs, _ = m.add_case(plan_case(extra="x"), fx)
    assert any("лишние поля" in e for e in errs)
    errs, _ = m.add_case(plan_case(steps=[{"action_id": "montage.swap_shots", "params": {}, "why": "x"}]), fx)
    assert errs
    assert "swap_k9" not in ids(fx)


def test_placeholders_must_be_known_to_the_seed(m, fx) -> None:
    step = {"action_id": "montage.delete_frame", "params": {"frame_id": "$frame.22"}}
    assert m.add_case(plan_case(id="ph_ok", text="убери кадр двадцать два совсем", steps=[step]), fx)[0] == []
    bad = {"action_id": "montage.delete_frame", "params": {"frame_id": "$frame.99"}}
    errs, _ = m.add_case(plan_case(id="ph_bad", text="убери кадр девяносто девять", steps=[bad]), fx)
    assert any("неизвестный плейсхолдер" in e for e in errs)


def test_upload_step_needs_an_attachment_and_attachments_prefix(m, fx) -> None:
    step = {"action_id": "montage.upload_voice", "params": {"source_path": "attachments/"}}
    errs, _ = m.add_case(plan_case(id="up1", text="загрузи озвучку из файла", steps=[step]), fx)
    assert any("context.attachments" in e for e in errs)
    ctx = {"attachments": [{"kind": "audio", "name": "voice.mp3", "size": 2400000}]}
    assert m.add_case(plan_case(id="up2", text="загрузи озвучку из файла", steps=[step], context=ctx,
                                workflow="upload attached", explanation_contains=["voice.mp3"]), fx)[0] == []
    abs_path = {"action_id": "montage.upload_voice", "params": {"source_path": "C:/secret/evil.mp3"}}
    errs, _ = m.add_case(plan_case(id="up3", text="загрузи озвучку с диска", steps=[abs_path], context=ctx), fx)
    assert any("attachments/" in e for e in errs)


def test_context_is_validated(m, fx) -> None:
    errs, _ = m.add_case(plan_case(id="c1", context={"selected_frame": 0}), fx)
    assert any("selected_frame" in e for e in errs)
    errs, _ = m.add_case(plan_case(id="c2", context={"colour": "red"}), fx)
    assert any("context.colour" in e for e in errs)
    errs, _ = m.add_case(plan_case(id="c3", context={"attachments": [{"kind": "exe", "name": "a.exe"}]}), fx)
    assert any("attachments[0]" in e for e in errs)
    ok = plan_case(id="c4", text="поменяй шоты у выбранного кадра", context={"selected_frame": 4, "status": "idle"})
    assert m.add_case(ok, fx)[0] == []



def test_prefilter_miss_is_a_warning_not_an_error(m, fx) -> None:
    case = plan_case(id="odd", text="абракадабра", steps=[{"action_id": "montage.delete_frame",
                                                          "params": {"frame_id": "$frame.5"}}])
    errs, warns = m.add_case(case, fx, dry_run=True)
    assert errs == [] and warns and "предфильтр" in warns[0] and "montage.delete_frame" in warns[0]
    assert "odd" not in ids(fx)



def test_cli_adds_a_plan_case_and_defaults_the_workflow(m, fx, capsys) -> None:
    code = m.main(["--fixture", str(fx), "--id", "cli_swap", "--text", "поменяй шоты девятого кадра местами",
                   "--step", "montage.swap_shots", '{"frame_number": 9}'])
    assert code == 0 and "Добавлено: cli_swap" in capsys.readouterr().out
    added = json.loads(fx.read_text(encoding="utf-8"))["cases"][-1]
    assert added["workflow"] == "montage" and added["steps"][0]["params"] == {"frame_number": 9}
    assert added["outcome"] == "plan" and added["context"] == {} and added["also_offered"] == []


def test_cli_context_attachments_and_extra_fields(m, fx) -> None:
    code = m.main(["--fixture", str(fx), "--id", "cli_att", "--text", "загрузи озвучку, вот файл", "--workflow",
                   "upload attached", "--attachment", "audio:voice.mp3:2400000", "--explanation-contains",
                   "voice.mp3", "--step", "montage.upload_voice", '{"source_path": "attachments/"}',
                   "--selected-frame", "3", "--also-offered", "montage.get_board,project.stop"])
    assert code == 0
    c = json.loads(fx.read_text(encoding="utf-8"))["cases"][-1]
    assert c["context"] == {"selected_frame": 3, "attachments": [{"kind": "audio", "name": "voice.mp3",
                                                                    "size": 2400000}]}
    assert c["also_offered"] == ["montage.get_board", "project.stop"] and c["explanation_contains"] == ["voice.mp3"]


def test_cli_json_file_and_failure_codes(m, fx, tmp_path, capsys) -> None:
    src = tmp_path / "case.json"
    src.write_text(json.dumps({"id": "from_json", "text": "залей проект на github", "workflow": "out_of_catalog",
                               "outcome": "refuse"}, ensure_ascii=False), encoding="utf-8")
    assert m.main(["--fixture", str(fx), "--json", str(src)]) == 0
    capsys.readouterr()
    before = fx.read_bytes()
    assert m.main(["--fixture", str(fx), "--json", str(src)]) == 1
    assert "ОШИБКА" in capsys.readouterr().err and fx.read_bytes() == before
    assert m.main(["--fixture", str(fx), "--id", "only_id"]) == 2
    assert m.main(["--fixture", str(fx), "--json", str(tmp_path / "missing.json")]) == 2
    assert m.main(["--fixture", str(fx), "--dry-run", "--id", "dry", "--text", "что-то про шоты кадра девять",
                   "--step", "montage.swap_shots", '{"frame_number": 9}']) == 0
    assert "dry" not in ids(fx)


def test_cli_strict_turns_prefilter_warning_into_failure(m, fx, capsys) -> None:
    args = ["--fixture", str(fx), "--id", "odd2", "--text", "абракадабра", "--step", "montage.delete_frame",
            '{"frame_id": "$frame.5"}']
    assert m.main(args) == 0 and "ВНИМАНИЕ" in capsys.readouterr().err
    assert m.main([*args[:3], "odd3", *args[4:], "--strict"]) == 1


def test_a_case_added_by_the_script_is_scored_by_the_eval_helpers(m, fx) -> None:
    assert m.add_case(plan_case(), fx)[0] == []
    spec = importlib.util.spec_from_file_location("eval_action_planner_for_add", ROOT / "scripts" / "eval_action_planner.py")
    ev = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ev
    spec.loader.exec_module(ev)
    cases = ev.load_cases(fx)
    new = next(c for c in cases if c["id"] == "swap_k9")
    got = [{"action_id": "montage.swap_shots", "params": {"project_id": 2, "frame_number": 9, "kind": "both"}}]
    assert ev.score_case(new, got, None, None)["ok"]
    assert not ev.score_case(new, [], "Какой кадр?", None)["ok"]
