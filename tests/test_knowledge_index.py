"""Тесты индекса знаний / поиска."""

from __future__ import annotations

from pathlib import Path

import app.services.knowledge_index as ki


def test_build_and_search_agent_map(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(ki, "index_path", lambda: tmp_path / "knowledge_index.json")
    payload = ki.build_index(write=True)
    assert payload["count"] >= 10
    assert (tmp_path / "knowledge_index.json").is_file()

    hits = ki.search_knowledge("AGENT_MAP gpt_workspace", limit=20)
    paths = [h["path"] for h in hits]
    assert any("AGENT_MAP" in p or "agent_map" in p.lower() for p in paths) or any(
        "gpt_workspace" in p for p in paths
    )

    hits2 = ki.search_knowledge("anim_pr R48", limit=15)
    assert hits2
    assert hits2[0]["score"] > 0


def test_knowledge_router_search() -> None:
    from fastapi.testclient import TestClient

    from app.web.api import create_app

    client = TestClient(create_app())
    r = client.get("/api/knowledge/search", params={"q": "PROMPTS_BLOCKS", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert any("PROMPTS_BLOCKS" in (h.get("path") or "") for h in body["hits"])

    h = client.get("/api/knowledge/help")
    assert h.status_code == 200
    assert "справка" in h.text.lower() or "AGENT_MAP" in h.text
