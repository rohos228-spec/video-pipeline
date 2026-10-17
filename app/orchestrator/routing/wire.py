"""Живые идентификаторы 52pay и Kie.

Карту «каталожный id → ключ и wire-модель» не копируем: её держит
``app.services.pay52_routes.resolve_text``. Модуль приедет с веткой
``r0a-merge-fable`` и на текущем ``main`` может отсутствовать.
База 52pay — ``settings.pay52_base_url`` (alias ``PAY52_BASE_URL``).
Слаг Kie — ``settings.gpt_model`` (alias ``GPT_MODEL``), не каталожный id с точками.
"""

from __future__ import annotations

import os
from typing import Any

PAY52_BASE_DEFAULT = "https://api.52pay.com/v1"
KIE_MODEL_DEFAULT = "gpt-5-6-sol"


class Pay52ModuleMissing:
    """``pay52_routes`` ещё нет в дереве. Транспорт берёт env и settings."""


PAY52_MODULE_MISSING = Pay52ModuleMissing()


def pay52_base_url() -> str:
    """База 52pay. Сначала settings, затем env, затем дефолт поля Settings."""
    try:
        from app.settings import settings

        base = str(getattr(settings, "pay52_base_url", "") or "").strip()
        if base:
            return base.rstrip("/")
    except Exception:
        pass
    base = (os.environ.get("PAY52_BASE_URL") or "").strip()
    if base:
        return base.rstrip("/")
    return PAY52_BASE_DEFAULT


def kie_model_slug() -> str:
    """Wire-слаг Kie: ``settings.gpt_model`` / ``GPT_MODEL``."""
    try:
        from app.settings import settings

        slug = str(getattr(settings, "gpt_model", "") or "").strip()
        if slug:
            return slug
    except Exception:
        pass
    slug = (os.environ.get("GPT_MODEL") or "").strip()
    return slug or KIE_MODEL_DEFAULT


def resolve_pay52(model: str) -> Any:
    """Маршрут 52pay из существующего ``resolve_text``.

    ``Pay52ModuleMissing`` — модуля нет, хоп не пропускаем.
    ``None`` — модуль ответил «маршрута нет» (пустой ключ или неизвестная модель):
    хоп пропускается, живой POST не делается.
    """
    try:
        from app.services.pay52_routes import resolve_text
    except ImportError:
        return PAY52_MODULE_MISSING
    try:
        return resolve_text(model)
    except (AttributeError, ImportError):
        return None
