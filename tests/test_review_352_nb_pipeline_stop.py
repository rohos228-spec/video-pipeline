"""Неблокирующее: стоп на ноде — это стоп проекта, замер плитки не на каждом рендере."""

from pathlib import Path


def test_node_stop_title_and_layout_effect_deps() -> None:
    src = Path("web/src/components/canvas/pipeline-node.tsx").read_text(encoding="utf-8")
    assert 'title="Остановить весь проект"' in src
    assert "}, [isOpenBox]);" in src
