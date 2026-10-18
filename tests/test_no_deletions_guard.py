"""Контракт точки входа #379 и случаи, которые шим не должен отвергать.

Тесты идут через ``app.pipeline.no_deletions_guard``. Если подхватится
модуль #379 @ 40da86ed до правки P1–P3, эти случаи станут красными.
"""

from __future__ import annotations

import copy
import inspect

import pytest

from app.pipeline.no_deletions_guard import (
    PreservationRejected,
    Violation,
    run_preservation_validators,
)
from app.services.frame_tombstones import mark_shots_inactive, merge_kadry_keep_missing


def _scene(rows, *, sid="s1", number=1, **extra):
    scene = {"id": sid, "номер": number, "кадры": rows}
    scene.update(extra)
    return {"сцены": [scene]}


def _violations(call) -> list:
    try:
        result = call()
    except PreservationRejected as exc:
        return list(exc.violations)
    if not isinstance(result, list):
        return []
    return [item for item in result if getattr(item, "code", "")]


def _has_scene_error(violations, *needles: str) -> bool:
    """Есть error по сцене или её шоту. Лишние warn не мешают."""

    for item in violations:
        if getattr(item, "severity", "") != "error":
            continue
        blob = f"{getattr(item, 'id', '')} {getattr(item, 'path', '')}"
        if any(needle and needle in blob for needle in needles):
            return True
    return False


def test_signature_matches_379_entry_point() -> None:
    sig = inspect.signature(run_preservation_validators)
    assert list(sig.parameters) == ["before", "after", "ops", "mode"]
    assert sig.parameters["ops"].default is None
    assert sig.parameters["mode"].kind is inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["mode"].default == "reject"
    item = Violation(code="kept_inactive", severity="warn", path="scenes/s1/кадры/k1", id="k1")
    assert item.as_dict() == {
        "code": "kept_inactive",
        "severity": "warn",
        "path": "scenes/s1/кадры/k1",
        "id": "k1",
    }
    assert item.__dataclass_fields__.keys() == {"code", "severity", "path", "id"}


def test_reject_does_not_mutate_and_soft_keeps_shot() -> None:
    before = _scene(
        [{"id": "k1", "закадр": "а"}, {"id": "k2", "закадр": "фраза на месте"}]
    )
    after = _scene([{"id": "k1", "закадр": "а"}])
    frozen = copy.deepcopy(after)
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, after)
    assert any(item.code == "frame_deleted" and item.id == "k2" for item in raised.value.violations)
    assert after == frozen

    softened = run_preservation_validators(before, after, mode="soft")
    assert any(
        item.code == "kept_inactive" and item.severity == "warn" and item.id == "k2"
        for item in softened
    )
    assert all(item.severity != "error" for item in softened)
    kept = {item["id"]: item for item in after["сцены"][0]["кадры"]}
    assert kept["k2"]["активен"] is False
    assert kept["k2"]["закадр"] == "фраза на месте"
    assert "закадр_архив" not in kept["k2"]


def test_inactive_shot_keeps_vo_in_place() -> None:
    """Пометка не уносит закадр. Хранитель такой снимок пропускает."""
    original = {"id": "k1", "закадр": "фраза на месте", "активен": True}
    marked = mark_shots_inactive([original], where="test_inactive_shot_keeps_vo_in_place")
    assert marked[0]["закадр"] == "фраза на месте"
    assert marked[0]["активен"] is False
    assert "закадр_архив" not in marked[0]
    assert original["закадр"] == "фраза на месте"
    assert "закадр_архив" not in original

    merged = merge_kadry_keep_missing(
        [
            {"id": "k1", "закадр": "фраза на месте"},
            {"id": "k2", "закадр": "вторая фраза"},
        ],
        [{"id": "k1", "закадр": "фраза на месте"}],
    )
    by_id = {row["id"]: row for row in merged}
    assert by_id["k1"]["закадр"] == "фраза на месте"
    assert by_id["k2"]["закадр"] == "вторая фраза"
    assert by_id["k2"]["активен"] is False
    assert "закадр_архив" not in by_id["k1"]
    assert "закадр_архив" not in by_id["k2"]

    before = _scene(
        [
            {"id": "k1", "закадр": "фраза на месте", "активен": False},
            {"id": "k2", "закадр": "вторая фраза"},
        ]
    )
    after = _scene([{"id": "k1", "закадр": "фраза на месте", "активен": False}])
    violations = run_preservation_validators(before, after, mode="soft")
    kept = {row["id"]: row for row in after["сцены"][0]["кадры"]}
    assert kept["k1"]["закадр"] == "фраза на месте"
    assert kept["k1"]["активен"] is False
    assert "закадр_архив" not in kept["k1"]
    assert kept["k2"]["закадр"] == "вторая фраза"
    assert kept["k2"]["активен"] is False
    assert all(item.code != "vo_on_inactive" for item in violations)
    parked = run_preservation_validators(before, before, mode="reject")
    assert all(item.severity != "error" for item in parked)
    assert all(item.code != "vo_on_inactive" for item in parked)


def test_remark_tail_park_and_restore_pass() -> None:
    inactive = {"id": "k1", "закадр": "фраза на месте", "активен": False}
    remarked = run_preservation_validators(
        _scene([dict(inactive)]),
        _scene([{**inactive, "пометка": "ещё раз"}]),
        mode="reject",
    )
    assert remarked == []

    tail = run_preservation_validators(
        _scene([dict(inactive), {"id": "k2", "закадр": "живой"}]),
        _scene([{"id": "k2", "закадр": "живой"}, dict(inactive)]),
        mode="soft",
    )
    assert tail == []

    parked = run_preservation_validators(
        _scene([{"id": "k1", "закадр": "текст"}], number=2),
        _scene([{"id": "k1", "закадр": "текст"}], number=900000),
        mode="reject",
    )
    assert parked == []

    before = _scene([{"id": "k1", "закадр": "фраза на месте", "активен": False}])
    after = _scene([{"id": "k1", "закадр": "фраза на месте", "активен": True}])
    restored = run_preservation_validators(
        before,
        after,
        [{"op": "restore", "shot_id": "k1", "frame_uuid": "s1"}],
        mode="reject",
    )
    assert restored == []
    assert after["сцены"][0]["кадры"][0]["активен"] is True


def test_scene_and_character_still_reject_in_soft_mode() -> None:
    with pytest.raises(PreservationRejected) as scene_exc:
        run_preservation_validators(
            _scene([{"id": "k1"}], sid="s1"),
            _scene([{"id": "k1"}], sid="s2"),
            mode="soft",
        )
    assert any(item.code == "scene_deleted" for item in scene_exc.value.violations)

    before = {"персонажи": [{"id": "c1", "name": "Анна"}], "сцены": []}
    after = {"персонажи": [], "сцены": []}
    with pytest.raises(PreservationRejected) as char_exc:
        run_preservation_validators(before, after, mode="soft")
    assert any(item.code == "character_deleted" and item.id == "c1" for item in char_exc.value.violations)


def test_coalesce_soft_keeps_frame_shot_and_inactive_vo() -> None:
    from app.services.apply_ops_batches import coalesce_cell_shot_ops

    uid = "cell-1"
    frames = [
        {
            "uuid": uid,
            "number": 6,
            "кадры": [
                {"id": "a", "закадр": "первый"},
                {"id": "b", "закадр": "фраза на месте"},
            ],
        }
    ]
    merged = coalesce_cell_shot_ops(
        [{"frame_uuid": uid, "fields": {"кадры": [{"id": "a", "закадр": "первый"}]}}],
        frames,
        patch=False,
    )
    rows = merged[0]["fields"]["кадры"]
    assert [row["id"] for row in rows] == ["a", "b"]
    assert rows[1]["активен"] is False
    assert rows[1]["закадр"] == "фраза на месте"

    soft = coalesce_cell_shot_ops(
        [
            {
                "frame_uuid": uid,
                "fields": {
                    "кадры": [
                        {"id": "a", "закадр": "первый"},
                        {"id": "b", "закадр": "фраза на месте", "активен": False},
                    ]
                },
            }
        ],
        frames,
        patch=False,
    )[0]["fields"]["кадры"]
    assert [row["id"] for row in soft] == ["a", "b"]
    assert soft[1]["активен"] is False
    assert soft[1]["закадр"] == "фраза на месте"
    assert "закадр_архив" not in soft[1]


def test_fill_catalog_keeps_shot_builder_dropped_and_inactive_vo(monkeypatch) -> None:
    from app.services import shot_templates as templates

    real = templates._assign_scene_vo

    def _drop(shots, vo, catalog_rows=None):
        rows = real(shots, vo, catalog_rows=catalog_rows)
        return rows[:1] if len(rows) > 1 else rows

    monkeypatch.setattr(templates, "_assign_scene_vo", _drop)
    out = templates.fill_kadry_from_catalog(
        [
            {"id": "a", "закадр": "первый"},
            {"id": "b", "закадр": "фраза на месте"},
        ],
        "",
    )
    by_id = {row["id"]: row for row in out}
    assert by_id["b"]["активен"] is False
    assert by_id["b"]["закадр"] == "фраза на месте"
    assert "закадр_архив" not in by_id["b"]

    monkeypatch.setattr(templates, "_assign_scene_vo", real)
    soft = templates.fill_kadry_from_catalog(
        [
            {"id": "a", "закадр": "первый"},
            {"id": "b", "закадр": "фраза на месте", "активен": False},
        ],
        "",
    )
    ids = [row["id"] for row in soft]
    assert ids.count("b") == 1
    kept = next(row for row in soft if row["id"] == "b")
    assert kept["активен"] is False
    assert kept["закадр"] == "фраза на месте"


def test_qc_candidate_keeps_dropped_shot_and_inactive_vo() -> None:
    from types import SimpleNamespace

    from app.services.montage_scene_improve import _keep_qc_kadry

    parent = SimpleNamespace(uuid="qc-1", number=3)
    shots = [
        {"id": "a", "закадр": "первый"},
        {"id": "b", "закадр": "фраза на месте"},
    ]
    candidate = [{"id": "a", "закадр": "первый"}]
    kept = _keep_qc_kadry(parent, shots, candidate)
    assert [row["id"] for row in kept] == ["a", "b"]
    assert kept[1]["активен"] is False
    assert kept[1]["закадр"] == "фраза на месте"
    soft = _keep_qc_kadry(
        parent,
        shots,
        [
            {"id": "a", "закадр": "первый"},
            {"id": "b", "закадр": "фраза на месте", "активен": False},
        ],
    )
    assert [row["id"] for row in soft] == ["a", "b"]
    assert soft[1]["активен"] is False
    assert soft[1]["закадр"] == "фраза на месте"
    assert "закадр_архив" not in soft[1]
    revived = _keep_qc_kadry(
        parent,
        [{"id": "a", "закадр": "первый", "активен": False}],
        [{"id": "a", "закадр": "первый", "активен": True}],
    )
    assert revived[0]["активен"] is False


def test_grammar_soft_keeps_dropped_shot_and_inactive_vo(monkeypatch) -> None:
    from app.services.scene_shot_grammar import align_shots_to_action_chain, apply_grammar_to_ops

    def _drop_tail(shots, action, **kwargs):
        del action, kwargs
        return [dict(shots[0])], None

    monkeypatch.setattr(
        "app.services.scene_shot_grammar.merge_gpt_shots_with_action",
        _drop_tail,
    )
    ops = [
        {
            "frame_uuid": "u",
            "fields": {
                "главное_действие": "1. кухня — режет хлеб",
                "кадры": [
                    {"id": "a", "закадр": "первый", "сцена": 1, "место": "кухня"},
                    {"id": "b", "закадр": "фраза на месте", "сцена": 1, "место": "кухня"},
                ],
            },
        }
    ]
    apply_grammar_to_ops(ops, [{"uuid": "u", "number": 1, "voiceover_text": "первый"}])
    rows = {shot["id"]: shot for shot in ops[0]["fields"]["кадры"]}
    assert rows["b"]["активен"] is False
    assert rows["b"]["закадр"] == "фраза на месте"
    assert "закадр_архив" not in rows["b"]

    soft = [
        {
            "frame_uuid": "u",
            "fields": {
                "главное_действие": "1. кухня — режет хлеб",
                "кадры": [
                    {"id": "a", "закадр": "первый", "сцена": 1, "место": "кухня"},
                    {
                        "id": "b",
                        "закадр": "фраза на месте",
                        "сцена": 1,
                        "место": "кухня",
                        "активен": False,
                    },
                ],
            },
        }
    ]
    align_shots_to_action_chain(
        soft,
        [{"uuid": "u", "number": 1, "главное_действие": "1. кухня — режет хлеб"}],
    )
    kept = soft[0]["fields"]["кадры"]
    assert [shot["id"] for shot in kept].count("b") == 1
    assert kept[1]["активен"] is False
    assert kept[1]["закадр"] == "фраза на месте"


def test_pack_soft_keeps_missing_shot_and_soft_delete_passes() -> None:
    from app.services.apply_ops_batches import _preserve_pack_ops

    uid = "aa" * 4
    chunk = [
        {
            "uuid": uid,
            "number": 2,
            "attrs": {
                "кадры": [
                    {"id": "k1", "закадр": "живой"},
                    {"id": "k2", "закадр": "фраза на месте"},
                ]
            },
        }
    ]
    ops = [{"frame_uuid": uid, "fields": {"кадры": [{"id": "k1", "закадр": "живой"}]}}]
    _preserve_pack_ops(ops, chunk)
    rows = ops[0]["fields"]["кадры"]
    assert [row["id"] for row in rows] == ["k1", "k2"]
    assert rows[1]["активен"] is False
    assert rows[1]["закадр"] == "фраза на месте"
    assert "закадр_архив" not in rows[1]

    soft_ops = [
        {
            "frame_uuid": uid,
            "fields": {
                "кадры": [
                    {"id": "k1", "закадр": "живой"},
                    {"id": "k2", "закадр": "фраза на месте", "активен": False},
                ]
            },
        }
    ]
    _preserve_pack_ops(soft_ops, chunk)
    soft_rows = soft_ops[0]["fields"]["кадры"]
    assert [row["id"] for row in soft_rows] == ["k1", "k2"]
    assert soft_rows[1]["активен"] is False
    assert soft_rows[1]["закадр"] == "фраза на месте"


def test_repair_vo_keeps_inactive_zakadr(warn_log=None) -> None:
    del warn_log
    from app.services.apply_ops_batches import repair_shot_vo_ops

    inactive = {"id": "1-K0", "закадр": "фраза на месте", "активен": False, "действие": "пауза"}
    ops = [
        {
            "frame_uuid": "u1",
            "fields": {
                "кадры": [
                    dict(inactive),
                    {"id": "1-K1", "закадр": "", "действие": "шаг"},
                ]
            },
        }
    ]
    frames = [{"uuid": "u1", "number": 1, "voiceover_text": "Короткий шаг."}]
    repair_shot_vo_ops(ops, frames)
    rows = {row["id"]: row for row in ops[0]["fields"]["кадры"]}
    assert rows["1-K0"]["закадр"] == "фраза на месте"
    assert rows["1-K0"]["активен"] is False
    assert "закадр_архив" not in rows["1-K0"]
    assert rows["1-K1"]["id"] == "1-K1"


def test_montage_mirror_soft_keeps_dropped_shot() -> None:
    from types import SimpleNamespace

    from app.services.montage_coverage_ops import _preserve_parent_kadry

    parent = SimpleNamespace(uuid="parent-1", number=4, id=4)
    before = [
        {"id": "k1", "закадр": "первый"},
        {"id": "k2", "закадр": "фраза на месте"},
    ]
    kadry = [{"id": "k1", "закадр": "первый"}]
    kept = _preserve_parent_kadry(parent, before, kadry)
    assert [row["id"] for row in kept] == ["k1", "k2"]
    assert kept[1]["активен"] is False
    assert kept[1]["закадр"] == "фраза на месте"

    already = [
        {"id": "k1", "закадр": "первый"},
        {"id": "k2", "закадр": "фраза на месте", "активен": False},
    ]
    soft = _preserve_parent_kadry(parent, before, [dict(row) for row in already])
    assert [row["id"] for row in soft] == ["k1", "k2"]
    assert soft[1]["активен"] is False
    assert soft[1]["закадр"] == "фраза на месте"


def test_frame_text_and_edge_loss_is_error() -> None:
    text = {"frame_id": "f1", "kind": "voiceover", "text": "фраза на месте"}
    edge = {"from_frame_id": "f1", "to_frame_id": "f2", "type": "next"}
    before = {"сцены": [], "frame_texts": [dict(text)], "frame_edges": [dict(edge)]}
    archived = {"сцены": [], "frame_texts": [{**text, "kind": "vo_archived"}], "frame_edges": [dict(edge)]}
    assert run_preservation_validators(before, archived, mode="soft") == []

    lost_text = {"сцены": [], "frame_texts": [], "frame_edges": [dict(edge)]}
    frozen = copy.deepcopy(lost_text)
    with pytest.raises(PreservationRejected) as text_exc:
        run_preservation_validators(before, lost_text, mode="soft")
    assert any(item.code == "frame_text_deleted" and item.severity == "error" for item in text_exc.value.violations)
    assert lost_text == frozen

    lost_edge = {"сцены": [], "frame_texts": [dict(text)], "frame_edges": []}
    with pytest.raises(PreservationRejected) as edge_exc:
        run_preservation_validators(before, lost_edge, mode="reject")
    assert any(item.code == "frame_edge_deleted" and item.severity == "error" for item in edge_exc.value.violations)


def test_idless_shot_loss_is_error_and_same_text_passes() -> None:
    before = _scene([{"закадр": "фраза на месте"}, {"закадр": "фраза на месте"}])
    same = _scene([{"действие": "фраза на месте"}])
    assert run_preservation_validators(before, same, mode="reject") == []

    gone = _scene([{"id": "k1", "закадр": "другой"}])
    frozen = copy.deepcopy(gone)
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, gone, mode="soft")
    assert any(
        item.code == "frame_deleted" and item.severity == "error" and item.id == "фраза на месте"
        for item in raised.value.violations
    )
    assert gone == frozen

    quiet = _scene([{"закадр": "фраза на месте", "активен": False}])
    assert run_preservation_validators(quiet, _scene([]), mode="soft") == []


def test_non_list_kadry_is_error() -> None:
    """Несписок — хотя бы один error по сцене. Лишние warn допустимы.

    Шим даёт error ``kadry_invalid`` и, для живого id, error ``shot_dropped``.
    Модуль #379 @ 1ef07735 рядом кладёт warn ``frames_no_data``.
    Точный набор и число не фиксируем: нужен error по сцене или шоту.
    """

    before = _scene([{"id": "k1", "закадр": "а"}])
    after = {"сцены": [{"id": "s1", "номер": 1, "кадры": "не список"}]}
    frozen = copy.deepcopy(after)
    violations = _violations(lambda: run_preservation_validators(before, after, mode="soft"))
    assert _has_scene_error(violations, "s1", "k1")
    assert after == frozen

    from app.pipeline.no_deletions_guard import keep_soft_kadry

    dict_violations = _violations(
        lambda: keep_soft_kadry([{"id": "k1"}], {"id": "k1"}, scene_id="s1", number=1)
    )
    assert _has_scene_error(dict_violations, "s1", "k1")


def test_keep_soft_kadry_restore_needs_op() -> None:
    """Неактивный → живой без адресованной операции — ``inactive_shot_revived``."""

    from app.pipeline.no_deletions_guard import keep_soft_kadry

    before = [{"id": "k1", "закадр": "фраза на месте", "активен": False}]
    after = [{"id": "k1", "закадр": "фраза на месте", "активен": True}]

    with pytest.raises(PreservationRejected) as bare_rows:
        keep_soft_kadry(before, [dict(after[0])], scene_id="s1", number=1)
    assert any(
        item.code == "inactive_shot_revived" and item.id == "k1" and item.severity == "error"
        for item in bare_rows.value.violations
    )

    with pytest.raises(PreservationRejected) as soft:
        run_preservation_validators(
            _scene(before),
            _scene([dict(after[0])]),
            mode="soft",
        )
    assert any(item.code == "inactive_shot_revived" and item.id == "k1" for item in soft.value.violations)

    for ops in ({"op": "restore"}, {"op": "restore", "shot_index": 0}):
        revived = [dict(after[0])]
        with pytest.raises(PreservationRejected) as missing_id:
            keep_soft_kadry(before, revived, scene_id="s1", number=1, ops=[ops])
        assert any(item.code == "inactive_shot_revived" for item in missing_id.value.violations)
        assert revived[0]["активен"] is True

    for ops in (
        {"op": "restore", "shot_id": "k1"},
        {"op": "activate", "shot_id": "k1"},
        {"op": "update", "id": "k1", "активен": True},
        {"op": "update", "shot_id": "k1", "fields": {"активен": True}},
    ):
        revived = [dict(after[0])]
        kept = keep_soft_kadry(before, revived, scene_id="s1", number=1, ops=[ops])
        assert kept[0]["активен"] is True
        assert kept[0]["id"] == "k1"

    shared = [{"id": "k1", "закадр": "фраза на месте", "активен": False}]
    left = {"сцены": [{"id": "s1", "номер": 1, "кадры": [dict(shared[0])]}]}
    right = {"сцены": [{"id": "s2", "номер": 2, "кадры": [dict(shared[0])]}]}
    revived_both = {
        "сцены": [
            {"id": "s1", "номер": 1, "кадры": [{"id": "k1", "закадр": "фраза на месте", "активен": True}]},
            {"id": "s2", "номер": 2, "кадры": [{"id": "k1", "закадр": "фраза на месте", "активен": True}]},
        ]
    }
    with pytest.raises(PreservationRejected):
        run_preservation_validators(
            {"сцены": left["сцены"] + right["сцены"]},
            revived_both,
            [{"op": "restore", "shot_id": "k1"}],
        )
    with pytest.raises(PreservationRejected) as paired:
        run_preservation_validators(
            {"сцены": [dict(left["сцены"][0]), dict(right["сцены"][0])]},
            {
                "сцены": [
                    {"id": "s1", "номер": 1, "кадры": [{"id": "k1", "активен": True, "закадр": "фраза на месте"}]},
                    {"id": "s2", "номер": 2, "кадры": [{"id": "k1", "активен": True, "закадр": "фраза на месте"}]},
                ]
            },
            [{"op": "restore", "shot_id": "k1", "frame_uuid": "s1"}],
        )
    assert any(item.code == "inactive_shot_revived" and item.id == "k1" for item in paired.value.violations)
    both = run_preservation_validators(
        {"сцены": [dict(left["сцены"][0]), dict(right["сцены"][0])]},
        {
            "сцены": [
                {"id": "s1", "номер": 1, "кадры": [{"id": "k1", "активен": True, "закадр": "фраза на месте"}]},
                {"id": "s2", "номер": 2, "кадры": [{"id": "k1", "активен": True, "закадр": "фраза на месте"}]},
            ]
        },
        [
            {"op": "restore", "shot_id": "k1", "frame_uuid": "s1"},
            {"op": "restore", "shot_id": "k1", "frame_uuid": "s2"},
        ],
    )
    assert both == []


def test_scene_voiceover_without_live_shots_is_vo_on_inactive() -> None:
    """Закадр сцены без живых шотов — error, soft его не поглощает."""

    before = _scene(
        [{"id": "k1", "закадр": "фраза на месте"}],
        закадр="фраза на месте",
    )
    after = _scene(
        [{"id": "k1", "закадр": "фраза на месте", "активен": False}],
        закадр="фраза на месте",
    )
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, after, mode="soft")
    assert any(
        item.code == "vo_on_inactive" and item.severity == "error" and item.id == "s1"
        for item in raised.value.violations
    )
    live = _scene(
        [{"id": "k1", "закадр": "фраза на месте"}],
        закадр="фраза на месте",
    )
    assert run_preservation_validators(live, copy.deepcopy(live), mode="reject") == []


def test_idless_name_and_missing_vo_do_not_hide_shots() -> None:
    """Поведение шима как у #379 @ 1ef07735: F1b, F1c, пустой закадр, ``кадры: None``."""

    before = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза. Вторая фраза.",
                "кадры": [
                    {"id": "k1", "закадр": "Первая фраза."},
                    {"id": "k2", "закадр": "Вторая фраза."},
                ],
            }
        ]
    }

    def with_extra(extra: dict) -> dict:
        scene = copy.deepcopy(before["сцены"][0])
        scene["кадры"] = [*scene["кадры"], extra]
        return {"сцены": [scene]}

    for extra in (
        {"id": "k9"},
        {"id": "k9", "закадр": ""},
        {"id": "k9", "имя": "Анна"},
        {"id": "", "parent_id": "9-K1"},
    ):
        added = run_preservation_validators(before, with_extra(extra), mode="reject")
        assert added == []
        assert all(item.code != "shot_dropped" for item in added)

    named = {
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "закадр": "Первая фраза.",
                "кадры": [{"id": "k1", "закадр": "Первая фраза.", "имя": "Анна"}],
            }
        ]
    }
    with pytest.raises(PreservationRejected) as cleared:
        run_preservation_validators(
            named,
            {"сцены": [{"id": "s1", "номер": 1, "закадр": "Первая фраза.", "кадры": []}]},
            mode="reject",
        )
    assert any(item.code == "shot_dropped" and item.id == "k1" for item in cleared.value.violations)

    nulled = copy.deepcopy(before)
    nulled["сцены"][0]["кадры"] = None
    frozen = copy.deepcopy(nulled)
    with pytest.raises(PreservationRejected) as null_exc:
        run_preservation_validators(before, nulled, mode="reject")
    assert any(item.code == "shot_dropped" and item.id == "k1" for item in null_exc.value.violations)
    assert nulled == frozen

    attrs_before = {
        "frames": [
            {
                "id": "u1",
                "attrs": {"кадры": [{"id": "k1", "закадр": "а"}, {"id": "k2", "закадр": "б"}]},
            }
        ]
    }
    with pytest.raises(PreservationRejected) as replaced:
        run_preservation_validators(
            attrs_before,
            {"frames": [{"id": "u1", "attrs": {"кадры": [{"id": "k1", "закадр": "а"}]}}]},
            mode="reject",
        )
    assert any(item.code == "shot_dropped" and item.id == "k2" for item in replaced.value.violations)
    with pytest.raises(PreservationRejected) as attrs_null:
        run_preservation_validators(
            attrs_before,
            {"frames": [{"id": "u1", "attrs": {"кадры": None}}]},
            mode="reject",
        )
    dropped = {item.id for item in attrs_null.value.violations if item.code == "shot_dropped"}
    assert {"k1", "k2"} <= dropped

    uid = "03ecb474-7628-4a21-b794-fef914ca3505"
    try:
        scene_row = run_preservation_validators(
            {"сцены": [{"id": uid, "номер": 1}]},
            {"сцены": [{"id": uid, "номер": 1, "кадры": [{"id": "k1", "закадр": "текст"}]}]},
            mode="reject",
        )
    except PreservationRejected as exc:
        scene_row = exc.violations
    assert uid not in {item.id for item in scene_row if item.code == "shot_dropped"}

    people = {
        "сцены": [{"id": "s1", "номер": 1, "кадры": [{"id": "k1", "закадр": "а"}]}],
        "персонажи": [{"id": "c01", "имя": "Анна"}, {"id": "c02", "code": "c02", "имя": "Борис"}],
    }
    people_after = copy.deepcopy(people)
    people_after["персонажи"] = [{"id": "c01", "имя": "Анна"}]
    with pytest.raises(PreservationRejected) as char_exc:
        run_preservation_validators(people, people_after, mode="reject")
    assert any(item.code == "character_deleted" and item.id == "c02" for item in char_exc.value.violations)
    assert all(item.code != "shot_dropped" for item in char_exc.value.violations)


_WS1_SHA = "1ef07735d94fb2bd7133fad65c0d651c5f008103"


def test_copy_inactive_shot_to_other_frame_is_revived() -> None:
    """Копия неактивного шота в другой кадр без restore — ``inactive_shot_revived``."""

    before = {
        "сцены": [
            {"id": "frame-a", "номер": 1, "кадры": [{"id": "k", "активен": False, "закадр": ""}]},
            {"id": "frame-b", "номер": 2, "кадры": [{"id": "b", "закадр": "живой"}]},
        ]
    }
    after = {
        "сцены": [
            {"id": "frame-a", "номер": 1, "кадры": [{"id": "k", "активен": False, "закадр": ""}]},
            {
                "id": "frame-b",
                "номер": 2,
                "кадры": [{"id": "b", "закадр": "живой"}, {"id": "k", "закадр": ""}],
            },
        ]
    }
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, after, mode="reject")
    assert any(item.code == "inactive_shot_revived" and item.id == "k" for item in raised.value.violations)

    stayed = copy.deepcopy(after)
    stayed["сцены"][1]["кадры"][1]["активен"] = False
    assert run_preservation_validators(before, stayed, mode="reject") == []

    allowed = run_preservation_validators(
        before,
        after,
        [{"op": "restore", "shot_id": "k", "frame_uuid": "frame-b"}],
        mode="reject",
    )
    assert allowed == []

    with pytest.raises(PreservationRejected) as scoped:
        run_preservation_validators(
            before,
            after,
            [{"op": "restore", "shot_id": "k", "frame_uuid": "frame-a"}],
            mode="reject",
        )
    assert any(item.code == "inactive_shot_revived" and item.id == "k" for item in scoped.value.violations)


def test_idless_loss_and_named_description_match_1ef07735() -> None:
    """Пустой id даёт ``frames_cleared``, ``описание`` и ``attrs.кадры`` не прячут шот."""

    kept = {"id": "k1", "закадр": "а"}
    lost = {"id": "", "parent_id": "9-K1", "закадр": "кусок"}
    before = {"frames": [{"id": "u1", "attrs": {"кадры": [kept, lost]}}]}
    after = {"frames": [{"id": "u1", "attrs": {"кадры": [kept]}}]}
    with pytest.raises(PreservationRejected) as cleared:
        run_preservation_validators(before, after, mode="reject")
    assert any(item.code == "frames_cleared" and item.id == "кусок" for item in cleared.value.violations)

    named_before = {
        "cells": [
            {
                "id": "c",
                "сцена": "1",
                "кадры": [
                    {"id": "k1", "имя": "Анна", "описание": "стоит у окна"},
                    {"id": "k2", "закадр": "б"},
                ],
            }
        ]
    }
    named_after = {
        "cells": [
            {
                "id": "c",
                "сцена": "1",
                "кадры": [{"id": "k9", "закадр": "а"}, {"id": "k2", "закадр": "б"}],
            }
        ]
    }
    with pytest.raises(PreservationRejected) as named:
        run_preservation_validators(named_before, named_after, mode="reject")
    assert any(item.code == "shot_dropped" and item.id == "k1" for item in named.value.violations)

    def wrapped(shots: list[dict]) -> dict:
        return {"frames": [{"id": "cell1", "сцена": 1, "attrs": {"кадры": shots}}]}

    with pytest.raises(PreservationRejected) as wrapper:
        run_preservation_validators(
            wrapped([{"id": "k1", "закадр": "а"}, {"id": "k2", "закадр": "б"}]),
            wrapped([{"id": "k1", "закадр": "а"}, {"id": "k9", "закадр": "б"}]),
            mode="reject",
        )
    assert any(item.code == "shot_dropped" and item.id == "k2" for item in wrapper.value.violations)


def test_guard_call_sites_pass_with_ws1_impl(tmp_path, monkeypatch) -> None:
    """Двенадцать точек вызова на настоящем preservation.py из 1ef07735.

    Файлы #379 лежат только во временном каталоге и в репозиторий не попадают.
    """
    import importlib
    import io
    import sys
    import tarfile
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    sha = _ensure_ws1_commit(repo)
    extracted = tmp_path / "ws1"
    extracted.mkdir()
    archive = _git(repo, "archive", sha, "app/orchestrator/validators")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
        tar.extractall(extracted, filter="data")
    module_dir = extracted / "app" / "orchestrator" / "validators"
    assert (module_dir / "preservation.py").is_file()
    # #379 @ 1ef07735 не содержит activity_flags: его читает наш код, не preservation.
    flags = module_dir / "activity_flags.py"
    if not flags.is_file():
        flags.write_bytes((repo / "app" / "orchestrator" / "validators" / "activity_flags.py").read_bytes())

    import app.orchestrator.validators as validators_pkg
    import app.pipeline.no_deletions_guard as guard
    import app.services.apply_ops_batches  # noqa: F401 — activity_flags до подмены пути
    import app.services.db_apply  # noqa: F401
    import app.services.montage_board_frames  # noqa: F401
    import app.services.montage_coverage_ops  # noqa: F401
    import app.services.montage_scene_improve  # noqa: F401
    import app.services.nii67_card  # noqa: F401
    import app.services.scene_shot_grammar  # noqa: F401
    import app.services.shot_templates  # noqa: F401
    import app.services.vo_shot_expand  # noqa: F401

    old_path = list(validators_pkg.__path__)
    saved = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name.startswith("app.orchestrator.validators.")
    }
    validators_pkg.__path__ = [str(module_dir)]
    try:
        for name in saved:
            del sys.modules[name]
        real = importlib.import_module("app.orchestrator.validators.preservation")
        monkeypatch.setattr(guard, "run_preservation_validators", real.run_preservation_validators)
        monkeypatch.setattr(guard, "PreservationRejected", real.PreservationRejected)
        rejected = _run_call_sites(real.PreservationRejected, tmp_path / "sites")
    finally:
        validators_pkg.__path__ = old_path
        for name in list(sys.modules):
            if name.startswith("app.orchestrator.validators.") and name not in saved:
                del sys.modules[name]
        sys.modules.update(saved)

    assert not rejected, rejected


def _run_call_sites(rejected_type: type, root) -> list[dict]:
    from _pytest.monkeypatch import MonkeyPatch

    from tests.test_montage_board_frames import test_rewrite_vo_cell_soft_keeps_dropped_shot_and_inactive_vo
    from tests.test_nii67_card import test_insert_duplicate_delete_reorder_stay_on_one_door
    from tests.test_no_deletions_guard import (
        test_coalesce_soft_keeps_frame_shot_and_inactive_vo,
        test_fill_catalog_keeps_shot_builder_dropped_and_inactive_vo,
        test_grammar_soft_keeps_dropped_shot_and_inactive_vo,
        test_montage_mirror_soft_keeps_dropped_shot,
        test_pack_soft_keeps_missing_shot_and_soft_delete_passes,
        test_qc_candidate_keeps_dropped_shot_and_inactive_vo,
        test_repair_vo_keeps_inactive_zakadr,
    )
    from tests.test_r0c_b_keep_frames import test_apply_generated_scenario_keeps_operator_blocks
    from tests.test_vo_shot_expand import test_vo_expand_soft_keeps_dropped_shot_and_inactive_vo

    def _generated(_mp, _path) -> None:
        from loguru import logger

        lines: list[str] = []
        sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
        try:
            test_apply_generated_scenario_keeps_operator_blocks(lines)
        finally:
            logger.remove(sink)

    calls = [
        ("coalesce_cell_shot_ops", lambda _mp, _path: test_coalesce_soft_keeps_frame_shot_and_inactive_vo()),
        (
            "fill_kadry_from_catalog",
            lambda mp, _path: test_fill_catalog_keeps_shot_builder_dropped_and_inactive_vo(mp),
        ),
        ("stage_qc", lambda _mp, _path: test_qc_candidate_keeps_dropped_shot_and_inactive_vo()),
        (
            "scene_shot_grammar",
            lambda mp, _path: test_grammar_soft_keeps_dropped_shot_and_inactive_vo(mp),
        ),
        ("apply_ops_batches", lambda _mp, _path: test_pack_soft_keeps_missing_shot_and_soft_delete_passes()),
        ("repair_shot_vo_ops", lambda _mp, _path: test_repair_vo_keeps_inactive_zakadr()),
        ("montage_coverage_ops", lambda _mp, _path: test_montage_mirror_soft_keeps_dropped_shot()),
        ("nii67_card.delete_block", lambda _mp, _path: test_insert_duplicate_delete_reorder_stay_on_one_door()),
        ("nii67_card.apply_generated_scenario", _generated),
        (
            "vo_shot_expand.keep_assigned_kadry",
            lambda mp, path: test_vo_expand_soft_keeps_dropped_shot_and_inactive_vo(mp, path),
        ),
        (
            "vo_shot_expand.preserve_promoted_frames",
            lambda mp, path: test_vo_expand_soft_keeps_dropped_shot_and_inactive_vo(mp, path),
        ),
        (
            "montage_board_frames",
            lambda mp, _path: test_rewrite_vo_cell_soft_keeps_dropped_shot_and_inactive_vo(mp),
        ),
    ]
    assert len(calls) == 12
    rejected: list[dict] = []
    for name, fn in calls:
        site = root / name
        site.mkdir(parents=True)
        with MonkeyPatch.context() as mp:
            try:
                fn(mp, site)
            except rejected_type as exc:
                codes = [
                    {"code": item.code, "id": item.id, "severity": item.severity}
                    for item in exc.violations
                ]
                rejected.append(
                    {
                        "call_site": name,
                        "revived": any(item.code == "inactive_shot_revived" for item in exc.violations),
                        "violations": codes,
                    }
                )
            except Exception as exc:  # noqa: BLE001 — отчёт точки, которая не прошла
                rejected.append(
                    {
                        "call_site": name,
                        "revived": False,
                        "violations": [{"code": type(exc).__name__, "id": str(exc), "severity": "error"}],
                    }
                )
    return rejected


def _ensure_ws1_commit(repo) -> str:
    probe = _git(repo, "rev-parse", "--verify", f"{_WS1_SHA}^{{commit}}", check=False)
    if probe:
        return _WS1_SHA
    _git(repo, "fetch", "origin", "cursor/ws1-r1-validators")
    return _WS1_SHA


def _git(repo, *args: str, check: bool = True) -> bytes:
    import subprocess

    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
    )
    if check and result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace"))
    if result.returncode != 0:
        return b""
    return result.stdout
