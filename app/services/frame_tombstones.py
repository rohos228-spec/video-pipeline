"""Номера кадров, которые импорт XLSX не должен оживлять.

Интерфейс совместим с PR #342 / #353: ``deleted_numbers`` читает
``project.meta["deleted_frame_numbers"]``, ``remember_deleted`` /
``forget_deleted`` пишут туда же.

Физически кадр не стирается. Пометка живёт на самом объекте
(``attrs["удалён"]``). Импорт считает номер запрещённым и по этой
пометке, и по списку в meta — чтобы не создать активную копию рядом.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import flag_modified

_KEY = "deleted_frame_numbers"
DELETED_MARK = "удалён"
REASON_MARK = "пометка"
ACTIVE_MARK = "активен"


def _touch(project) -> None:
    try:
        flag_modified(project, "meta")
    except (SQLAlchemyError, AttributeError):
        pass


def _flag_frame(frame) -> None:
    try:
        flag_modified(frame, "attrs")
    except (SQLAlchemyError, AttributeError):
        pass


def deleted_numbers(project) -> set[int]:
    raw = (getattr(project, "meta", None) or {}).get(_KEY) or []
    out: set[int] = set()
    for x in raw:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def remember_deleted(project, number: int) -> None:
    meta = dict(project.meta or {})
    nums = deleted_numbers(project)
    nums.add(int(number))
    meta[_KEY] = sorted(nums)
    project.meta = meta
    _touch(project)


def forget_deleted(project, number: int | None = None) -> None:
    nums = deleted_numbers(project)
    if not nums:
        return
    if number is None:
        nums = set()
    else:
        nums.discard(int(number))
    meta = dict(project.meta or {})
    if nums:
        meta[_KEY] = sorted(nums)
    else:
        meta.pop(_KEY, None)
    project.meta = meta
    _touch(project)


def frame_marked_deleted(frame: Any) -> bool:
    """Кадр помечен «удалён» и должен остаться неактивным."""
    if frame is None:
        return False
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        return False
    return bool(attrs.get(DELETED_MARK))


def frame_is_active(frame: Any) -> bool:
    if frame is None or frame_marked_deleted(frame):
        return False
    attrs = getattr(frame, "attrs", None) or {}
    if isinstance(attrs, dict) and attrs.get(ACTIVE_MARK) is False:
        return False
    return True


def shot_is_active(shot: Any) -> bool:
    """Живой элемент ``кадры[]``. Помеченный не получает закадр, кадр и генерацию."""
    if not isinstance(shot, dict):
        return False
    if shot.get(DELETED_MARK) or shot.get("карантин"):
        return False
    if shot.get(ACTIVE_MARK) is False:
        return False
    return True


_SHOT_ID_KEYS = ("id", "id_shot", "shot_id")
_SHOT_CONTENT_KEYS = ("закадр", "описание", "описание_кадра", "description")


def _shot_id(shot: dict[str, Any]) -> str | None:
    for key in _SHOT_ID_KEYS:
        val = str(shot.get(key) or "").strip()
        if val:
            return val
    return None


def _shot_content_identity(shot: dict[str, Any]) -> tuple[tuple[str, str], ...] | None:
    """Текст шота без ``порядок``/``номер``. Пустой текст не склеивает шоты."""
    parts: list[tuple[str, str]] = []
    for key in _SHOT_CONTENT_KEYS:
        val = str(shot.get(key) or "").strip()
        if val:
            parts.append((key, val))
    if not parts:
        return None
    return tuple(parts)


def merge_kadry_keep_missing(old: Any, new: Any) -> Any:
    """Новый список ``кадры`` не выбрасывает старые шоты.

    Та же семантика, что у WS1 ``_merge_kadry_keep_missing``: совпадение по id,
    без id — только по уникальному тексту, не по ``порядок``. Пропавшие шоты
    остаются в хвосте с ``активен: False``. Входящий флаг не снимается, старый
    неактивный шот не оживает.
    """
    if not isinstance(new, list):
        logger.warning(
            "WARN merge_kadry_keep_missing: новое значение не список ({}) — "
            "старые шоты остаются без изменений",
            type(new).__name__,
        )
        return old
    incoming: list[Any] = []
    seen_ids: set[str] = set()
    incoming_by_id: dict[str, dict[str, Any]] = {}
    new_content_counts: dict[tuple[tuple[str, str], ...], int] = {}
    incoming_by_content: dict[tuple[tuple[str, str], ...], list[dict[str, Any]]] = {}
    for shot in new:
        if not isinstance(shot, dict):
            incoming.append(shot)
            continue
        item = dict(shot)
        incoming.append(item)
        sid = _shot_id(item)
        if sid:
            seen_ids.add(sid)
            incoming_by_id[sid] = item
            continue
        ident = _shot_content_identity(item)
        if ident is not None:
            new_content_counts[ident] = new_content_counts.get(ident, 0) + 1
            incoming_by_content.setdefault(ident, []).append(item)
    if not isinstance(old, list):
        return incoming
    old_content_counts: dict[tuple[tuple[str, str], ...], int] = {}
    for shot in old:
        if isinstance(shot, dict) and _shot_id(shot) is None:
            ident = _shot_content_identity(shot)
            if ident is not None:
                old_content_counts[ident] = old_content_counts.get(ident, 0) + 1
    kept: list[Any] = []
    for shot in old:
        if not isinstance(shot, dict):
            if shot not in incoming:
                kept.append(shot)
            continue
        sid = _shot_id(shot)
        if sid:
            if sid in seen_ids:
                if shot.get(ACTIVE_MARK) is False:
                    incoming_by_id[sid][ACTIVE_MARK] = False
                continue
        else:
            ident = _shot_content_identity(shot)
            if (
                ident is not None
                and old_content_counts.get(ident) == 1
                and new_content_counts.get(ident) == 1
            ):
                if shot.get(ACTIVE_MARK) is False:
                    matched = incoming_by_content.get(ident) or []
                    if len(matched) == 1:
                        matched[0][ACTIVE_MARK] = False
                continue
            if ident is None and any(
                isinstance(item, dict) and _shot_id(item) is None and item == shot
                for item in incoming
            ):
                continue
        marked = dict(shot)
        marked[ACTIVE_MARK] = False
        kept.append(marked)
    if kept:
        labels = []
        for shot in kept:
            if isinstance(shot, dict):
                labels.append(_shot_id(shot) or "?")
            else:
                labels.append(repr(shot))
        logger.warning(
            "WARN merge_kadry_keep_missing: {} шотов нет в новом списке, "
            "остаются в хвосте с активен: False ({})",
            len(kept),
            ", ".join(labels),
        )
    return [*incoming, *kept]


def mark_shots_inactive(shots: Any, *, where: str) -> Any:
    """Пометить шоты списка неактивными. Список и его элементы остаются."""
    if not isinstance(shots, list):
        return shots
    out: list[Any] = []
    labels: list[str] = []
    for shot in shots:
        if not isinstance(shot, dict):
            out.append(shot)
            continue
        item = dict(shot)
        if item.get(ACTIVE_MARK) is not False:
            labels.append(_shot_id(item) or "?")
        item[ACTIVE_MARK] = False
        out.append(item)
    if out:
        logger.warning(
            "WARN {}: {} шотов остаются в списке с активен: False ({})",
            where,
            len(out),
            ", ".join(labels) if labels else "уже помечены",
        )
    return out


class PaidFrameSkipped(RuntimeError):
    """Помеченный кадр не отправляется в платную генерацию."""


def paid_frame_skip_reason(frame: Any) -> str | None:
    """Почему кадр нельзя отправлять в платную генерацию картинки или видео."""
    if frame_is_active(frame):
        return None
    number = getattr(frame, "number", None)
    if frame_marked_deleted(frame):
        return f"кадр #{number} помечен «удалён» — платная генерация пропущена"
    return f"кадр #{number} неактивен — платная генерация пропущена"


def log_paid_frame_skip(frame: Any, *, where: str) -> str | None:
    """WARN на каждый пропуск. Кадр не меняется, кэш между вызовами не копится."""
    reason = paid_frame_skip_reason(frame)
    if not reason:
        return None
    logger.warning("WARN {}: {}", where, reason)
    return reason


def _frame_number(frame) -> int | None:
    try:
        number = getattr(frame, "number", None)
        if number is None:
            return None
        return int(number)
    except (TypeError, ValueError):
        return None


def park_marked_frame_numbers(frames: list, project=None) -> int:
    """Увести помеченные кадры на номера 900000+, чтобы живые остались 1..K.

    Вызывать после перенумерации. Уже занятый номер (другой кадр всё ещё
    держит 900000+) не перезаписывается: берётся следующий свободный.
    Уникальный парковочный номер и ``исходный_номер`` повторный вызов не
    меняет. Оба номера остаются запрещёнными для импорта.
    """
    held: dict[int, int] = {}
    for frame in frames:
        current = _frame_number(frame)
        if current is not None:
            held[current] = held.get(current, 0) + 1
    parked = 0
    cursor = 900_000
    for frame in frames:
        if not frame_marked_deleted(frame):
            continue
        attrs = dict(getattr(frame, "attrs", None) or {})
        current = _frame_number(frame)
        if "исходный_номер" not in attrs and current is not None:
            attrs["исходный_номер"] = current
            frame.attrs = attrs
            _flag_frame(frame)
        already_parked = (
            current is not None and current >= 900_000 and held.get(current) == 1
        )
        if already_parked:
            new_number = current
        else:
            naive = 900_000 + parked
            while held.get(cursor, 0) > 0:
                cursor += 1
            new_number = cursor
            cursor += 1
            if current is not None:
                held[current] = held.get(current, 0) - 1
                if held[current] <= 0:
                    held.pop(current, None)
            held[new_number] = held.get(new_number, 0) + 1
            if naive != new_number or (
                current is not None and current >= 900_000
            ):
                blocked = naive if naive != new_number else current
                logger.warning(
                    "WARN park_marked_frame_numbers: номер {} занят, "
                    "кадр #{} уходит на {}, исходный_номер сохранён",
                    blocked,
                    getattr(frame, "id", None),
                    new_number,
                )
        frame.number = new_number
        if project is not None and new_number is not None:
            remember_deleted(project, int(new_number))
        parked += 1
    return parked


def mark_frame_deleted(frame, project, reason: str) -> None:
    """Пометить кадр удалённым, не стирая строку. Номер попадает в tombstone."""
    attrs = dict(getattr(frame, "attrs", None) or {})
    attrs[DELETED_MARK] = True
    attrs[REASON_MARK] = reason
    attrs["карантин"] = True
    attrs[ACTIVE_MARK] = False
    frame.attrs = attrs
    _flag_frame(frame)
    number = getattr(frame, "number", None)
    if project is not None and number is not None:
        remember_deleted(project, int(number))
    logger.warning(
        "WARN frame_tombstones: кадр #{} id={} помечен «удалён», строка сохранена: {}",
        number,
        getattr(frame, "id", None),
        reason,
    )


def import_skip_reason(project, frame, number: int) -> str | None:
    """Почему импорт XLSX не должен ни обновить, ни создать кадр с этим номером.

    Сначала смотрим пометку на самом кадре. Список ``deleted_frame_numbers``
    нужен только когда строки уже нет: иначе импорт завёл бы активную копию
    с тем же номером.
    """
    if frame is not None and frame_marked_deleted(frame):
        return (
            f"кадр #{int(number)} помечен «удалён» — не оживляем и не дублируем"
        )
    if frame is None and int(number) in deleted_numbers(project):
        return (
            f"номер #{int(number)} в deleted_frame_numbers — не создаём активную копию"
        )
    return None


def seal_import_skip(project, frame, number: int) -> str | None:
    """Пропуск импорта по пометке. Потерянный tombstone-номер восстанавливается."""
    reason = import_skip_reason(project, frame, number)
    if reason and frame is not None and frame_marked_deleted(frame):
        remember_deleted(project, int(number))
    return reason
