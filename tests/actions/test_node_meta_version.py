"""Версия meta: свой ключ, снимок прежнего значения, 409 на конфликт."""

from __future__ import annotations

import pytest

from app.orchestrator.node_store.meta_book import MetaBook, VersionConflict


def test_set_prompt_variant_concurrent_slots_both_kept() -> None:
    book = MetaBook()
    seen = book.version
    book.set_prompt_variant(
        node_key="hero", slot_id="main", variant="a.md", version=seen
    )
    with pytest.raises(VersionConflict) as exc:
        book.set_prompt_variant(
            node_key="hero", slot_id="alt", variant="b.md", version=seen
        )
    assert exc.value.status_code == 409
    book.set_prompt_variant(
        node_key="hero", slot_id="alt", variant="b.md", version=book.version
    )
    assert book.meta["prompt_slot_variants"]["hero"] == {
        "main": "a.md",
        "alt": "b.md",
    }


def test_set_assemble_params_does_not_clobber_other_step_params() -> None:
    book = MetaBook(
        meta={
            "node_step_params": {
                "script": {"tone": "dry"},
                "assemble": {"bgm_level": 0.1, "subtitles_enabled": True},
            }
        }
    )
    book.set_assemble_params(values={"bgm_level": 0.4}, version=book.version)
    params = book.meta["node_step_params"]
    assert params["script"] == {"tone": "dry"}
    assert params["assemble"]["bgm_level"] == 0.4
    assert params["assemble"]["subtitles_enabled"] is True


def test_node_param_actions_write_snapshot() -> None:
    book = MetaBook()
    book.set_config(
        node_key="hero", fields={"role": "writer"}, version=book.version
    )
    book.set_prompt_variant(
        node_key="hero", slot_id="main", variant="a.md", version=book.version
    )
    book.set_assemble_params(values={"bgm_level": 0.2}, version=book.version)
    seen = book.version
    book.set_disabled(node_key="hero", disabled=True, version=seen)
    with pytest.raises(VersionConflict) as exc:
        book.set_disabled(node_key="plan", disabled=True, version=seen)
    assert exc.value.status_code == 409
    book.set_disabled(node_key="plan", disabled=True, version=book.version)

    actions = [row["action"] for row in book.list_snapshots()]
    assert actions == [
        "node.set_config",
        "node.set_prompt_variant",
        "montage.set_assemble_params",
        "node.set_disabled",
        "node.set_disabled",
    ]
    for row in book.list_snapshots():
        assert "before" in row and "after" in row
    assert book.meta["disabled_nodes"] == ["hero", "plan"]


def test_patch_meta_conflict_rejected() -> None:
    book = MetaBook()
    book.patch_meta("alpha", 1, version=0)
    with pytest.raises(VersionConflict) as exc:
        book.patch_meta("beta", 2, version=0)
    assert exc.value.status_code == 409
    assert book.meta["alpha"] == 1
    assert "beta" not in book.meta
    book.patch_meta("beta", 2, version=book.version)
    assert book.meta["alpha"] == 1
    assert book.meta["beta"] == 2


def test_node_set_config_keeps_previous_value() -> None:
    book = MetaBook()
    book.set_config(
        node_key="hero",
        fields={"role": "writer", "outputMode": "db"},
        version=book.version,
    )
    book.set_config(
        node_key="hero", fields={"role": "editor"}, version=book.version
    )
    assert book.meta["node_config"]["hero"]["outputMode"] == "db"
    snaps = [
        row for row in book.list_snapshots() if row["action"] == "node.set_config"
    ]
    assert snaps[-1]["before"]["role"] == "writer"
    assert snaps[-1]["after"]["role"] == "editor"
    book.rollback(snaps[-1]["id"])
    assert book.meta["node_config"]["hero"] == {
        "role": "writer",
        "outputMode": "db",
    }
