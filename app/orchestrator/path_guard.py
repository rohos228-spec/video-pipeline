"""Проверка «путь внутри корня» для ядра WS3.

Сравнение строк через ``startswith`` считает ``/allowed_evil`` вложенным
в ``/allowed``. Здесь оба пути приводятся ``realpath`` (симлинк снаружи
корня раскрывается) и сравниваются ``os.path.commonpath`` по компонентам.
"""

from __future__ import annotations

import os
from os import PathLike


def path_is_within(root: str | PathLike[str], candidate: str | PathLike[str]) -> bool:
    """True, если ``candidate`` после ``realpath`` лежит внутри ``root``.

    Сам корень считается внутри. Разные диски и пустые пути — нет.
    """
    try:
        root_real = os.path.realpath(root)
        cand_real = os.path.realpath(candidate)
    except (OSError, ValueError):
        return False
    if not root_real or not cand_real:
        return False
    try:
        common = os.path.commonpath([root_real, cand_real])
    except ValueError:
        return False
    return common == root_real
