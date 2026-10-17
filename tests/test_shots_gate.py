"""Гейт «Шотов»: ответ с кадрами пишется. Выкидывается только пустой ответ."""

from __future__ import annotations

import json

import pytest

U1 = "aaaa1111-0000-0000-0000-000000000001"
U2 = "bbbb2222-0000-0000-0000-000000000002"


def _shot(sid, parent=None, *, img="", start="", vo="кусок закадра"):
    out = {"id": sid, "parent_id": parent, "закадр": vo, "действие": "шаг"}
    if img:
        out["промт_картинки"] = img
    if start:
        out["промт_старт"] = start
    return out


CELL_VO = "кусок закадра первый кусок закадра второй"


def _good_op(uid):
    # Склейка непустых закадров = закадр ячейки; пустой закадр у кадра — норма.
    return {
        "frame_uuid": uid,
        "fields": {
            "кадры": [
                _shot("1-S1-K1", img="главный промт", vo="кусок закадра первый"),
                _shot("1-S1-K2", "1-S1-K1", start="старт ребёнка", vo=""),
                _shot("1-S2-K1", start="старт второй сцены", vo="кусок закадра второй"),
                _shot("1-S2-K2", "1-S2-K1", start="старт", vo=""),
            ]
        },
    }


def _frames(*uids):
    return [{"uuid": u, "voiceover_text": CELL_VO} for u in uids]


def test_gate_accepts_full_reply() -> None:
    from app.services.apply_ops_batches import shots_reply_gate_problems

    assert shots_reply_gate_problems([_good_op(U1)], _frames(U1)) == []


def test_gate_accepts_empty_vo_on_shot() -> None:
    """Пустой закадр у кадра (пауза/перебивка) — не брак (промт §4)."""
    from app.services.apply_ops_batches import shots_reply_gate_problems

    op = _good_op(U1)
    assert not op["fields"]["кадры"][1]["закадр"]
    assert shots_reply_gate_problems([op], _frames(U1)) == []


@pytest.mark.parametrize(
    ("idx", "patch", "needle"),
    [
        (0, {"промт_картинки": ""}, "главный кадр без промт_картинки"),
        (1, {"промт_старт": ""}, "дочерний кадр без промт_старт"),
        (2, {"промт_старт": ""}, "главный кадр сцены без промт_старт"),
        (3, {"закадр": "кусок закадра второй"}, "склейка закадра"),
        (0, {"закадр": "x" * 90}, "> 80"),
    ],
)
def test_gate_flags_missing(idx, patch, needle) -> None:
    from app.services.apply_ops_batches import shots_reply_gate_problems

    op = _good_op(U1)
    op["fields"]["кадры"][idx].update(patch)
    problems = shots_reply_gate_problems([op], _frames(U1))
    assert problems and all(u == U1 for u, _ in problems)
    assert any(needle in w for _, w in problems), problems


def test_gate_flags_op_without_shots() -> None:
    from app.services.apply_ops_batches import shots_reply_gate_problems

    assert shots_reply_gate_problems([{"frame_uuid": U1, "fields": {"кадры": []}}])


def _bad_op(uid):
    op = _good_op(uid)
    op["fields"]["кадры"][1]["промт_старт"] = ""
    return op


async def _run(tmp_path, monkeypatch, replies_by_uid, *, chunk_size=None):
    """replies_by_uid: uuid → список ops по очереди вызовов этой пачки."""
    from app.services import apply_ops_batches as aob
    from app.services.gpt_operator_client import OperatorApiResult

    calls: list[str] = []
    applied: list[list] = []

    async def fake_run(**kwargs):
        batch = json.loads(kwargs["input_paths"][0].read_text(encoding="utf-8"))
        uid = batch["frames"][0]["uuid"]
        calls.append(uid)
        op = replies_by_uid[uid].pop(0)
        return OperatorApiResult(
            reply_text=json.dumps({"ops": [op]}, ensure_ascii=False),
            output_paths=[],
            apply_ops={"ops": [op]},
        )

    def passthrough(ops, chunk, *, kind, all_frames=None, ctx=None):
        return aob.CodePassesResult(ops=list(ops), entries=[])

    async def apply_fn(payload):
        applied.append(list(payload.get("ops") or []))

    monkeypatch.setattr(aob, "run_operator_api", fake_run)
    monkeypatch.setattr(aob, "run_code_passes", passthrough)
    frames = _frames(*replies_by_uid)
    ctx = tmp_path / "db_frames.json"
    ctx.write_text("{}", encoding="utf-8")
    res = await aob.run_apply_ops_batched(
        project_dir=tmp_path,
        node_key="n_excel_gpt_fw_shots",
        role="excel_gpt",
        output_mode="project_file",
        prompt="p",
        accompanying="",
        db_ctx={"frames": frames},
        ctx_path=ctx,
        project_id=1,
        dense=False,
        apply_fn=apply_fn,
        force_full=True,
        chunk_size=chunk_size,
        footer_kind="shots_coverage",
    )
    return res, calls, applied


async def test_bad_reply_is_written_once(tmp_path, monkeypatch) -> None:
    res, calls, applied = await _run(
        tmp_path, monkeypatch, {U1: [_bad_op(U1), _good_op(U1)]}
    )
    assert calls == [U1]
    assert len(applied) == 1 and applied[0][0]["frame_uuid"] == U1
    assert "skipped_packs" not in (res.apply_ops or {})


async def test_bad_reply_not_skipped(tmp_path, monkeypatch) -> None:
    res, calls, applied = await _run(
        tmp_path, monkeypatch, {U1: [_bad_op(U1), _bad_op(U1), _good_op(U1)]}
    )
    assert calls == [U1]
    assert len(applied) == 1 and applied[0][0]["frame_uuid"] == U1
    assert "skipped_packs" not in (res.apply_ops or {})


async def test_empty_shots_are_not_written(tmp_path, monkeypatch) -> None:
    empty = {"frame_uuid": U1, "fields": {"кадры": []}}
    res, calls, applied = await _run(tmp_path, monkeypatch, {U1: [empty, empty]})
    assert calls == [U1, U1]
    assert applied == []
    skipped = (res.apply_ops or {}).get("skipped_packs") or []
    assert len(skipped) == 1
    assert skipped[0]["uuids"] == [U1]


def test_place_without_action_is_not_junk() -> None:
    """Площадка без нумерованной цепи — не брак."""
    from app.services.apply_ops_batches import outright_junk_reason

    op = {
        "frame_uuid": U1,
        "fields": {"площадка": {"зоны": [{"id": "архив"}]}, "меняет": []},
    }
    assert outright_junk_reason(op, "action_chain") is None
    assert outright_junk_reason({"frame_uuid": U1, "fields": {}}, "action_chain") == (
        "пустой ответ"
    )


async def test_bad_and_good_packs_both_applied(tmp_path, monkeypatch) -> None:
    res, calls, applied = await _run(
        tmp_path,
        monkeypatch,
        {U1: [_bad_op(U1), _bad_op(U1)], U2: [_good_op(U2)]},
        chunk_size=1,
    )
    assert sorted(calls) == sorted([U1, U2])
    assert sorted(ops[0]["frame_uuid"] for ops in applied) == [U1, U2]
    assert "skipped_packs" not in (res.apply_ops or {})
