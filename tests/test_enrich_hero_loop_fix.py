"""Regression: enrich slot не перезапускается после отката hero; hero bootstrap из xlsx."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.models import Project, ProjectStatus
from app.orchestrator.graph.planner import WorkflowGraph
from app.orchestrator.steps import generate_hero


def test_work_types_done_remembers_completed_enrich_slots() -> None:
    g = WorkflowGraph.default()
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.frames_ready,
        meta={"graph_executor": True, "enrich_completed_slots": [3]},
    )
    done = g._work_types_done(p)
    assert "split" in done
    assert "enrich_3" in done
    assert "enrich_1" not in done


def test_frames_ready_skips_completed_enrich_3_on_custom_graph() -> None:
    """После отката к frames_ready enrich_3 не должен запускаться снова."""
    nodes = [
        {"id": "n_topic", "type": "topic", "position": {"x": 0, "y": 0}, "data": {}},
        {"id": "n_split", "type": "split", "position": {"x": 100, "y": 0}, "data": {}},
        {"id": "n_enrich_3", "type": "enrich_3", "position": {"x": 200, "y": 0}, "data": {}},
        {"id": "n_hero", "type": "hero", "position": {"x": 300, "y": 0}, "data": {}},
    ]
    edges = [
        {"id": "e1", "source": "n_topic", "target": "n_split", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e2", "source": "n_split", "target": "n_enrich_3", "sourceHandle": "out", "targetHandle": "in"},
        {"id": "e3", "source": "n_enrich_3", "target": "n_hero", "sourceHandle": "out", "targetHandle": "in"},
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.frames_ready,
        meta={"graph_executor": True, "enrich_completed_slots": [3]},
    )
    nxt = g.next_running_after_ready(p, ProjectStatus.frames_ready)
    assert nxt == ProjectStatus.generating_hero


def test_rollback_after_hero_failure_returns_last_enrich_ready() -> None:
    from app.main import _running_status_requires

    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.generating_hero,
        meta={"enrich_completed_slots": [3]},
    )
    requires = _running_status_requires(ProjectStatus.generating_hero, p)
    assert requires is ProjectStatus.enrich_3_ready


@pytest.mark.asyncio
async def test_bootstrap_excel_hero_from_xlsx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services.excel_characters import ExcelCharacter
    from app.settings import settings

    slug = "stalin-test"
    videos = tmp_path / "videos" / slug
    videos.mkdir(parents=True)
    (videos / "project.xlsx").write_bytes(b"fake")

    fake_char = ExcelCharacter(id="c01", name="Сталин", look="лицо")
    import app.services.excel_characters as ec

    monkeypatch.setattr(ec, "parse_persons_sheet", lambda _p: [fake_char])
    monkeypatch.setattr(settings, "data_dir", tmp_path)

    p = Project(topic="t", slug=slug, hero_mode="auto")
    p.status = ProjectStatus.generating_hero
    p.meta = {}

    session = AsyncMock()
    session.flush = AsyncMock()

    cfg = await generate_hero._bootstrap_excel_hero_from_xlsx(session, p)
    assert cfg is not None
    assert len(cfg["characters"]) == 1
    assert p.meta["excel_hero"]["characters"][0]["id"] == "c01"
