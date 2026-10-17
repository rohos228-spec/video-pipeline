"""Откат мастер-промта отказывается, если текст уже меняли после снимка."""

from __future__ import annotations

import pytest

from app.orchestrator.node_store.meta_book import VersionConflict
from app.orchestrator.node_store.prompt_history import PromptHistory


def test_save_master_checks_expected_version() -> None:
    history = PromptHistory()
    history.save_master(
        step_code="script", name="a.md", content="v1", expected_version=0
    )
    with pytest.raises(VersionConflict) as exc:
        history.save_master(
            step_code="script", name="a.md", content="v2", expected_version=0
        )
    assert exc.value.status_code == 409
    assert history.read("script", "a.md") == "v1"
    history.save_master(
        step_code="script", name="a.md", content="v2", expected_version=1
    )
    assert history.read("script", "a.md") == "v2"


def test_rollback_master_prompt_refuses_if_changed_since() -> None:
    history = PromptHistory()
    history.save_master(
        step_code="script", name="a.md", content="v1", expected_version=0
    )
    snap = history.save_master(
        step_code="script", name="a.md", content="v2", expected_version=1
    )
    history.save_master(
        step_code="script", name="a.md", content="v3", expected_version=2
    )
    with pytest.raises(VersionConflict) as exc:
        history.rollback_master_prompt(snap["id"])
    assert exc.value.status_code == 409
    assert history.read("script", "a.md") == "v3"
    latest = history.snapshots[-1]
    assert latest["after"] == "v3"
    history.rollback_master_prompt(latest["id"])
    assert history.read("script", "a.md") == "v2"
