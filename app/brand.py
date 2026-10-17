"""Бренд стороннего провайдера не должен попадать в пользовательский текст."""
from __future__ import annotations

import re

_RX = re.compile(r"outsee\.io|outsee|оутси|аутси", re.I)


def scrub(text):
    if not isinstance(text, str) or not text:
        return text
    return _RX.sub(lambda m: "VIA" if ".io" in m.group(0).lower() else "VIA Render", text)


def _scrub_obj(obj, depth=0):
    if obj is None or depth > 3:
        return
    for f in ("text", "caption", "question", "title", "description", "message_text"):
        v = getattr(obj, f, None)
        if isinstance(v, str) and _RX.search(v):
            try:
                setattr(obj, f, scrub(v))
            except Exception:
                pass
    for f in ("media", "input_message_content", "results"):
        v = getattr(obj, f, None)
        if isinstance(v, list):
            for it in v:
                _scrub_obj(it, depth + 1)
        elif v is not None and not isinstance(v, (str, bytes)):
            _scrub_obj(v, depth + 1)
    kb = getattr(obj, "reply_markup", None)
    rows = getattr(kb, "inline_keyboard", None) or getattr(kb, "keyboard", None)
    if rows:
        for row in rows:
            for b in row:
                _scrub_obj(b, depth + 1)


try:
    from aiogram.client.session.middlewares.base import BaseRequestMiddleware

    class BrandScrubMiddleware(BaseRequestMiddleware):
        async def __call__(self, make_request, bot, method):
            try:
                _scrub_obj(method)
            except Exception:
                pass
            return await make_request(bot, method)
except Exception:  # pragma: no cover
    BrandScrubMiddleware = None  # type: ignore
