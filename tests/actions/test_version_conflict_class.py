"""Один класс VersionConflict на файл, meta и откат промта."""

from __future__ import annotations

from app.orchestrator.node_store.atomic_write import VersionConflict as FileConflict
from app.orchestrator.node_store.errors import VersionConflict as SharedConflict
from app.orchestrator.node_store.meta_book import VersionConflict as MetaConflict
from app.orchestrator.node_store.prompt_history import VersionConflict as PromptConflict


def test_version_conflict_is_one_class() -> None:
    assert FileConflict is MetaConflict is PromptConflict is SharedConflict
    assert SharedConflict.status_code == 409
    assert issubclass(SharedConflict, Exception)
