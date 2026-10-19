"""Кэш retrieve не переживает reindex и не сохраняет снимок, снятый посреди sync."""

from __future__ import annotations

import threading
from pathlib import Path

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.types import SourceDoc


def _engine(tmp_path: Path, docs: list[SourceDoc]) -> RagEngine:
    return RagEngine(
        db_path=tmp_path / "orch_rag.db",
        rules_path=tmp_path / "no-rules.md",
        static_docs={"rules": docs, "nodes": [], "prompts": [], "history": []},
        scan_git=False,
    )


def test_edited_source_is_visible_after_reindex(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        [SourceDoc(corpus="rules", source_id="rules.md", title="A", text="версия один уникально", part_key="rule-1")],
    )
    try:
        engine.reindex(["rules"])
        engine.retrieve("уникально", ["rules"], top_k=3, rerank=False)
        engine.static_docs = {
            "rules": [
                SourceDoc(
                    corpus="rules",
                    source_id="rules.md",
                    title="B",
                    text="версия два уникально",
                    part_key="rule-1",
                )
            ],
            "nodes": [],
            "prompts": [],
            "history": [],
        }
        engine.reindex(["rules"])
        hits = engine.retrieve("уникально", ["rules"], top_k=3, rerank=False)
    finally:
        engine.close()
    assert any("два" in chunk.text for chunk in hits)
    assert all("один" not in chunk.text for chunk in hits)


def test_retrieve_during_reindex_does_not_keep_the_old_corpus(tmp_path: Path) -> None:
    engine = _engine(
        tmp_path,
        [SourceDoc(corpus="rules", source_id="rules.md", title="A", text="старый якорь альфа", part_key="rule-1")],
    )
    started = threading.Event()
    release = threading.Event()
    original = engine.store.sync

    def slow_sync(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    errors: list[BaseException] = []

    def do_reindex() -> None:
        try:
            engine.reindex(["rules"], timeout=5)
        except BaseException as exc:  # noqa: BLE001 - отдать ошибку потока тесту
            errors.append(exc)

    try:
        engine.reindex(["rules"])
        engine.retrieve("альфа", ["rules"], top_k=3, rerank=False)
        engine.store.sync = slow_sync  # type: ignore[method-assign]
        engine.static_docs = {
            "rules": [
                SourceDoc(corpus="rules", source_id="rules.md", title="B", text="новый якорь бета", part_key="rule-1")
            ],
            "nodes": [],
            "prompts": [],
            "history": [],
        }
        thread = threading.Thread(target=do_reindex)
        thread.start()
        assert started.wait(5)
        engine.retrieve("бета", ["rules"], top_k=3, rerank=False)
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        assert errors == []
        hits = engine.retrieve("бета", ["rules"], top_k=3, rerank=False)
    finally:
        release.set()
        engine.close()
    assert any("бета" in chunk.text for chunk in hits)
    assert all("альфа" not in chunk.text for chunk in hits)
