from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.attributes import flag_modified


def _touch(project) -> None:
    try:
        flag_modified(project, "meta")
    except (SQLAlchemyError, AttributeError):
        pass


_KEY = "deleted_frame_numbers"


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


def mark_frame_deleted(project, number: int) -> None:
    """Пометить номер кадра удалённым, не стирая строку кадра."""

    remember_deleted(project, number)


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
