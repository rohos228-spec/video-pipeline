"""Ветки предикатов, снимка и реестра, которые основной набор не задевает."""

from __future__ import annotations

import importlib
import sqlite3
import sys
import types

import pytest

from app.orchestrator.validators import run
from app.orchestrator.validators.grammar import (
    local_ensure_wide_before_dialogue,
    local_merge_trivial_shots,
    merge_notes,
    shots_of,
    upstream_grammar,
    wide_notes,
)
from app.orchestrator.validators.result import is_dry_run
from app.orchestrator.validators.snapshot import _select_ids, load_project_snapshot


def _shot(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "a",
        "план": "СРЕДНИЙ",
        "ракурс": "фронт",
        "кто": "c01",
        "действие": "сидит",
        "место": "кухня",
        "объект": "стол",
        "реплика": "",
        "говорит": "",
    }
    base.update(kwargs)
    return base


def test_merge_predicate_rejects_nontrivial_pairs() -> None:
    assert local_merge_trivial_shots([_shot()]) == []
    assert local_merge_trivial_shots(["не словарь", _shot()]) == []
    reply_next = [_shot(), _shot(id="b"), _shot(id="c", реплика="нет", говорит="Анна")]
    assert local_merge_trivial_shots(reply_next) == []
    assert local_merge_trivial_shots([_shot(), _shot(id="b", объект="взгляд")]) == []
    assert local_merge_trivial_shots([_shot(), _shot(id="b", кто="")]) == []
    assert local_merge_trivial_shots([_shot(действие="замечает дверь"), _shot(id="b")]) == []
    assert local_merge_trivial_shots([_shot(закадр="был"), _shot(id="b", закадр="стал")]) == []
    assert local_merge_trivial_shots([_shot(место="кухня"), _shot(id="b", место="двор")]) == []
    assert local_merge_trivial_shots([_shot(кто="c02 и c01"), _shot(id="b", кто="Анна и Борис")]) == []
    assert local_merge_trivial_shots([_shot(действие="c05 машет"), _shot(id="b", действие="123")]) == []
    assert (
        local_merge_trivial_shots(
            [_shot(план="КРУПНЫЙ", ракурс="сбоку", объект="чашка"), _shot(id="b", объект="лампа")]
        )
        == []
    )
    long = "сидит " * 80
    assert local_merge_trivial_shots([_shot(действие=long), _shot(id="b", действие=long)]) == []


def test_merge_copies_tail_fields_and_rewrites_parent() -> None:
    shots = [
        _shot(id="a", порядок=1, parent_id="b"),
        _shot(id="b", действие="сидит дальше", конец="упал", закадр="текст", точность="старт/конец"),
        _shot(id="c", порядок=3),
    ]
    notes = local_merge_trivial_shots(shots)
    assert notes == ["b склеен в a"]
    assert shots[0]["конец"] == "упал"
    assert shots[0]["закадр"] == "текст"
    assert shots[0]["точность"] == "старт/конец"
    assert shots[0]["parent_id"] == "a"
    assert shots[0]["порядок"] == 1


def test_wide_rewrites_prompt_and_ids() -> None:
    shots: list[object] = [
        {
            "id": "s-K1",
            "план": "КРУПНЫЙ",
            "реплика": "привет",
            "говорит": "Анна",
            "кто": "c01",
            "промт": "План КРУПНЫЙ. Ракурс сбоку. Высота снизу вверх.",
            "промт_alt": "",
            "промт_x": 5,
            "промт_видео": "не трогать",
            "точка": "сбоку",
            "порядок": 1,
            "parent_id": "s-K2",
        },
        {
            "id": "s-K2",
            "план": "КРУПНЫЙ",
            "реплика": "здравствуй",
            "говорит": "Борис",
            "кто": "c02",
            "порядок": 2,
        },
        "мусор",
    ]
    notes = local_ensure_wide_before_dialogue(shots)
    assert notes
    inserted = shots[0]
    assert isinstance(inserted, dict)
    assert inserted["план"] == "ОБЩИЙ"
    assert "План ОБЩИЙ" in str(inserted["промт"])
    assert "Ракурс фронт" in str(inserted["промт"])
    assert "Высота уровень глаз" in str(inserted["промт"])
    assert inserted["промт_видео"] == "не трогать"
    assert inserted["parent_id"] == "s-K3"
    assert local_ensure_wide_before_dialogue([]) == []


def _install_grammar(monkeypatch: pytest.MonkeyPatch, module: types.ModuleType) -> None:
    """И sys.modules, и атрибут пакета: ``import a.b.c`` читает атрибут, не кэш."""

    name = "app.services.scene_shot_grammar"
    monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, name, module)
    import app.services as services

    monkeypatch.setattr(services, "scene_shot_grammar", module, raising=False)


def test_upstream_grammar_is_preferred_when_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("app.services.scene_shot_grammar")

    def merge_trivial_shots(shots: list[object]) -> list[str]:
        return ["from-upstream"]

    def ensure_wide_before_dialogue(shots: list[object]) -> None:
        return None

    module.merge_trivial_shots = merge_trivial_shots
    module.ensure_wide_before_dialogue = ensure_wide_before_dialogue
    _install_grammar(monkeypatch, module)
    assert upstream_grammar() is module
    assert merge_notes([{"id": "a"}]) == ["from-upstream"]
    assert wide_notes([{"id": "a"}]) == []
    assert merge_notes("не список") == []
    invalid = shots_of({"shots": "нет"})
    assert invalid is not None and invalid != [] and invalid.invalid
    assert shots_of({"кадры": [{"id": "k"}]}) == [{"id": "k"}]

    bare = types.ModuleType("app.services.scene_shot_grammar")
    _install_grammar(monkeypatch, bare)
    assert upstream_grammar() is None


def test_snapshot_partial_tables_and_errors(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE scenes (id INTEGER, project_id INTEGER)")
    conn.execute("INSERT INTO scenes VALUES (4, 2)")
    snap = load_project_snapshot(2, conn=conn)
    assert snap["available"] is True
    assert snap["scenes"] == [{"id": 4}]
    assert snap["frames"] == []
    conn.close()
    assert load_project_snapshot(2, conn=conn)["available"] is False

    empty = sqlite3.connect(":memory:")
    assert _select_ids(empty, "scenes", 1) == []
    with pytest.raises(sqlite3.Error):
        _select_ids(empty, "other", 1)
    empty.close()

    db = tmp_path / "project.db"
    file_conn = sqlite3.connect(db)
    file_conn.execute("CREATE TABLE frames (id INTEGER, project_id INTEGER)")
    file_conn.execute("INSERT INTO frames VALUES (8, 1)")
    file_conn.commit()
    file_conn.close()

    def _path(project_id: int):
        assert project_id == 1
        return db

    monkeypatch.setattr("app.project_db.resolve_project_db_path_by_id", _path)
    via_project = load_project_snapshot(1)
    assert via_project["frames"] == [{"id": 8}]
    assert via_project["scenes"] == []

    monkeypatch.setattr("app.project_db.resolve_project_db_path_by_id", lambda project_id: tmp_path / "missing.db")
    assert load_project_snapshot(1)["available"] is False


def test_no_deletions_odd_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.project_db.resolve_project_db_path_by_id", lambda project_id: None)
    missing = run("no_deletions", 1, {"load_snapshot": True})
    assert missing.passed is True
    assert missing.details["reason"] == "snapshot_unavailable"
    listed = run(
        "no_deletions",
        1,
        {"before": ["f1", {"no": "id"}, {"id": None}], "after": ["f1", "f2"]},
    )
    assert listed.blocking is True
    assert listed.details["frames"]["count_decreased"] is True
    assert listed.details["frames"]["missing_ids"] == []
    junk = run("no_deletions", 1, {"before": "нет", "after": {"frames": "нет", "сцены": []}})
    assert junk.passed is True
    same = run(
        "no_deletions",
        1,
        {"before": ["f1", {"id": None}], "after": ["f1", "x"]},
    )
    assert same.passed is True


def test_plan_schema_types_and_context_schema() -> None:
    result = run(
        "plan_sanity",
        1,
        {
            "plan": [
                {
                    "action": "create_node",
                    "params": {
                        "node_key": "BAD",
                        "group": "g",
                        "title": "t",
                        "prompt_template": "p",
                        "params": [],
                    },
                },
                {"action": "rag_search", "params": {"query": "q", "corpora": "нет", "top_k": True}},
                {
                    "action": "edit_node_prompt",
                    "params": {
                        "node_key": "n_a",
                        "patch_mode": "nope",
                        "content": "c",
                        "reason": "r",
                    },
                },
                {
                    "action": "edit_node_params",
                    "params": {
                        "node_key": "n_a",
                        "params_patch": {},
                        "reason": "r",
                        "op": "delete",
                        "target": "prompt",
                    },
                },
                {"action": "custom", "params": {"a": 1}},
            ],
            "whitelist": [
                "create_node",
                "rag_search",
                "edit_node_prompt",
                "edit_node_params",
                "custom",
            ],
            "schemas": {"custom": {"fields": {"a": {"type": "widget", "required": True}}}},
        },
    )
    reasons = {item["reason"] for item in result.details["errors"]}
    assert "bad_type" in reasons
    assert result.blocking is True


def test_registry_names_failure_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("app.orchestrator.actions")

    class Registry:
        def names(self) -> list[str]:
            raise RuntimeError("нет списка")

    module.registry = Registry()
    monkeypatch.setitem(sys.modules, "app.orchestrator.actions", module)
    result = run("plan_sanity", 1, {"plan": [{"action": "list_nodes", "params": {}}], "registry": Registry()})
    assert result.passed is True

    def broken() -> dict[str, str]:
        raise RuntimeError("реестр")

    module.registry = broken
    monkeypatch.setitem(sys.modules, "app.orchestrator.actions", module)
    again = run("plan_sanity", 1, {"plan": [{"action": "list_nodes"}]})
    assert again.passed is True


def test_plan_registry_shapes(monkeypatch: pytest.MonkeyPatch) -> None:
    class Step:
        action = "list_nodes"
        params = "не словарь"

    assert run("plan_sanity", 1, {"plan": [Step()], "whitelist": ["list_nodes"]}).passed is True
    hidden = run(
        "plan_sanity",
        1,
        {
            "plan": [
                {
                    "action": "edit_node_params",
                    "params": {
                        "node_key": "n_a",
                        "params_patch": {},
                        "reason": "r",
                        "delete_frame": True,
                    },
                }
            ]
        },
    )
    assert hidden.details["errors"][0]["reason"] == "deletion_forbidden"
    typed = run(
        "plan_sanity",
        1,
        {"plan": [{"action": "rag_search", "params": {"query": 5}}], "whitelist": ["rag_search", "free_action"]},
    )
    assert typed.details["errors"][0]["reason"] == "bad_type"
    free = run("plan_sanity", 1, {"plan": [{"action": "free_action"}], "whitelist": ["free_action"]})
    assert free.passed is False
    assert free.details["errors"][0]["reason"] == "not_in_whitelist"

    class Named:
        def names(self) -> list[str]:
            return ["get_node"]

        def validate_params(self, action: str, params: dict) -> list[str] | None:
            if action == "get_node" and not params:
                raise KeyError(action)
            return None

    via_context = run(
        "plan_sanity",
        1,
        {"registry": Named(), "plan": [{"action": "get_node", "params": {"node_key": "n_a"}}]},
    )
    assert via_context.passed is True
    fallback = run("plan_sanity", 1, {"registry": Named(), "plan": [{"action": "get_node", "params": {}}]})
    assert fallback.details["errors"][0]["reason"] == "missing_param"

    module = types.ModuleType("app.orchestrator.actions")
    monkeypatch.setitem(sys.modules, "app.orchestrator.actions", module)
    assert run("plan_sanity", 1, {"plan": [{"action": "list_nodes"}]}).passed is True
    module.registry = {"list_nodes": {}}
    assert run("plan_sanity", 1, {"plan": [{"action": "list_nodes"}]}).passed is True
    module.registry = object()
    assert run("plan_sanity", 1, {"plan": [{"action": "list_nodes"}]}).passed is True


def test_response_without_approval_is_not_exhaustion() -> None:
    class Bare:
        task_status = "waiting_approval"
        approval = None

    assert run("spend_guard", 1, {"response": None, "run_id": "edge"}).details["reason"] == "spend_source_missing"
    assert run("spend_guard", 1, {"response": Bare(), "run_id": "edge"}).details["reason"] == "spend_source_missing"


def test_contracts_predicate_error_uses_local_check(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("app.orchestrator.contracts")

    def budget_exhausted_response(response: object) -> bool:
        raise RuntimeError("контракт")

    module.budget_exhausted_response = budget_exhausted_response
    monkeypatch.setitem(sys.modules, "app.orchestrator.contracts", module)

    class Approval:
        reason = "budget_exhausted"
        payload = None

    class Response:
        task_status = "waiting_approval"
        approval = Approval()

    result = run("spend_guard", 1, {"response": Response(), "run_id": "edge"})
    assert result.passed is False
    assert result.blocking is True
    assert result.details["response"]["payload"] == {}


def test_dry_run_helper_and_vo_length_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    assert is_dry_run(None) is False
    assert is_dry_run({}) is False

    real = importlib.import_module

    def broken(name: str, package: str | None = None):
        if name == "app.orchestrator.validators.vo_length":
            raise RuntimeError("сломан")
        return real(name, package)

    monkeypatch.setattr("app.orchestrator.validators.importlib.import_module", broken)
    assert run("vo_length", 1, {}).details["reason"] == "unavailable"
    monkeypatch.undo()

    empty = types.ModuleType("app.orchestrator.validators.vo_length")
    monkeypatch.setitem(sys.modules, "app.orchestrator.validators.vo_length", empty)
    assert run("vo_length", 1, {}).details["reason"] == "unavailable"

    adapter = types.ModuleType("app.orchestrator.validators.vo_length")

    def check_vo_lengths(fragments: list[str]) -> list[str]:
        return list(fragments)

    adapter.check_vo_lengths = check_vo_lengths
    monkeypatch.setitem(sys.modules, "app.orchestrator.validators.vo_length", adapter)
    adapted = run("vo_length", 1, {"vo": ["длинный фрагмент"]})
    assert adapted.passed is False
    assert adapted.details["violations"] == ["длинный фрагмент"]


def test_all_dumps_result_without_model_dump(monkeypatch: pytest.MonkeyPatch) -> None:
    class Bare:
        name = "no_deletions"
        passed = True
        details = "ok"
        blocking = False

    monkeypatch.setitem(sys.modules["app.orchestrator.validators"]._CHECKS, "no_deletions", lambda project_id, context: Bare())
    result = run("all", 1, {})
    dumped = next(item for item in result.details["results"] if item["name"] == "no_deletions")
    assert dumped["pass"] is True
    assert dumped["details"] == "ok"
