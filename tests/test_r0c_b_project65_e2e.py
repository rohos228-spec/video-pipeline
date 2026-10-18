"""Сквозной прогон настоящего экспорта проекта 65.

Фикстура ``tests/fixtures/project65_export.json`` (коммит ``3b9ba2ee``):
38 сцен, 187 шотов, 98 строк кадров. Флага ``активен`` в экспорте нет.
Синтетическая сцена из ``7b64ebb0`` заменена этим прогоном.
"""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "project65_export.json"


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def _shot_id(shot: dict[str, Any]) -> str:
    return str(shot.get("id") or shot.get("shot_id") or shot.get("id_shot") or "").strip()


def _shots(cell: dict[str, Any]) -> list[dict[str, Any]]:
    attrs = cell.get("attrs") if isinstance(cell.get("attrs"), dict) else {}
    rows = attrs.get("кадры")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _set_shots(cell: dict[str, Any], shots: list[dict[str, Any]]) -> None:
    attrs = dict(cell.get("attrs") or {})
    attrs["кадры"] = shots
    cell["attrs"] = attrs


def _cell_from_scene(scene: dict[str, Any]) -> dict[str, Any]:
    vo = str(scene.get("voiceover_text") or "")
    action = str(scene.get("главное_действие") or scene.get("main_action") or "")
    return {
        "uuid": str(scene.get("uuid") or ""),
        "number": scene.get("number"),
        "voiceover_text": vo,
        "главное_действие": action,
        "attrs": {
            "кадры": copy.deepcopy(scene.get("кадры") or []),
            "camera_subdivide": copy.deepcopy(scene.get("camera_subdivide") or {}),
            "главное_действие": action,
            "main_action": action,
            "vo_cell_full": vo,
            "place": scene.get("place") or "",
        },
    }


def _ns(cell: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        uuid=cell["uuid"],
        number=cell["number"],
        sort_key=float(cell["number"] or 0),
        voiceover_text=cell.get("voiceover_text") or "",
        image_prompt="",
        animation_prompt="",
        duration_seconds=2.0,
        attrs=copy.deepcopy(cell.get("attrs") or {}),
    )


def _pull(ns: Any, cell: dict[str, Any]) -> None:
    cell["voiceover_text"] = str(getattr(ns, "voiceover_text", "") or "")
    cell["attrs"] = copy.deepcopy(getattr(ns, "attrs", None) or {})
    attrs = cell["attrs"]
    if not str(cell.get("главное_действие") or "").strip():
        cell["главное_действие"] = str(attrs.get("главное_действие") or "")


def _texts(cell: dict[str, Any]) -> str:
    attrs = cell.get("attrs") if isinstance(cell.get("attrs"), dict) else {}
    parts = [
        str(cell.get("voiceover_text") or ""),
        str(cell.get("главное_действие") or ""),
        str(attrs.get("vo_cell_full") or ""),
        str(attrs.get("главное_действие") or ""),
    ]
    parts.extend(str(shot.get("закадр") or "") for shot in _shots(cell))
    return _norm(" ".join(parts))


def _note_marks(
    step: str,
    before: list[dict[str, Any]],
    after: list[dict[str, Any]],
    marks: list[dict[str, str]],
) -> None:
    from app.services.frame_tombstones import shot_is_active

    after_by = {_shot_id(shot): shot for shot in after if _shot_id(shot)}
    for shot in before:
        sid = _shot_id(shot)
        if not sid or not shot_is_active(shot):
            continue
        nxt = after_by.get(sid)
        assert nxt is not None, f"{step} потерял шот {sid}"
        if shot_is_active(nxt):
            continue
        reason = str(nxt.get("пометка") or nxt.get("fw_qc") or "").strip()
        if not reason:
            assert "закадр_архив" not in nxt, sid
            assert _norm(str(nxt.get("закадр") or "")) == _norm(str(shot.get("закадр") or ""))
            reason = "guard kept_inactive: шаг не вернул этот id"
        marks.append({"step": step, "id": sid, "reason": reason})


def _ops_for(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "frame_uuid": cell["uuid"],
            "fields": {
                "кадры": _shots(cell),
                "главное_действие": cell.get("главное_действие") or "",
            },
        }
        for cell in cells
    ]


def _frames_for(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "uuid": cell["uuid"],
            "number": cell["number"],
            "voiceover_text": cell.get("voiceover_text") or "",
            "закадр": cell.get("voiceover_text") or "",
            "главное_действие": cell.get("главное_действие") or "",
            "attrs": cell.get("attrs") or {},
        }
        for cell in cells
    ]


def _write_ops(cells: list[dict[str, Any]], ops: list[dict[str, Any]]) -> None:
    by_uid = {cell["uuid"]: cell for cell in cells}
    for op in ops:
        if not isinstance(op, dict):
            continue
        uid = str(op.get("frame_uuid") or "")
        cell = by_uid.get(uid)
        fields = op.get("fields") if isinstance(op.get("fields"), dict) else None
        if cell is None or fields is None:
            continue
        rows = fields.get("кадры")
        if isinstance(rows, list):
            _set_shots(cell, rows)
        action = fields.get("главное_действие")
        if isinstance(action, str) and action.strip():
            cell["главное_действие"] = action


def test_project65_export_keeps_rows_texts_and_active_shots(tmp_path: Path) -> None:
    """Экспорт 65 проходит шаги ветки: строки, id и тексты на месте."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    asyncio.run(_run(raw, tmp_path))


async def _run(raw: dict[str, Any], tmp_path: Path) -> None:
    from app.pipeline.no_deletions_guard import run_preservation_validators
    from app.services.apply_ops_batches import (
        coalesce_cell_shot_ops,
        repair_shot_vo_ops,
        run_apply_ops_batched,
        shots_coverage_ops_reason,
    )
    from app.services.frame_tombstones import shot_is_active
    from app.services.gpt_operator_client import OperatorApiResult
    from app.services.montage_coverage_ops import apply_coverage_scene_action
    from app.services.montage_scene_improve import stage_qc
    from app.services.scene_shot_grammar import align_shots_to_action_chain, apply_grammar_to_ops
    from app.services.shot_templates import fill_kadry_from_catalog
    from app.services.shots_report import build_shots_report_model
    from app.services.vo_shot_expand import (
        apply_generation_places_from_sidecars,
        apply_ordered_voiceover_to_stills,
        apply_shot_voiceover_to_cells,
        flattened_coverage_groups,
        merge_flattened_coverage_members,
        promote_shots_to_vo_cells,
        relink_shot_roles_by_scene,
    )

    scenes = raw["scenes"]
    frames = raw["frames_table"]
    fragments = [str(text) for text in raw["fragments"]["texts"]]
    characters = raw["characters"]
    assert len(scenes) == 38
    assert len(frames) == 98
    assert len(fragments) == 38
    assert len(characters) == 7

    cells = [_cell_from_scene(scene) for scene in scenes]
    original_shots: dict[str, dict[str, Any]] = {}
    original_vo: dict[str, str] = {}
    for scene, cell in zip(scenes, cells, strict=True):
        original_vo[cell["uuid"]] = str(scene.get("voiceover_text") or "")
        for shot in _shots(cell):
            sid = _shot_id(shot)
            assert sid
            assert sid not in original_shots
            assert "активен" not in shot
            assert shot_is_active(shot) is True
            original_shots[sid] = copy.deepcopy(shot)
    assert len(original_shots) == 187

    frame_rows = [
        (int(row["id"]), int(row["number"]), str(row.get("voiceover_text") or ""))
        for row in frames
    ]
    assert len({row[0] for row in frame_rows}) == 98

    report = build_shots_report_model([_ns(cell) for cell in cells])
    report_ids = {str(row.get("id") or "") for row in report["shots"]}
    assert original_shots.keys() <= report_ids

    snapshot = {
        "сцены": [
            {
                "id": cell["uuid"],
                "номер": cell["number"],
                "кадры": copy.deepcopy(_shots(cell)),
            }
            for cell in cells
        ],
        "персонажи": copy.deepcopy(characters),
    }
    echo = copy.deepcopy(snapshot)
    assert run_preservation_validators(snapshot, echo, mode="soft") == []
    assert echo == snapshot

    marks: list[dict[str, str]] = []

    def _sync(step: str, before_cells: list[dict[str, Any]]) -> None:
        before = {
            cell["uuid"]: copy.deepcopy(_shots(cell)) for cell in before_cells
        }
        for cell in cells:
            _note_marks(step, before.get(cell["uuid"]) or [], _shots(cell), marks)

    # Грамматика и выравнивание по цепи действия.
    before_cells = copy.deepcopy(cells)
    ops = _ops_for(cells)
    frames_arg = _frames_for(cells)
    apply_grammar_to_ops(ops, frames_arg)
    align_shots_to_action_chain(ops, frames_arg)
    _write_ops(cells, ops)
    _sync("scene_shot_grammar", before_cells)

    # Каталог шаблонов.
    before_cells = copy.deepcopy(cells)
    for cell in cells:
        filled = fill_kadry_from_catalog(
            _shots(cell),
            str(cell.get("главное_действие") or ""),
            cell_number=int(cell["number"] or 1),
        )
        _set_shots(cell, filled)
    _sync("shot_templates", before_cells)

    # Склейка нескольких ops одной ячейки.
    before_cells = copy.deepcopy(cells)
    coalesced = coalesce_cell_shot_ops(_ops_for(cells), _frames_for(cells), patch=False)
    _write_ops(cells, coalesced)
    _sync("coalesce_cell_shot_ops", before_cells)

    # Покрытие: причина QC и запись цепи на ячейку.
    before_cells = copy.deepcopy(cells)
    ops = _ops_for(cells)
    frames_arg = _frames_for(cells)
    repair_shot_vo_ops(ops, frames_arg)
    shots_coverage_ops_reason(ops, frames_arg)
    _write_ops(cells, ops)
    for cell in cells:
        parent = _ns(cell)
        members = [parent]
        await apply_coverage_scene_action(
            session=None,  # type: ignore[arg-type]
            project=SimpleNamespace(id=65),
            frame=parent,
            frames=members,
            action=str(cell.get("главное_действие") or parent.voiceover_text or "кадр"),
            grow=False,
            kadry=copy.deepcopy(_shots(cell)),
            lock_parent_plan=False,
        )
        _pull(parent, cell)
    _sync("coverage_ops", before_cells)

    # Свои функции vo_shot_expand.
    before_cells = copy.deepcopy(cells)
    nodes = [_ns(cell) for cell in cells]
    by_uuid = {node.uuid: node for node in nodes}
    groups = flattened_coverage_groups(nodes)
    for members in groups.values():
        parent, _extra = merge_flattened_coverage_members(members)
        by_uuid[str(parent.uuid)] = parent
    relink_shot_roles_by_scene(list(by_uuid.values()))
    apply_shot_voiceover_to_cells(list(by_uuid.values()))
    scenes_dir = tmp_path / "scenes"
    scenes_dir.mkdir()
    apply_ordered_voiceover_to_stills(list(by_uuid.values()), [], scenes_dir)
    apply_generation_places_from_sidecars(list(by_uuid.values()), scenes_dir)
    promote_shots_to_vo_cells(list(by_uuid.values()))
    for cell in cells:
        node = by_uuid.get(cell["uuid"])
        if node is not None:
            _pull(node, cell)
    _sync("vo_shot_expand", before_cells)

    # Пачки apply-ops: модель отвечает текущими кадрами.
    before_cells = copy.deepcopy(cells)

    async def _stub_llm(**kwargs: Any) -> OperatorApiResult:
        path = kwargs["input_paths"][0]
        ctx = json.loads(Path(path).read_text(encoding="utf-8"))
        ops_out = []
        for fr in ctx.get("frames") or []:
            attrs = fr.get("attrs") if isinstance(fr.get("attrs"), dict) else {}
            ops_out.append(
                {
                    "frame_uuid": fr.get("uuid"),
                    "fields": {
                        "кадры": copy.deepcopy(attrs.get("кадры") or []),
                        "главное_действие": fr.get("главное_действие")
                        or attrs.get("главное_действие")
                        or "",
                    },
                }
            )
        return OperatorApiResult(
            reply_text="{}",
            output_paths=[],
            apply_ops={"ops": ops_out},
        )

    import app.services.apply_ops_batches as batches

    original_api = batches.run_operator_api
    batches.run_operator_api = _stub_llm  # type: ignore[assignment]
    applied: list[dict[str, Any]] = []

    async def _apply(payload: dict[str, Any]) -> None:
        applied.extend(payload.get("ops") or [])

    try:
        await run_apply_ops_batched(
            project_dir=tmp_path,
            node_key="shots",
            role="shots",
            output_mode="json",
            prompt="project-65",
            accompanying="",
            db_ctx={"frames": _frames_for(cells), "project_id": 65},
            ctx_path=tmp_path / "db_frames.json",
            project_id=65,
            dense=False,
            apply_fn=_apply,
            force_full=True,
            stagger_sec=0,
            parallel_max=1,
            footer_kind="shots",
            chunk_size=3,
        )
    finally:
        batches.run_operator_api = original_api
    if applied:
        _write_ops(cells, applied)
    _sync("apply_ops_batches", before_cells)

    # QC: отдельная копия, чтобы парковка якоря не снимала id с основного прогона.
    qc_marks_before = len(marks)
    for cell in cells:
        qc_cell = copy.deepcopy(cell)
        parent = _ns(qc_cell)
        shots = _shots(qc_cell)
        before_shots = copy.deepcopy(shots)

        async def _ask(_prompt: str, _shots: list[dict[str, Any]] = shots, _uid: str = cell["uuid"]) -> str:
            return json.dumps(
                {"ops": [{"frame_uuid": _uid, "fields": {"кадры": _shots}}]},
                ensure_ascii=False,
            )

        units = [
            {
                "n": index,
                "бюджет": 1,
                "закадр": str(shot.get("закадр") or ""),
            }
            for index, shot in enumerate(shots, start=1)
        ]
        for index, shot in enumerate(shots, start=1):
            shot["якорь_n"] = index
        parent.attrs = dict(parent.attrs or {})
        parent.attrs["кадры"] = shots
        revised, _soft = await stage_qc(
            _ask,
            parent=parent,
            vo=str(qc_cell.get("voiceover_text") or ""),
            shots=shots,
            units=units,
            place=str((qc_cell.get("attrs") or {}).get("place") or ""),
            nodes=[],
        )
        # repair_shots внутри stage_qc переписывает суффикс K по позиции.
        # Хранитель видит уже новый id. Старый id обязан остаться, если
        # ремонт его не переименовал; переименованный кадр остаётся в списке.
        revised_ids = {_shot_id(shot) for shot in revised if _shot_id(shot)}
        assert len(revised) >= len(before_shots)
        revised_blob = _norm(" ".join(str(shot.get("закадр") or "") for shot in revised))
        for shot in before_shots:
            sid = _shot_id(shot)
            piece = _norm(str(shot.get("закадр") or ""))
            if piece:
                assert piece in revised_blob or piece in _norm(str(qc_cell.get("voiceover_text") or ""))
            if sid in revised_ids:
                continue
            same_action = [
                row
                for row in revised
                if _norm(str(row.get("действие") or "")) == _norm(str(shot.get("действие") or ""))
            ]
            assert same_action, sid
            if not shot_is_active(same_action[0]):
                reason = str(same_action[0].get("пометка") or same_action[0].get("fw_qc") or "").strip()
                marks.append(
                    {
                        "step": "montage_scene_improve",
                        "id": sid,
                        "reason": reason or "repair_shots переписал id, кадр сохранён",
                    }
                )
        still = {_shot_id(shot) for shot in before_shots} & revised_ids
        _note_marks(
            "montage_scene_improve",
            [shot for shot in before_shots if _shot_id(shot) in still],
            [shot for shot in revised if _shot_id(shot) in still],
            marks,
        )

    # Итог основного прогона: исходные id, тексты, сцены и строки кадров.
    found_ids = {sid for cell in cells for sid in (_shot_id(shot) for shot in _shots(cell)) if sid}
    missing_ids = set(original_shots) - found_ids
    extra_ids = found_ids - set(original_shots)
    assert not missing_ids, sorted(missing_ids)[:12]
    for cell in cells:
        blob = _texts(cell)
        assert _norm(original_vo[cell["uuid"]]) in blob
        for shot in _shots(cell):
            sid = _shot_id(shot)
            if sid not in original_shots:
                continue
            piece = _norm(str(original_shots[sid].get("закадр") or ""))
            if piece:
                assert piece in blob, sid
    assert [str(text) for text in raw["fragments"]["texts"]] == fragments
    assert len(cells) == 38
    assert [(int(row["id"]), int(row["number"]), str(row.get("voiceover_text") or "")) for row in frames] == frame_rows
    assert {item["id"] for item in characters} == {item["id"] for item in raw["characters"]}

    still_active = []
    for cell in cells:
        for shot in _shots(cell):
            sid = _shot_id(shot)
            if sid in original_shots and shot_is_active(shot):
                still_active.append(shot)
    visible = build_shots_report_model([_ns(cell) for cell in cells])
    visible_ids = {str(row.get("id") or "") for row in visible["shots"]}
    for shot in still_active:
        assert shot_is_active(shot)
        assert "активен" not in shot or shot.get("активен") is not False
        assert _shot_id(shot) in visible_ids

    # Пометки QC на копии не прячут шоты основного прогона.
    # Новый неактивный шот либо помечен самим шагом, либо возвращён хранителем
    # после того, как шаг выбросил id. Без причины тест падает в _note_marks.
    assert qc_marks_before <= len(marks)
    for mark in marks:
        assert mark["id"] in original_shots
        assert mark["reason"]
    print(
        "PROJECT65_COUNTS",
        "before",
        len(scenes),
        len(original_shots),
        len(frames),
        "after",
        len(cells),
        len(found_ids),
        len(frames),
        "active",
        len(still_active),
        "inactive",
        len(marks),
        "extra",
        len(extra_ids),
    )
    if extra_ids:
        extra_rows = [
            (
                _shot_id(shot),
                shot.get("активен", "missing"),
                str(shot.get("пометка") or shot.get("fw_qc") or ""),
            )
            for cell in cells
            for shot in _shots(cell)
            if _shot_id(shot) in extra_ids
        ]
        print("PROJECT65_EXTRA", extra_rows)
    print("PROJECT65_MARKS", len(marks))
    reasons: dict[tuple[str, str], list[str]] = {}
    for mark in marks:
        reasons.setdefault((mark["step"], mark["reason"]), []).append(mark["id"])
    for (step, reason), ids in sorted(reasons.items()):
        print(f"PROJECT65_INACTIVE {step} | {len(ids)} | {reason} | {', '.join(ids)}")
