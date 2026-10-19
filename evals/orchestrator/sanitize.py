"""Контрактная санитизация HTML отчётов (контур A).

Продакшен-bleach принадлежит WS2. Здесь строгий разбор стандартной библиотеки
с тем же allowlist, чтобы E7 не ходил в сеть и не зависел от чужого модуля.

Незакрытый разрешённый тег на выходе не балансируется: стенд не повторяет
bleach и для E7 этого достаточно.
"""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser

ALLOWED_TAGS = frozenset(
    {
        "h1",
        "h2",
        "h3",
        "h4",
        "p",
        "ul",
        "ol",
        "li",
        "table",
        "tr",
        "td",
        "th",
        "b",
        "i",
        "code",
        "pre",
        "a",
        "img",
    }
)
DROP_WITH_CONTENT = frozenset({"script", "style", "iframe", "object", "svg", "math"})
VOID_TAGS = frozenset({"img"})

XSS_VECTORS = (
    "<script>alert(1)</script>",
    '<img src=x onerror="alert(1)">',
    '<a href="javascript:alert(1)">x</a>',
    '<iframe src="http://evil.example"></iframe>',
    '<p onclick="alert(1)">x</p>',
)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs, void=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs, void=True)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT and self._skip:
            self._skip -= 1
            return
        if self._skip or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        self.parts.append(escape(data))

    def _open(self, tag: str, attrs: list[tuple[str, str | None]], *, void: bool) -> None:
        tag = tag.lower()
        if tag in DROP_WITH_CONTENT:
            if not void:
                self._skip += 1
            return
        if self._skip or tag not in ALLOWED_TAGS:
            return
        rendered = _safe_attrs(tag, attrs)
        if rendered is None:
            return
        if tag in VOID_TAGS or void:
            self.parts.append(f"<{tag}{rendered}>")
            return
        self.parts.append(f"<{tag}{rendered}>")


def _safe_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> str | None:
    mapping = {key.lower(): (value or "") for key, value in attrs if not key.lower().startswith("on")}
    kept: list[tuple[str, str]] = []
    if tag == "a":
        href = mapping.get("href", "")
        if href.startswith("http://") or href.startswith("https://"):
            kept.append(("href", href))
    elif tag == "img":
        src = mapping.get("src", "")
        if not src.startswith("/api/orchestrator/reports/"):
            return None
        kept.append(("src", src))
    body = "".join(f' {key}="{escape(value, quote=True)}"' for key, value in kept)
    return body


def sanitize_html(raw: str) -> str:
    parser = _Sanitizer()
    parser.feed(raw or "")
    parser.close()
    return "".join(parser.parts)
