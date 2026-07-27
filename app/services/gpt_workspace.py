"""Studio GPT workspace — свободный чат через API (история, вложения, сохранение).

Файлы: data/gpt_workspace/<session_id>/
  meta.json, messages.json, attachments/, outputs/
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from loguru import logger

from app.settings import settings

_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._\-а-яА-ЯёЁ ]+")


def _file_urls(path: Path) -> dict[str, str]:
    """URL просмотра и принудительного скачивания через /api/files."""
    q = quote(str(path), safe="")
    return {
        "url": f"/api/files?path={q}",
        "download_url": f"/api/files?path={q}&download=1",
    }


def _root() -> Path:
    d = Path(settings.data_dir) / "gpt_workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_dir(session_id: str) -> Path:
    sid = "".join(c for c in session_id if c.isalnum() or c in "-_")
    if not sid:
        raise ValueError("пустой session_id")
    return _root() / sid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_sessions() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for d in sorted(_root().iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not d.is_dir():
            continue
        meta = _read_json(d / "meta.json", {})
        if not isinstance(meta, dict):
            continue
        msgs = _read_json(d / "messages.json", [])
        items.append(
            {
                "id": d.name,
                "title": str(meta.get("title") or d.name),
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "message_count": len(msgs) if isinstance(msgs, list) else 0,
                "status": meta.get("status") or "idle",
            }
        )
    return items


def create_session(*, title: str | None = None) -> dict[str, Any]:
    sid = uuid.uuid4().hex[:12]
    d = _session_dir(sid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "attachments").mkdir(exist_ok=True)
    (d / "outputs").mkdir(exist_ok=True)
    now = _now()
    meta = {
        "id": sid,
        "title": (title or "").strip() or f"Чат {now[:16].replace('T', ' ')}",
        "created_at": now,
        "updated_at": now,
        "status": "idle",
    }
    _write_json(d / "meta.json", meta)
    _write_json(d / "messages.json", [])
    return get_session(sid)


def get_session(session_id: str) -> dict[str, Any]:
    d = _session_dir(session_id)
    if not d.is_dir():
        raise FileNotFoundError(f"сессия не найдена: {session_id}")
    meta = _read_json(d / "meta.json", {})
    messages = _read_json(d / "messages.json", [])
    attachments = []
    att_dir = d / "attachments"
    if att_dir.is_dir():
        for p in sorted(att_dir.iterdir()):
            if p.is_file():
                attachments.append(
                    {
                        "name": p.name,
                        "size": p.stat().st_size,
                        "path": str(p),
                        **_file_urls(p),
                    }
                )
    outputs = []
    out_dir = d / "outputs"
    if out_dir.is_dir():
        for p in sorted(out_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file():
                outputs.append(
                    {
                        "name": p.name,
                        "size": p.stat().st_size,
                        "path": str(p),
                        **_file_urls(p),
                    }
                )
    return {
        "id": d.name,
        "title": meta.get("title") or d.name,
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "status": meta.get("status") or "idle",
        "messages": messages if isinstance(messages, list) else [],
        "attachments": attachments,
        "outputs": outputs,
    }


def delete_session(session_id: str) -> None:
    d = _session_dir(session_id)
    if d.is_dir():
        shutil.rmtree(d, ignore_errors=True)


def rename_session(session_id: str, title: str) -> dict[str, Any]:
    d = _session_dir(session_id)
    meta = _read_json(d / "meta.json", {})
    meta["title"] = (title or "").strip() or meta.get("title") or session_id
    meta["updated_at"] = _now()
    _write_json(d / "meta.json", meta)
    return get_session(session_id)


def _safe_filename(name: str) -> str:
    base = Path(name).name
    cleaned = _SAFE_NAME.sub("_", base).strip(" ._") or "file"
    return cleaned[:120]


def save_attachment(session_id: str, filename: str, data: bytes) -> dict[str, Any]:
    d = _session_dir(session_id)
    if not d.is_dir() or not (d / "meta.json").is_file():
        raise FileNotFoundError(f"сессия не найдена: {session_id}")
    if not data:
        raise ValueError("пустой файл")
    att = d / "attachments"
    att.mkdir(exist_ok=True)
    safe = _safe_filename(filename)
    path = att / safe
    # уникальность
    if path.exists():
        stem, suf = path.stem, path.suffix
        i = 2
        while True:
            cand = att / f"{stem}_{i}{suf}"
            if not cand.exists():
                path = cand
                break
            i += 1
    path.write_bytes(data)
    meta = _read_json(d / "meta.json", {})
    meta["updated_at"] = _now()
    _write_json(d / "meta.json", meta)
    return {
        "name": path.name,
        "size": path.stat().st_size,
        "path": str(path),
        **_file_urls(path),
    }


def delete_attachment(session_id: str, name: str) -> None:
    d = _session_dir(session_id)
    if not d.is_dir() or not (d / "meta.json").is_file():
        raise FileNotFoundError(f"сессия не найдена: {session_id}")
    path = d / "attachments" / Path(name).name
    if path.exists():
        path.unlink()


def _append_message(session_id: str, role: str, content: str, **extra: Any) -> None:
    d = _session_dir(session_id)
    msgs = _read_json(d / "messages.json", [])
    if not isinstance(msgs, list):
        msgs = []
    entry = {
        "id": uuid.uuid4().hex[:10],
        "role": role,
        "content": content,
        "at": _now(),
        **extra,
    }
    msgs.append(entry)
    _write_json(d / "messages.json", msgs)
    meta = _read_json(d / "meta.json", {})
    meta["updated_at"] = _now()
    # автозаголовок из первого юзер-сообщения
    if role == "user" and len(msgs) <= 2:
        title = (content or "").strip().replace("\n", " ")[:48]
        if title:
            meta["title"] = title
    _write_json(d / "meta.json", meta)


async def ask(
    session_id: str,
    message: str,
    *,
    with_attachments: bool = True,
) -> dict[str, Any]:
    """Отправить сообщение в GPT API, сохранить ответ и файлы в outputs/.

    Память диалога: прошлые user/assistant реплики сессии уходят в API как history.
    """
    from app.services.gpt_api import normalize_history
    from app.services.gpt_client import get_gpt_client

    d = _session_dir(session_id)
    if not d.is_dir():
        raise FileNotFoundError(f"сессия не найдена: {session_id}")

    text = (message or "").strip()
    if not text:
        raise ValueError("пустое сообщение")

    files: list[Path] = []
    if with_attachments:
        att = d / "attachments"
        if att.is_dir():
            files = [p for p in sorted(att.iterdir()) if p.is_file()]

    # История ДО текущего сообщения (UI хранит всё; в API — только user/assistant)
    prior_raw = _read_json(d / "messages.json", [])
    history = normalize_history(prior_raw if isinstance(prior_raw, list) else [])

    meta = _read_json(d / "meta.json", {})
    meta["status"] = "running"
    meta["updated_at"] = _now()
    _write_json(d / "meta.json", meta)

    _append_message(
        session_id,
        "user",
        text,
        attachment_names=[p.name for p in files],
    )

    try:
        gpt = get_gpt_client()
        has_xlsx = any(p.suffix.lower() in {".xlsx", ".xlsm"} for p in files)
        # Свободный чат: сообщение юзера = prompt, все файлы (в т.ч. .txt) = вложения.
        # expect_file_download только когда реально ждём xlsx-ответ.
        reply = await gpt.ask_with_files(
            text,
            files,
            timeout=float(settings.gpt_timeout_s or 600),
            expect_file_download=has_xlsx,
            history=history,
            treat_txt_as_prompt=False,
        )
        reply = (reply or "").strip()

        out_dir = d / "outputs"
        out_dir.mkdir(exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        saved_files: list[str] = []

        # Текст ответа всегда сохраняем
        reply_path = out_dir / f"reply_{ts}.txt"
        reply_path.write_text(reply, encoding="utf-8")
        saved_files.append(reply_path.name)

        # Попробовать materialize xlsx/доп. файлы из ответа
        if has_xlsx:
            try:
                xlsx_path = out_dir / f"result_{ts}.xlsx"
                await gpt.download_attachment_from_last_reply(
                    xlsx_path,
                    timeout=120,
                    fallback_text=reply,
                    allow_reply_text_fallback=False,
                )
                if xlsx_path.exists() and xlsx_path.stat().st_size > 64:
                    saved_files.append(xlsx_path.name)
            except Exception:  # noqa: BLE001
                # Не xlsx — ок, текст уже сохранён
                pass
        else:
            # Даже без входного xlsx модель могла вернуть таблицу — мягкая попытка
            try:
                xlsx_path = out_dir / f"result_{ts}.xlsx"
                await gpt.download_attachment_from_last_reply(
                    xlsx_path,
                    timeout=60,
                    fallback_text=reply,
                    allow_reply_text_fallback=False,
                )
                if xlsx_path.exists() and xlsx_path.stat().st_size > 64:
                    saved_files.append(xlsx_path.name)
            except Exception:  # noqa: BLE001
                pass

        _append_message(
            session_id,
            "assistant",
            reply,
            output_files=saved_files,
        )
        meta = _read_json(d / "meta.json", {})
        meta["status"] = "idle"
        meta["updated_at"] = _now()
        _write_json(d / "meta.json", meta)
        logger.info(
            "gpt_workspace: session={} reply_len={} files={} history={}",
            session_id,
            len(reply),
            saved_files,
            len(history),
        )
        return get_session(session_id)
    except Exception as e:
        meta = _read_json(d / "meta.json", {})
        meta["status"] = "error"
        meta["last_error"] = str(e)[:500]
        meta["updated_at"] = _now()
        _write_json(d / "meta.json", meta)
        _append_message(session_id, "system", f"Ошибка: {e}")
        raise


def save_output_to_project(
    session_id: str,
    *,
    output_name: str,
    project_data_dir: Path,
    as_name: str | None = None,
) -> dict[str, Any]:
    """Скопировать output в папку проекта (сохранение «как в оригинале»)."""
    src = _session_dir(session_id) / "outputs" / Path(output_name).name
    if not src.is_file():
        raise FileNotFoundError(f"нет файла {output_name}")
    project_data_dir.mkdir(parents=True, exist_ok=True)
    dest_name = _safe_filename(as_name or src.name)
    dest = project_data_dir / dest_name
    # project.xlsx — особый случай: бэкап
    if dest_name.lower() == "project.xlsx" and dest.exists():
        bak = project_data_dir / "old" / f"project_before_gpt_{uuid.uuid4().hex[:8]}.xlsx"
        bak.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dest, bak)
    shutil.copy2(src, dest)
    return {"saved_as": str(dest), "name": dest.name, "size": dest.stat().st_size}


def save_reply_as_voiceover(
    session_id: str,
    *,
    project_data_dir: Path,
    message_id: str | None = None,
) -> dict[str, Any]:
    """Сохранить текст ответа ассистента как voiceover.txt."""
    session = get_session(session_id)
    msgs = list(session.get("messages") or [])
    text = ""
    if message_id:
        for m in msgs:
            if m.get("id") == message_id and m.get("role") == "assistant":
                text = str(m.get("content") or "")
                break
    if not text:
        for m in reversed(msgs):
            if m.get("role") == "assistant":
                text = str(m.get("content") or "")
                break
    if not text.strip():
        raise ValueError("нет текста ответа для сохранения")
    project_data_dir.mkdir(parents=True, exist_ok=True)
    dest = project_data_dir / "voiceover.txt"
    dest.write_text(text.strip() + "\n", encoding="utf-8")
    return {"saved_as": str(dest), "name": dest.name, "chars": len(text.strip())}
