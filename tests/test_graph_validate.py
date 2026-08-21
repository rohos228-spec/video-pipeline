"""Тесты validate_workflow_graph."""

from __future__ import annotations

from app.orchestrator.graph.validate import validate_workflow_graph


def test_valid_linear_graph() -> None:
    nodes = [
        {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_script", "type": "script", "position": {"x": 1, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_plan", "target": "n_script", "sourceHandle": "out", "targetHandle": "in"},
    ]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is True
    assert r["errors"] == []


def test_detects_cycle() -> None:
    nodes = [
        {"id": "a", "type": "plan", "position": {}, "data": {}},
        {"id": "b", "type": "script", "position": {}, "data": {}},
        {"id": "c", "type": "split", "position": {}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "a", "target": "b"},
        {"id": "e2", "source": "b", "target": "c"},
        {"id": "e3", "source": "c", "target": "a"},
    ]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is False
    assert any("цикл" in e for e in r["errors"])


def test_ok_fail_retry_loop_is_allowed() -> None:
    """Проверка → не ок → правка → снова проверка — сохраняется."""
    nodes = [
        {"id": "check", "type": "excel_gpt", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "fix", "type": "excel_gpt", "position": {"x": 0, "y": 100}, "data": {}},
        {"id": "img", "type": "images", "position": {"x": 200, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e_ok", "source": "check", "target": "img", "data": {"kind": "pass"}},
        {"id": "e_fail", "source": "check", "target": "fix", "data": {"kind": "fail"}},
        {"id": "e_back", "source": "fix", "target": "check", "data": {"kind": "after"}},
    ]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is True
    assert r["errors"] == []
    assert any("Не ок" in w or "петля" in w for w in r["warnings"])


def test_hero_excel_gpt_loop_auto_marks_fail() -> None:
    """hero → excel_gpt → hero: обратная «Связь» сама становится «Не ок»."""
    nodes = [
        {"id": "n_hero", "type": "hero", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_excel_gpt", "type": "excel_gpt", "position": {"x": 200, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e_fwd", "source": "n_hero", "target": "n_excel_gpt", "data": {"kind": "after"}},
        {"id": "e_back", "source": "n_excel_gpt", "target": "n_hero", "data": {"kind": "after"}},
    ]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is True, r["errors"]
    assert r["errors"] == []
    assert any(p["id"] == "e_back" and p["kind"] == "fail" for p in r["edge_patches"])
    back = next(e for e in r["edges"] if e["id"] == "e_back")
    assert (back.get("data") or {}).get("kind") == "fail"
    fwd = next(e for e in r["edges"] if e["id"] == "e_fwd")
    assert (fwd.get("data") or {}).get("kind") == "after"


def test_split_feed_into_scriptwriter_does_not_flip_writer_to_check() -> None:
    """Пунктир Разбивка→сценарист не должен делать сценарист→проверка «не ок».

    Иначе проверка теряет вход, а ▶ сценариста падает: «у ноды check нет файлов».
    """
    nodes = [
        {"id": "n_plan", "type": "plan", "position": {}, "data": {}},
        {"id": "n_script", "type": "script", "position": {}, "data": {}},
        {"id": "n_split", "type": "split", "position": {}, "data": {}},
        {"id": "n_excel_gpt_fw_script", "type": "excel_gpt", "position": {}, "data": {}},
        {"id": "n_excel_gpt_fw_check_script", "type": "excel_gpt", "position": {}, "data": {}},
        {"id": "n_excel_gpt_fw_frames", "type": "excel_gpt", "position": {}, "data": {}},
        {"id": "n_excel_gpt_fw_qc", "type": "excel_gpt", "position": {}, "data": {}},
    ]
    writer_to_check = "e_n_excel_gpt_fw_script_n_excel_gpt_fw_check_script"
    edges = [
        {"id": "e_plan_writer", "source": "n_plan", "target": "n_excel_gpt_fw_script", "data": {"kind": "after"}},
        {"id": writer_to_check, "source": "n_excel_gpt_fw_script", "target": "n_excel_gpt_fw_check_script", "data": {"kind": "after"}},
        {"id": "e_check_frames", "source": "n_excel_gpt_fw_check_script", "target": "n_excel_gpt_fw_frames", "data": {"kind": "pass"}},
        {"id": "e_check_writer", "source": "n_excel_gpt_fw_check_script", "target": "n_excel_gpt_fw_script", "data": {"kind": "fail"}},
        {"id": "e_frames_qc", "source": "n_excel_gpt_fw_frames", "target": "n_excel_gpt_fw_qc", "data": {"kind": "after"}},
        {"id": "e_qc_script", "source": "n_excel_gpt_fw_qc", "target": "n_script", "data": {"kind": "after"}},
        {"id": "e_script_split", "source": "n_script", "target": "n_split", "data": {"kind": "after"}},
        {"id": "e_split_writer", "source": "n_split", "target": "n_excel_gpt_fw_script", "data": {"kind": "after"}},
    ]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is True, r["errors"]
    assert not any(p["id"] == writer_to_check for p in r.get("edge_patches") or [])
    kept = {e["id"]: e for e in (r.get("edges") or edges)}
    kind = ((kept[writer_to_check].get("data") or {}).get("kind") or "after")
    assert kind == "after"


def test_does_not_overwrite_pass_edge_on_cycle() -> None:
    """Уже размеченная «Ок» не превращается в «Не ок» при петле."""
    nodes = [
        {"id": "gpt_a", "type": "excel_gpt", "position": {}, "data": {}},
        {"id": "gpt_b", "type": "excel_gpt", "position": {}, "data": {}},
        {"id": "img", "type": "images", "position": {}, "data": {}},
    ]
    edges = [
        {"id": "e_ok", "source": "gpt_a", "target": "gpt_b", "data": {"kind": "pass"}},
        {"id": "e_fail", "source": "gpt_a", "target": "img", "data": {"kind": "fail"}},
        {"id": "e_back", "source": "gpt_b", "target": "gpt_a", "data": {"kind": "after"}},
    ]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is True, r["errors"]
    # e_ok остаётся pass
    assert not any(p["id"] == "e_ok" for p in r.get("edge_patches") or [])
    # обратная after с gpt_b → fail
    assert any(p["id"] == "e_back" and p["kind"] == "fail" for p in r["edge_patches"])
    if r.get("edges"):
        ok = next(e for e in r["edges"] if e["id"] == "e_ok")
        assert (ok.get("data") or {}).get("kind") == "pass"


def test_broken_edge_reference() -> None:
    nodes = [{"id": "n_plan", "type": "plan", "position": {}, "data": {}}]
    edges = [{"id": "e1", "source": "n_plan", "target": "n_missing"}]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is False


def test_topic_alone_is_not_isolated_warning() -> None:
    """topic — конфиг без стрелок; не тост «изолированные ноды: n_topic»."""
    nodes = [
        {"id": "n_topic", "type": "topic", "position": {}, "data": {}},
        {"id": "n_plan", "type": "plan", "position": {}, "data": {}},
        {"id": "n_script", "type": "script", "position": {}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_plan", "target": "n_script", "data": {"kind": "after"}},
    ]
    r = validate_workflow_graph(nodes, edges)
    assert r["valid"] is True
    assert not any("изолирован" in w for w in r["warnings"])


def test_isolated_work_node_still_warned() -> None:
    nodes = [
        {"id": "n_plan", "type": "plan", "position": {}, "data": {}},
        {"id": "n_orphan", "type": "script", "position": {}, "data": {}},
    ]
    edges: list[dict] = []
    r = validate_workflow_graph(nodes, edges)
    assert any("изолирован" in w and "n_orphan" in w for w in r["warnings"])
