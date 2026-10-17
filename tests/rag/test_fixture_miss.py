"""Промах фикстуры — FixtureMiss, без живого вызова."""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from app.orchestrator.rag.llm_fixtures import ReplayRouter
from app.orchestrator.rag.types import FixtureMiss


def test_missing_fixture_fails_and_does_not_open_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("сетевой вызов запрещён")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    router = ReplayRouter(tmp_path)
    with pytest.raises(FixtureMiss) as caught:
        router.call(
            "auto",
            [{"role": "user", "content": "нет такой фикстуры"}],
            False,
            reasoning_effort="high",
        )
    assert caught.value.fixture_key
    assert caught.value.label == "fixture-miss"
    assert "fixture-miss" in str(caught.value)
