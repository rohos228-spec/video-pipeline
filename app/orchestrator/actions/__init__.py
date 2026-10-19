"""Живой реестр R0b.

Пакет отдаёт ``ensure_builtin_actions`` и ``registry`` слоя действий:
приёмка ``tests/actions/test_whitelist_no_exec.py`` берёт первый
импортируемый модуль и требует реестр. Схемы результатов — ``contracts``
(локальный эквивалент #378, без смены формата).
"""

from app.services.action_layer.spec import ensure_builtin_actions, registry

__all__ = ["ensure_builtin_actions", "registry"]
