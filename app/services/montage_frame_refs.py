"""Ручные референсы кадра: то, что оператор добавил прямо на доске монтажа.

Автоматические рефы (персонажи ячейки, предметы, still VO-родителя) приходят из
листов и папок проекта — их считает `montage_board._group_refs_for_frames`.
Здесь — рефы, которые оператор принёс сам: файл + вид (персонаж / предмет /
фон / просто реф кадра) + **обязательное описание**, иначе генератор не знает,
что с картинки брать.

Хранение: файл в ``data/<project>/refs/``, запись в ``Frame.attrs["ref_manual"]``
(SoT — БД проекта, как и остальные правки доски).
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

REF_KINDS: dict[str, str] = {
    "character": "персонаж",
    "item": "предмет",
    "background": "фон",
    "other": "реф кадра",
}

#: Подсказка оператору, что писать в описании для каждого вида.
REF_KIND_HINTS: dict[str, str] = {
    "character": "внешность: возраст, лицо, волосы, одежда, что делает в кадре",
    "item": "предмет: что это, материал, размер, состояние, как попадает в кадр",
    "background": "фон: что за место, время суток, глубина, чем занят задний план",
    "other": "что именно брать с этой картинки в кадр",
}

_ATTR_KEY = "ref_manual"
_IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def normalize_ref_kind(raw: str | None) -> str:
    kind = (raw or "").strip().lower()
    if kind in REF_KINDS:
        return kind
    aliases = {
        "персонаж": "character",
        "char": "character",
        "предмет": "item",
        "prop": "item",
        "фон": "background",
        "bg": "background",
        "реф": "other",
    }
    return aliases.get(kind, "other")


def refs_dir(data_dir: Path) -> Path:
    return data_dir / "refs"


def manual_refs(frame: Any) -> list[dict[str, Any]]:
    """Записи рефов кадра как есть (без проверки файлов)."""
    raw = (getattr(frame, "attrs", None) or {}).get(_ATTR_KEY)
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict) and r.get("id")]


def manual_ref_paths(data_dir: Path, frame: Any) -> list[Path]:
    """Файлы рефов кадра для генерации — в порядке добавления."""
    out: list[Path] = []
    for row in manual_refs(frame):
        path = _ref_path(data_dir, row)
        if path is not None and path.is_file():
            out.append(path)
    return out


def manual_refs_for_board(data_dir: Path, frame: Any) -> list[dict[str, Any]]:
    """DTO для доски: вид, описание и превью-URL."""
    from app.services.montage_board import _preview_url

    out: list[dict[str, Any]] = []
    for row in manual_refs(frame):
        kind = normalize_ref_kind(str(row.get("kind") or ""))
        path = _ref_path(data_dir, row)
        out.append(
            {
                "id": str(row.get("id") or ""),
                "kind": kind,
                "kind_label": REF_KINDS[kind],
                "description": str(row.get("описание") or row.get("description") or ""),
                "image_url": _preview_url(path) if path is not None else None,
            }
        )
    return out


def manual_ref_prompt_note(frame: Any) -> str:
    """Описания ручных рефов для промта: генератор должен знать, что с них брать."""
    parts: list[str] = []
    for row in manual_refs(frame):
        text = str(row.get("описание") or row.get("description") or "").strip()
        if not text:
            continue
        kind = normalize_ref_kind(str(row.get("kind") or ""))
        parts.append(f"{REF_KINDS[kind]} — {text}")
    if not parts:
        return ""
    return "Референсы кадра: " + "; ".join(parts) + "."


def append_manual_ref_note(prompt: str, frame: Any) -> str:
    note = manual_ref_prompt_note(frame)
    if not note or note in (prompt or ""):
        return prompt
    base = (prompt or "").rstrip()
    return f"{base}\n{note}" if base else note


def add_manual_ref(
    frame: Any,
    *,
    data_dir: Path,
    kind: str,
    description: str,
    content: bytes,
    suffix: str,
) -> dict[str, Any]:
    """Сохранить файл рефа и приписать его кадру. Описание обязательно."""
    text = (description or "").strip()
    if not text:
        raise ValueError("нужно описание рефа: без него генератор не знает, что брать")
    if not content:
        raise ValueError("пустой файл рефа")
    ext = (suffix or "").lower()
    if ext not in _IMG_SUFFIXES:
        raise ValueError(f"реф должен быть картинкой {sorted(_IMG_SUFFIXES)}, а не {ext!r}")

    kind_n = normalize_ref_kind(kind)
    ref_id = uuid.uuid4().hex[:8]
    target_dir = refs_dir(data_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    number = int(getattr(frame, "number", 0) or 0)
    path = target_dir / f"frame_{number:03d}_{kind_n}_{ref_id}{ext}"
    path.write_bytes(content)

    row = {
        "id": ref_id,
        "kind": kind_n,
        "описание": text,
        "file": _relative_file(data_dir, path),
    }
    attrs = dict(getattr(frame, "attrs", None) or {})
    rows = [r for r in (attrs.get(_ATTR_KEY) or []) if isinstance(r, dict)]
    rows.append(row)
    attrs[_ATTR_KEY] = rows
    frame.attrs = attrs
    logger.info("montage refs: кадр {} + {} «{}»", number, kind_n, text[:60])
    return row


def delete_manual_ref(frame: Any, *, data_dir: Path, ref_id: str) -> bool:
    """Убрать реф кадра вместе с файлом."""
    wanted = (ref_id or "").strip()
    if not wanted:
        return False
    attrs = dict(getattr(frame, "attrs", None) or {})
    rows = [r for r in (attrs.get(_ATTR_KEY) or []) if isinstance(r, dict)]
    keep = [r for r in rows if str(r.get("id") or "") != wanted]
    if len(keep) == len(rows):
        return False
    for row in rows:
        if str(row.get("id") or "") != wanted:
            continue
        path = _ref_path(data_dir, row)
        if path is not None and path.is_file():
            try:
                path.unlink()
            except OSError as e:  # noqa: PERF203 — удаление не должно ронять запрос
                logger.warning("montage refs: не удалил {}: {}", path, e)
    attrs[_ATTR_KEY] = keep
    frame.attrs = attrs
    logger.info(
        "montage refs: кадр {} − реф {}", int(getattr(frame, "number", 0) or 0), wanted
    )
    return True


def _relative_file(data_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(data_dir))
    except ValueError:
        return str(path)


def _ref_path(data_dir: Path, row: dict[str, Any]) -> Path | None:
    raw = str(row.get("file") or "").strip()
    if not raw:
        return None
    # Путь пришёл из БД проекта: не даём вылезти за data_dir.
    if re.match(r"^[a-zA-Z]:[\\/]|^/", raw):
        path = Path(raw)
        try:
            path.relative_to(data_dir)
        except ValueError:
            logger.warning("montage refs: путь вне проекта, пропускаю: {}", raw)
            return None
        return path
    return data_dir / raw
