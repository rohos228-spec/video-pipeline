"""Референсы кадра, которые оператор приложил прямо на доске монтажа.

Автоматические рефы (персонажи ячейки, предметы, still VO-родителя) приходят из
листов и папок проекта — их считает `montage_board._group_refs_for_frames`.
Здесь — рефы, приложенные руками, двумя путями:

* **приложить готовый** — файл персонажа / предмета, который уже есть в проекте
  (`characters/`, `items/`): ссылаемся на него, файл не копируем и при
  отвязке не удаляем;
* **загрузить новый** — файл + **имя**, которое дальше видно на доске.

Хранение: запись в ``Frame.attrs["ref_manual"]`` (SoT — БД проекта), новый файл
кладём в ``data/<project>/refs/``.
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

_ATTR_KEY = "ref_manual"
_IMG_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
#: Где искать уже готовые рефы проекта: папка → вид рефа.
_ASSET_DIRS: dict[str, str] = {"characters": "character", "items": "item"}


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


def ref_name(row: dict[str, Any]) -> str:
    """Имя рефа для доски и промта."""
    for key in ("имя", "name", "описание", "description"):
        text = str(row.get(key) or "").strip()
        if text:
            return text
    return ""


def manual_ref_paths(data_dir: Path, frame: Any) -> list[Path]:
    """Файлы рефов кадра для генерации — в порядке добавления."""
    out: list[Path] = []
    for row in manual_refs(frame):
        path = _ref_path(data_dir, row)
        if path is not None and path.is_file():
            out.append(path)
    return out


def manual_refs_for_board(data_dir: Path, frame: Any) -> list[dict[str, Any]]:
    """DTO для доски: вид, имя и превью-URL."""
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
                "name": ref_name(row),
                "linked": bool(row.get("linked")),
                "image_url": _preview_url(path) if path is not None else None,
            }
        )
    return out


def list_ref_assets(
    data_dir: Path,
    *,
    names: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Готовые рефы проекта: что лежит в ``characters/`` и ``items/``.

    ``names`` — карта вид → {код: имя} из `Entity`, чтобы на доске был не «c02»,
    а имя персонажа.
    """
    from app.services.montage_board import _preview_url

    out: list[dict[str, Any]] = []
    for folder, kind in _ASSET_DIRS.items():
        directory = data_dir / folder
        if not directory.is_dir():
            continue
        by_kind = (names or {}).get(kind) or {}
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix.lower() not in _IMG_SUFFIXES:
                continue
            code = path.stem.split("_", 1)[0].strip().lower()
            out.append(
                {
                    "kind": kind,
                    "kind_label": REF_KINDS[kind],
                    "code": code,
                    "name": by_kind.get(code) or path.stem,
                    "file": _relative_file(data_dir, path),
                    "image_url": _preview_url(path),
                }
            )
    return out


def manual_ref_prompt_note(frame: Any) -> str:
    """Имена ручных рефов для промта: генератор должен знать, что это за файлы."""
    parts: list[str] = []
    for row in manual_refs(frame):
        name = ref_name(row)
        if not name:
            continue
        kind = normalize_ref_kind(str(row.get("kind") or ""))
        parts.append(f"{REF_KINDS[kind]} — {name}")
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
    name: str,
    content: bytes,
    suffix: str,
) -> dict[str, Any]:
    """Загрузить новый реф кадру: файл + имя, которое видно на доске."""
    text = (name or "").strip()
    if not text:
        raise ValueError("нужно имя рефа: под ним он будет виден на доске")
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

    return _append_row(
        frame,
        {
            "id": ref_id,
            "kind": kind_n,
            "имя": text,
            "file": _relative_file(data_dir, path),
        },
    )


def link_ref_asset(
    frame: Any,
    *,
    data_dir: Path,
    file: str,
    kind: str = "",
    name: str = "",
) -> dict[str, Any]:
    """Приложить кадру готовый реф проекта — файл остаётся на месте."""
    assets = {a["file"]: a for a in list_ref_assets(data_dir)}
    asset = assets.get((file or "").strip())
    if asset is None:
        raise ValueError(f"нет такого рефа в проекте: {file!r}")
    kind_n = normalize_ref_kind(kind or asset["kind"])
    text = (name or "").strip() or str(asset["name"])
    return _append_row(
        frame,
        {
            "id": uuid.uuid4().hex[:8],
            "kind": kind_n,
            "имя": text,
            "file": asset["file"],
            "linked": True,
        },
    )


def delete_manual_ref(frame: Any, *, data_dir: Path, ref_id: str) -> bool:
    """Убрать реф кадра. Загруженный файл удаляем, готовый ассет — нет."""
    wanted = (ref_id or "").strip()
    if not wanted:
        return False
    attrs = dict(getattr(frame, "attrs", None) or {})
    rows = [r for r in (attrs.get(_ATTR_KEY) or []) if isinstance(r, dict)]
    keep = [r for r in rows if str(r.get("id") or "") != wanted]
    if len(keep) == len(rows):
        return False
    for row in rows:
        if str(row.get("id") or "") != wanted or row.get("linked"):
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


def _append_row(frame: Any, row: dict[str, Any]) -> dict[str, Any]:
    attrs = dict(getattr(frame, "attrs", None) or {})
    rows = [r for r in (attrs.get(_ATTR_KEY) or []) if isinstance(r, dict)]
    rows.append(row)
    attrs[_ATTR_KEY] = rows
    frame.attrs = attrs
    logger.info(
        "montage refs: кадр {} + {} «{}»",
        int(getattr(frame, "number", 0) or 0),
        row.get("kind"),
        str(row.get("имя") or "")[:60],
    )
    return row


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
