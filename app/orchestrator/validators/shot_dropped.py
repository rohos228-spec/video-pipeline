"""Шот был в сцене и пропал, не будучи ``активен: False``.

Ловит, в частности, выкидывание чужого места в
``align_shots_to_action_chain``. Саму функцию не чинит и ничего не пишет.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.validators.activity_flags import shot_is_active
from app.orchestrator.validators.entities import collect_cells, frame_id, iter_shots, text_of
from app.orchestrator.validators.grammar import shot_counts_as_active
from app.orchestrator.validators.violations import Violation, sorted_violations


def check_shot_dropped(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    del ops
    if before is None or after is None:
        return []
    after_ids = {fid for shot in iter_shots(after) if (fid := frame_id(shot))}
    violations: list[Violation] = []
    seen: set[str] = set()
    for shot in iter_shots(before):
        fid = frame_id(shot)
        if not fid or fid in seen:
            continue
        seen.add(fid)
        if not shot_counts_as_active(shot) or fid in after_ids:
            continue
        scene = shot.get("сцена") if isinstance(shot, dict) else None
        scene_key = "" if scene in (None, "") else str(scene)
        path = f"scenes/{scene_key}/кадры/{fid}" if scene_key else f"кадры/{fid}"
        violations.append(
            Violation(code="shot_dropped", severity="error", path=path, id=fid)
        )
    return sorted_violations(violations)


_ANON_KEYS = ("закадр", "voiceover", "vo", "text", "текст", "действие")


def check_marked_shot_removed(before: Any, after: Any, ops: Any = None) -> list[Violation]:
    """Помеченный шот исчез из «после» целиком.

    ``shot_dropped`` / ``frame_deleted`` / ``frames_cleared`` пропускают
    уже неактивный шот. Здесь активность только ``shot_is_active``.
    Перенос и шот на месте не ошибка: id или текст ещё есть в «после».
    Безымянный помеченный шот на том же индексе того же кадра тоже на месте,
    даже если у него другой текст. Id по-прежнему сверяется по всему снимку.
    """

    del ops
    if before is None or after is None:
        return []
    after_cells = _kadry_cells(after)
    after_shots = _listed_shots(after_cells)
    after_ids = {fid for shot in after_shots if (fid := frame_id(shot))}
    after_anon = {_anon_text(shot) for shot in after_shots if frame_id(shot) is None}
    after_by_key = _cells_by_key(after_cells)
    violations: list[Violation] = []
    seen: set[str] = set()
    used: dict[tuple[str, str | int], int] = {}
    for index, cell in enumerate(_kadry_cells(before)):
        key = _cell_key(cell, index)
        slot = used.get(key, 0)
        used[key] = slot + 1
        group = after_by_key.get(key) or []
        after_list = group[slot].get("кадры") if slot < len(group) else None
        if not isinstance(after_list, list):
            after_list = []
        shots = cell.get("кадры")
        if not isinstance(shots, list):
            continue
        # «То же место» засчитывается, только если помеченных безымянных шотов
        # в кадре не стало меньше: иначе сдвиг прячет удаление.
        slot_ok = _marked_idless_count(after_list) >= _marked_idless_count(shots)
        cell_texts = {
            _anon_text(other)
            for other in shots
            if isinstance(other, dict) and frame_id(other) is None
        }
        for shot_index, shot in enumerate(shots):
            if not isinstance(shot, dict) or shot_is_active(shot):
                continue
            fid = frame_id(shot)
            if fid:
                if fid in after_ids or fid in seen:
                    continue
                seen.add(fid)
                token = fid
            else:
                if slot_ok and _same_slot_still_marked(
                    after_list, shot_index, _anon_text(shot), cell_texts
                ):
                    continue
                token = _anon_text(shot)
                if not token or token in after_anon or token in seen:
                    continue
                seen.add(token)
            violations.append(
                Violation(
                    code="marked_shot_removed",
                    severity="error",
                    path=f"кадры/{token}",
                    id=token,
                )
            )
    return sorted_violations(violations)


def _kadry_cells(side: Any) -> list[dict[str, Any]]:
    return [cell for cell in collect_cells(side) if isinstance(cell.get("кадры"), list)]


def _listed_shots(cells: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for cell in cells:
        shots = cell.get("кадры")
        if not isinstance(shots, list):
            continue
        for shot in shots:
            if isinstance(shot, dict):
                found.append(shot)
    return found


def _cells_by_key(
    cells: list[dict[str, Any]],
) -> dict[tuple[str, str | int], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str | int], list[dict[str, Any]]] = {}
    for index, cell in enumerate(cells):
        grouped.setdefault(_cell_key(cell, index), []).append(cell)
    return grouped


def _cell_key(cell: dict[str, Any], index: int) -> tuple[str, str | int]:
    ident = frame_id(cell)
    if ident:
        return ("id", ident)
    number = cell.get("номер", cell.get("number"))
    if number not in (None, ""):
        return ("num", str(number))
    return ("idx", index)


def _marked_idless_count(shots: list[Any]) -> int:
    return sum(
        1
        for shot in shots
        if isinstance(shot, dict) and frame_id(shot) is None and not shot_is_active(shot)
    )


def _same_slot_still_marked(
    after_shots: list[Any],
    shot_index: int,
    own_text: str = "",
    cell_texts: set[str] | None = None,
) -> bool:
    """Тот же индекс кадра: шота без id всё ещё нет среди живых.

    Если на этом месте стоит текст другого шота того же кадра, это сдвиг
    соседа, а не правка на месте.
    """

    if shot_index >= len(after_shots):
        return False
    other = after_shots[shot_index]
    if not isinstance(other, dict) or frame_id(other) is not None:
        return False
    if shot_is_active(other):
        return False
    other_text = _anon_text(other)
    if cell_texts and other_text != own_text and other_text in cell_texts:
        return False
    return True


def _anon_text(shot: dict[str, Any]) -> str:
    for key in _ANON_KEYS:
        text = text_of(shot.get(key))
        if text:
            return text
    return ""
