"""PR #386: перенумерация K{i} не гасит живой шот и не оживляет архивный."""

from __future__ import annotations

from app.orchestrator.validators.activity_flags import shot_is_active
from app.services.vo_shot_expand import _merge_live_keep_dead


def _row(sid: str, *, active: bool = True) -> dict:
    row = {"id": sid, "закадр": f"текст {sid}"}
    if not active:
        row["активен"] = False
    return row


def test_live_shot_takes_archived_id_both_sides_keep_state():
    # живой шот после перенумерации получает id архивной строки
    old_rows = [_row("7-S2-K1"), _row("7-S2-K2", active=False)]
    live_rows = [{"id": "7-S2-K2", "закадр": "текст 7-S2-K1", "parent_id": None}]

    merged = _merge_live_keep_dead(old_rows, live_rows)

    live = [r for r in merged if isinstance(r, dict) and shot_is_active(r)]
    dead = [r for r in merged if isinstance(r, dict) and not shot_is_active(r)]
    assert [r["id"] for r in live] == ["7-S2-K2"]
    arch = [r for r in dead if r.get("id_прежний") == "7-S2-K2"]
    assert len(arch) == 1
    assert arch[0]["id"] == "7-S2-K2~arch"
    assert arch[0]["закадр"] == "текст 7-S2-K2"
    assert not {r["id"] for r in live} & {r["id"] for r in dead}


def test_sync_live_coverage_rewrite_idempotent():
    # sync_live_coverage_ids переписывает кадры только через
    # _merge_live_keep_dead: повторный прогон ничего не меняет
    old_rows = [_row("7-S2-K1"), _row("7-S2-K2", active=False)]
    live_rows = [{"id": "7-S2-K2", "закадр": "текст 7-S2-K1", "parent_id": None}]

    once = _merge_live_keep_dead(old_rows, live_rows)
    live_again = [dict(r) for r in once if shot_is_active(r)]
    twice = _merge_live_keep_dead([dict(r) for r in once], live_again)
    assert twice == once


def test_merge_trivial_shots_keeps_live_with_archived_id(monkeypatch):
    from app.services import scene_shot_grammar

    monkeypatch.setattr(
        scene_shot_grammar, "_can_merge_trivial", lambda a, b, nxt: True
    )
    dead = {"id": "7-S2-K2", "действие": "старая версия", "активен": False}
    shots = [
        {"id": "7-S2-K2", "действие": "идёт", "порядок": 1},
        {"id": "7-S2-K3", "действие": "садится", "порядок": 2},
        dead,
    ]

    notes = scene_shot_grammar.merge_trivial_shots(shots)

    assert notes
    live = [r for r in shots if isinstance(r, dict) and shot_is_active(r)]
    assert [r["id"] for r in live] == ["7-S2-K2"]
    assert any(r is dead for r in shots)
    assert not shot_is_active(dead)
