"""Необязательный прогон «в тени» перед применением: реестр вызываемых объектов.

По умолчанию ничего не зарегистрировано. Контур сам не запускает ни модели, ни
платные вызовы; зарегистрированный хук отвечает за свой расход. Любой хук,
вернувший не ok или упавший, останавливает применение (изменение не пишется).
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

ShadowHook = Callable[[dict], Any]
_HOOKS: dict[str, ShadowHook] = {}


def register_shadow_hook(name: str, hook: ShadowHook) -> None:
    if not name or not callable(hook):
        raise ValueError("shadow hook needs a name and a callable")
    _HOOKS[name] = hook


def unregister_shadow_hook(name: str) -> None:
    _HOOKS.pop(name, None)


def registered() -> list[str]:
    return sorted(_HOOKS)


async def run_shadow_hooks(proposal: dict) -> list[dict]:
    """Хук получает копию предложения и возвращает {"ok": bool, "note": str}."""
    results: list[dict] = []
    for name in registered():
        try:
            out = _HOOKS[name](dict(proposal))
            if inspect.isawaitable(out):
                out = await out
            ok = bool(out.get("ok")) if isinstance(out, dict) else bool(out)
            note = str(out.get("note", ""))[:500] if isinstance(out, dict) else ""
        except Exception as exc:  # noqa: BLE001 - упавший хук = не пройдено
            ok, note = False, f"hook error: {type(exc).__name__}"
        results.append({"name": name, "ok": ok, "note": note})
    return results
