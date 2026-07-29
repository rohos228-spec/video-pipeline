"""Сбор багрепорта: описание + хвост логов → файл в ``баги/``."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.project_root import find_project_root
from app.settings import settings

VALID_WINDOWS_MIN = frozenset({1, 5, 30, 60})

# loguru / типичный префикс: 2026-07-29 06:22:01.123 | INFO | ...
_TS_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
)


def bugs_dir() -> Path:
    root = find_project_root()
    d = root / "баги"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _list_log_paths() -> list[Path]:
    data = settings.data_dir.resolve()
    found: list[Path] = []
    for name in ("studio-live.log", "backend.log", "doctor.log"):
        p = data / name
        if p.is_file():
            found.append(p)
    found.extend(sorted(data.glob("backend-*.log"), key=lambda p: p.stat().st_mtime, reverse=True)[:3])
    logs_dir = Path("logs")
    if not logs_dir.is_absolute():
        logs_dir = find_project_root() / "logs"
    if logs_dir.is_dir():
        for name in ("doctor.log", "studio.log"):
            p = logs_dir / name
            if p.is_file() and p not in found:
                found.append(p)
    # unique by resolve
    out: list[Path] = []
    seen: set[str] = set()
    for p in found:
        key = str(p.resolve())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _parse_line_ts(line: str) -> datetime | None:
    m = _TS_RE.match(line.strip())
    if not m:
        return None
    raw = m.group(1).replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _tail_bytes(path: Path, max_bytes: int = 2_000_000) -> str:
    try:
        size = path.stat().st_size
    except OSError:
        return ""
    with open(path, "rb") as fh:
        if size > max_bytes:
            fh.seek(size - max_bytes)
            fh.readline()  # drop partial first line
        data = fh.read()
    return data.decode("utf-8", errors="replace")


def filter_log_window(text: str, *, minutes: int, now: datetime | None = None) -> str:
    """Оставить строки за последние ``minutes`` минут (по timestamp в строке).

    Если timestamps нет — вернуть хвост целиком (уже обрезанный при чтении).
    """
    if minutes not in VALID_WINDOWS_MIN:
        minutes = 5
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(minutes=minutes)
    lines = text.splitlines()
    kept: list[str] = []
    any_ts = False
    for line in lines:
        ts = _parse_line_ts(line)
        if ts is None:
            # строки без ts — держим, если уже вошли в окно
            if kept:
                kept.append(line)
            continue
        any_ts = True
        # naive timestamps из логов считаем локальными ≈ utc для фильтра
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= since:
            kept.append(line)
    if any_ts:
        return "\n".join(kept)
    # нет timestamp — отдаём последние ~400 строк
    return "\n".join(lines[-400:])


def collect_log_snippets(*, minutes: int) -> list[dict[str, Any]]:
    snippets: list[dict[str, Any]] = []
    for path in _list_log_paths():
        raw = _tail_bytes(path)
        filtered = filter_log_window(raw, minutes=minutes)
        snippets.append(
            {
                "name": path.name,
                "path": str(path),
                "chars": len(filtered),
                "text": filtered,
            }
        )
    return snippets


def build_clipboard_prompt(*, rel_path: str, description: str) -> str:
    return (
        "Почини баг по отчёту из video-pipeline.\n"
        f"Файл: {rel_path}\n\n"
        f"Кратко от пользователя:\n{description.strip()[:1500]}\n\n"
        "Прочитай отчёт целиком (описание + логи), найди корневую причину и исправь в main."
    )


def write_bug_report(
    *,
    description: str,
    minutes: int,
    project_id: int | None = None,
    project_slug: str | None = None,
    studio_version: str | None = None,
) -> dict[str, Any]:
    minutes = int(minutes) if int(minutes) in VALID_WINDOWS_MIN else 5
    desc = (description or "").strip()
    if not desc:
        raise ValueError("нужно описание проблемы")

    now_local = datetime.now().astimezone()
    stamp = now_local.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = bugs_dir()
    filename = f"{stamp}.md"
    path = out_dir / filename
    # коллизия в ту же секунду
    n = 1
    while path.exists():
        filename = f"{stamp}_{n}.md"
        path = out_dir / filename
        n += 1

    snippets = collect_log_snippets(minutes=minutes)
    rel = f"баги/{filename}"

    parts: list[str] = [
        f"# Багрепорт {stamp}",
        "",
        f"- создан: {now_local.isoformat(timespec='seconds')}",
        f"- окно логов: {minutes} мин",
    ]
    if project_id is not None:
        parts.append(f"- project_id: {project_id}")
    if project_slug:
        parts.append(f"- project_slug: {project_slug}")
    if studio_version:
        parts.append(f"- studio: {studio_version}")
    parts.extend(["", "## Описание", "", desc, "", "## Логи", ""])

    for sn in snippets:
        parts.append(f"### {sn['name']}")
        parts.append(f"_{sn['path']} · {sn['chars']} символов_")
        parts.append("")
        parts.append("```")
        parts.append(sn["text"] or "(пусто)")
        parts.append("```")
        parts.append("")

    path.write_text("\n".join(parts), encoding="utf-8")
    clipboard = build_clipboard_prompt(rel_path=rel, description=desc)
    return {
        "ok": True,
        "path": str(path),
        "rel": rel,
        "filename": filename,
        "minutes": minutes,
        "logFiles": [s["name"] for s in snippets],
        "clipboardPrompt": clipboard,
    }
