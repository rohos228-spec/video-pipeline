"""Общие ошибки node_store. Один класс на все 409."""

from __future__ import annotations


class VersionConflict(Exception):
    """Версия, с которой читал писатель, уже не совпадает с текущей."""

    status_code = 409
