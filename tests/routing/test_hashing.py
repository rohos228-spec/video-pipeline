"""Канонические сообщения фикстуры."""

from __future__ import annotations

import pytest

from app.orchestrator.routing.hashing import normalize_messages


def test_normalize_messages_rejects_none_and_non_dict():
    with pytest.raises(ValueError, match="string or a dict"):
        normalize_messages([None])
    with pytest.raises(ValueError, match="string or a dict"):
        normalize_messages([1])


def test_normalize_messages_keeps_role_and_content():
    assert normalize_messages(
        [{"role": "user", "content": "да", "extra": "x"}]
    ) == [{"role": "user", "content": "да"}]
