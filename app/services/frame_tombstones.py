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


def park_marked_frame_numbers(frames: list, project=None) -> int:
    """Увести помеченные кадры на номера 900000+, чтобы живые остались 1..K.

    Вызывать после перенумерации. Оба номера (старый, уже в tombstone, и
    новый) остаются запрещёнными для импорта.
    """
    parked = 0
    for frame in frames:
        if not frame_marked_deleted(frame):
            continue
        frame.number = 900_000 + parked
        if project is not None:
            remember_deleted(project, int(frame.number))
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
