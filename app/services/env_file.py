"""Последний непустой ключ в .env. Пустой дубль `KIE_API_KEY=` не затирает."""

from __future__ import annotations

from pathlib import Path


def _strip_value(raw: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if text[0] in {'"', "'"}:
        quote = text[0]
        end = text.find(quote, 1)
        if end > 0:
            return text[1:end]
        return text.strip(quote).strip()
    return text.split("#", 1)[0].strip().strip('"').strip("'")


def last_nonempty_dotenv_values(path: Path, names: tuple[str, ...] | list[str]) -> dict[str, str]:
    want = {n.upper(): n for n in names}
    found: dict[str, str] = {}
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="cp1251")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key_u = key.strip().lstrip("\ufeff").upper()
        if key_u not in want:
            continue
        cleaned = _strip_value(val)
        if cleaned:
            found[key_u] = cleaned
    return {want[k]: v for k, v in found.items() if k in want}


def last_nonempty_dotenv_value(path: Path, *names: str) -> str:
    vals = last_nonempty_dotenv_values(path, names)
    for name in names:
        hit = vals.get(name)
        if hit:
            return hit
    return ""
