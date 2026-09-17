"""Тесты защиты от Race Condition при параллельной записи чекпоинтов scene_design."""

import pytest
import asyncio
from pathlib import Path
from types import SimpleNamespace
from app.services.scene_design.runner import save_checkpoint, load_checkpoint, _META_LOCK
from app.services.scene_design import agents as ag


@pytest.mark.asyncio
async def test_concurrent_agent_checkpoint_writes_preserve_all_agents(tmp_path: Path):
    """Проверяет, что одновременная запись чекпоинтов всех агентов не затирает друг друга."""
    project = SimpleNamespace(
        id=101,
        slug="concurrent_test",
        data_dir=tmp_path,
        meta={},
    )
    
    agent_names = ["skeleton", "characters", "world", "camera", "action"]

    async def _write_agent(name: str):
        await asyncio.sleep(0.01)
        list_key = ag.LIST_KEY[name]
        data = {list_key: [{"id": f"{name}_01"}]}
        async with _META_LOCK:
            save_checkpoint(project, name, data)

    await asyncio.gather(*[_write_agent(name) for name in agent_names])

    # Проверяем, что в project.meta сохранились ВСЕ агенты
    agents_meta = project.meta.get("scene_design", {}).get("agents", {})
    assert len(agents_meta) == len(agent_names)
    for name in agent_names:
        assert name in agents_meta
        assert agents_meta[name]["status"] == "done"
        loaded = load_checkpoint(project, name)
        assert loaded is not None


def test_load_checkpoint_legacy_style_does_not_keyerror(tmp_path: Path):
    """Старый style.json: сборка не должна падать KeyError LIST_KEY['style']."""
    import json

    project = SimpleNamespace(
        id=102,
        slug="style_legacy",
        data_dir=tmp_path,
        meta={
            "scene_design": {
                "agents": {"style": {"status": "done"}},
            }
        },
    )
    sd = tmp_path / "scene_design"
    sd.mkdir()
    (sd / "style.json").write_text(
        json.dumps({"style_arc": [{"тон": "тихо"}], "report": "ok"}, ensure_ascii=False),
        encoding="utf-8",
    )
    loaded = load_checkpoint(project, "style")
    assert loaded is not None
    assert loaded["style_arc"][0]["тон"] == "тихо"