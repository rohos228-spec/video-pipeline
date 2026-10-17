"""В индекс пишутся только канонические имена. Алиасы остаются на входе поиска."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.orchestrator.rag.corpora import CANONICAL_CORPORA, CORPUS_ALIASES
from app.orchestrator.rag.store import RagStore
from app.orchestrator.rag.types import SourceDoc

_STORED_ALIAS = re.compile(r"""corpus\s*=\s*['\"](?:projects|chats)['\"]""")


def test_aliases_are_rejected_as_stored_index_names(tmp_path: Path) -> None:
    assert set(CORPUS_ALIASES.values()) <= set(CANONICAL_CORPORA)
    for alias in ("projects", "chats"):
        with pytest.raises(ValueError, match="алиас"):
            SourceDoc(corpus=alias, source_id="src", title="t", text="текст")
    store = RagStore(tmp_path / "orch_rag.db")
    with pytest.raises(ValueError, match="алиас"):
        store.sync("projects", {}, {}, indexed_at="t0")
    with pytest.raises(ValueError, match="алиас"):
        store.sync("chats", {}, {}, indexed_at="t0")
    assert SourceDoc(corpus="history", source_id="src", title="t", text="текст").corpus == "history"


def test_rag_package_does_not_store_plan_names() -> None:
    root = Path("app/orchestrator/rag")
    hits: list[str] = []
    for path in sorted(root.glob("*.py")):
        if path.name == "corpora.py":
            continue
        if _STORED_ALIAS.search(path.read_text(encoding="utf-8")):
            hits.append(path.name)
    assert hits == []
