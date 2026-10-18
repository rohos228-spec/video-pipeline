"""Кнопки Базы передают confirm=true и умеют вернуть помеченную строку."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_baza_delete_passes_confirm_and_offers_restore() -> None:
    api = (ROOT / "web/src/lib/api.ts").read_text(encoding="utf-8")
    baza = (ROOT / "web/src/components/baza/baza-workspace.tsx").read_text(
        encoding="utf-8"
    )
    for path in (
        "/api/db/texts/${textId}?confirm=true",
        "/api/db/entities/${entityId}?confirm=true",
        "/api/db/edges/${edgeId}?confirm=true",
        "/api/db/texts/${textId}/restore",
        "/api/db/entities/${entityId}/restore",
        "/api/db/edges/${edgeId}/restore",
    ):
        assert path in api
    assert "dbRestoreText" in baza
    assert "dbRestoreEdge" in baza
    assert "dbRestoreEntity" in baza
    assert 'title="Вернуть текст"' in baza
    assert 'title="Вернуть связь"' in baza
    assert 'title="Вернуть сущность"' in baza
    for stale in (
        '`/api/db/texts/${textId}`, { method: "DELETE" }',
        '`/api/db/entities/${entityId}`, { method: "DELETE" }',
        '`/api/db/edges/${edgeId}`, { method: "DELETE" }',
    ):
        assert stale not in api
