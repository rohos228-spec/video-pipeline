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

# Vision ≠ исходные байты. Возврат файлов делает Studio из attachments/, не модель.
_WORKSPACE_SYSTEM = (
    "Ты в Studio GPT (HTTP API).\n"
    "Изображения приходят как vision (input_image) — ты их ВИДИШЬ и можешь анализировать, "
    "но у тебя НЕТ исходных байтов PNG/Base64 для дословного возврата.\n"
    "Возврат исходных вложений делает Studio сама (кнопка ↓ / «Результаты»), "
    "не проси реконструировать файл и НЕ пиши, что «интерфейс не умеет» / "
    "«нет доступа к байтам».\n"
    "Если просят анализ — анализируй. Если просят вернуть файл — коротко: "
    "«Studio положила исходник в Результаты» (файлы уже прикреплены приложением).\n"
    "Новые таблицы можно отдать TSV с «# Лист: …». Новые картинки — только если "
    "ты их реально сгенерировал (data:image/...;base64,...), не «восстанавливай» "
    "исходник пользователя."
)

_RETURN_FILE_RE = re.compile(
    r"(?i)\b("
    r"верн[иу]|пришл[иу]|отправ[ьи]|скача[йть]|download|send\s+back|"
    r"return\s+(the\s+)?file|дай\s+файл|выгруз|исходник"
    r")\b"
)

_ANALYZE_RE = re.compile(
    r"(?i)("
    r"анализ|опис|что\s+(на|в)\s+|расскаж|проверь|сравн|"
    r"describe|analy[sz]e|compare|explain|что\s+это|кто\s+это|"
    r"перепиши|исправ|сгенерир|сделай\s+(новую|другую)|измени"
    r")"
)


def _wants_file_return(message: str) -> bool:
    return bool(_RETURN_FILE_RE.search(message or ""))


def _wants_analysis(message: str) -> bool:
    return bool(_ANALYZE_RE.search(message or ""))


def _pure_file_return(message: str) -> bool:
    """Только вернуть файл(ы), без анализа/генерации — GPT не нужен."""
    text = (message or "").strip()
    if not text or not _wants_file_return(text):
        return False
    if _wants_analysis(text):
        return False
    # короткое «верни sand.png» / «пришли файл обратно»
    return len(text) < 240


def _file_urls(path: Path) -> dict[str, str]:
    """URL просмотра и принудительного скачивания через /api/files."""
    q = quote(str(path), safe="")
    return {
        "url": f"/api/files?path={q}",
        "download_url": f"/api/files?path={q}&download=1",
    }


def _file_entry(path: Path) -> dict[str, Any]:
    """Карточка файла: sniff-rename на диске + mime/kind для превью в GPT-окне."""
    from app.services.gpt_api import ensure_correct_extension, suggested_name_and_mime

    fixed = ensure_correct_extension(path)
    display_name, mime = suggested_name_and_mime(fixed)
    kind = "image" if (mime or "").startswith("image/") else "file"
    if kind != "image" and fixed.suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".gif",
        ".bmp",
        ".svg",
        ".avif",
        ".heic",
        ".ico",
    }:
        kind = "image"
    return {
        "name": fixed.name,
        "display_name": display_name or fixed.name,
        "size": fixed.stat().st_size if fixed.is_file() else 0,
        "path": str(fixed),
        "mime": mime,
        "kind": kind,
        **_file_urls(fixed),
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
    # Лениво: .bin на диске → png/jpg по magic (в т.ч. старые сессии)
    renames = _normalize_session_files(d)
    meta = _read_json(d / "meta.json", {})
    messages = _read_json(d / "messages.json", [])
    if renames and isinstance(messages, list):
        messages = _rewrite_message_filenames(messages, renames)
        _write_json(d / "messages.json", messages)
    attachments = []
    att_dir = d / "attachments"
    if att_dir.is_dir():
        for p in sorted(att_dir.iterdir()):
            if p.is_file():
                attachments.append(_file_entry(p))
    outputs = []
    out_dir = d / "outputs"
    if out_dir.is_dir():
        for p in sorted(out_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
            if p.is_file():
                outputs.append(_file_entry(p))
    return {
        "id": d.name,
        "title": meta.get("title") or d.name,
        "created_at": meta.get("created_at"),
        "updated_at": meta.get("updated_at"),
        "status": meta.get("status") or "idle",
        "phase": meta.get("phase") or "",
        "phase_detail": meta.get("phase_detail") or "",
        "messages": messages if isinstance(messages, list) else [],
        "attachments": attachments,
        "outputs": outputs,
    }


def _normalize_session_files(d: Path) -> dict[str, str]:
    """Переименовать .bin/слабые расширения по magic/CT. {old_name: new_name}."""
    from app.services.gpt_api import ensure_correct_extension, finalize_downloaded_file

    renames: dict[str, str] = {}
    for sub in ("attachments", "outputs"):
        folder = d / sub
        if not folder.is_dir():
            continue
        for p in list(folder.iterdir()):
            if not p.is_file():
                continue
            old = p.name
            if p.suffix.lower() in {".bin", ".download", ".octet-stream", ".dat", ".tmp"}:
                fixed = finalize_downloaded_file(p)
            else:
                fixed = ensure_correct_extension(p)
            if fixed.name != old:
                renames[old] = fixed.name
    return renames


def _rewrite_message_filenames(
    messages: list[Any], renames: dict[str, str]
) -> list[Any]:
    out: list[Any] = []
    for m in messages:
        if not isinstance(m, dict):
            out.append(m)
            continue
        nm = dict(m)
        for key in ("attachment_names", "output_files"):
            names = nm.get(key)
            if isinstance(names, list):
                nm[key] = [renames.get(str(n), n) for n in names]
        content = nm.get("content")
        if isinstance(content, str) and content:
            for old, new in renames.items():
                if old and new and old != new and old in content:
                    content = content.replace(old, new)
            nm["content"] = content
        out.append(nm)
    return out


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
    from app.services.gpt_api import resolve_bytes_extension, sniff_file_extension

    d = _session_dir(session_id)
    if not d.is_dir() or not (d / "meta.json").is_file():
        raise FileNotFoundError(f"сессия не найдена: {session_id}")
    if not data:
        raise ValueError("пустой файл")
    att = d / "attachments"
    att.mkdir(exist_ok=True)
    safe = _safe_filename(filename)
    # Никогда не оставляем .bin — magic / fallback .dat
    sniffed = sniff_file_extension(data) or resolve_bytes_extension(data, fallback=".dat")
    suf = Path(safe).suffix.lower()
    if suf in {"", ".bin", ".dat", ".tmp", ".octet-stream", ".download"} or (
        sniffed.startswith(".")
        and sniffed in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".avif"}
        and suf not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".avif"}
    ):
        safe = f"{Path(safe).stem}{sniffed}"
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
    return _file_entry(path)


def delete_attachment(session_id: str, name: str) -> None:
    d = _session_dir(session_id)
    if not d.is_dir() or not (d / "meta.json").is_file():
        raise FileNotFoundError(f"сессия не найдена: {session_id}")
    path = d / "attachments" / Path(name).name
    if path.exists():
        path.unlink()


def copy_attachment_to_outputs(session_id: str, name: str) -> dict[str, Any]:
    """Скопировать вложение в outputs/ — «вернуть файл» без генерации моделью."""
    from app.services.gpt_api import ensure_correct_extension

    d = _session_dir(session_id)
    if not d.is_dir() or not (d / "meta.json").is_file():
        raise FileNotFoundError(f"сессия не найдена: {session_id}")
    src = d / "attachments" / Path(name).name
    if not src.is_file():
        raise FileNotFoundError(f"нет вложения {name}")
    out_dir = d / "outputs"
    out_dir.mkdir(exist_ok=True)
    # Имя с корректным расширением (если исходник .bin, а внутри PNG)
    fixed_src = ensure_correct_extension(src)
    safe = _safe_filename(fixed_src.name)
    dest = out_dir / safe
    if dest.exists():
        stem, suf = Path(safe).stem, Path(safe).suffix
        i = 2
        while True:
            cand = out_dir / f"{stem}_out{i}{suf}"
            if not cand.exists():
                dest = cand
                break
            i += 1
    shutil.copy2(fixed_src, dest)
    dest = ensure_correct_extension(dest)
    meta = _read_json(d / "meta.json", {})
    meta["updated_at"] = _now()
    _write_json(d / "meta.json", meta)
    return _file_entry(dest)


def build_outputs_zip(session_id: str) -> Path:
    """Собрать zip всех outputs сессии (скачать всё, как из ChatGPT)."""
    import zipfile

    d = _session_dir(session_id)
    if not d.is_dir() or not (d / "meta.json").is_file():
        raise FileNotFoundError(f"сессия не найдена: {session_id}")
    out_dir = d / "outputs"
    files = [p for p in out_dir.iterdir() if p.is_file()] if out_dir.is_dir() else []
    if not files:
        raise FileNotFoundError("нет файлов в Результатах")
    zip_path = d / f"outputs_{session_id}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, arcname=p.name)
    return zip_path


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


def _promote_attachments(session_id: str, files: list[Path]) -> list[str]:
    """Скопировать исходные байты вложений в outputs/ (возврат без модели)."""
    names: list[str] = []
    for src in files:
        try:
            dest_info = copy_attachment_to_outputs(session_id, src.name)
            if dest_info["name"] not in names:
                names.append(dest_info["name"])
        except Exception as e:  # noqa: BLE001
            logger.warning("gpt_workspace: promote {}: {}", src.name, e)
    return names


def _studio_return_reply(returned: list[str]) -> str:
    if not returned:
        return (
            "Нет вложений в этой сессии. Прикрепи файл(ы) скрепкой, "
            "затем снова попроси вернуть."
        )
    lines = [
        "Studio вернула исходные файлы из хранилища сессии "
        "(байты с диска, без участия модели / vision).",
        "",
        "Скачай в блоке «Результаты» или ↓ ниже:",
    ]
    for n in returned:
        lines.append(f"• {n}")
    return "\n".join(lines)


async def ask(
    session_id: str,
    message: str,
    *,
    with_attachments: bool = True,
) -> dict[str, Any]:
    """Отправить сообщение в GPT API, сохранить ответ и файлы в outputs/.

    Память диалога: прошлые user/assistant реплики сессии уходят в API как history.

    Возврат исходников: Studio копирует attachments/ → outputs/ сама.
    Vision даёт модели только визуальные токены, не байты файла.
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
    meta["phase"] = "accepted"
    meta["phase_detail"] = "Запрос принят"
    meta["updated_at"] = _now()
    _write_json(d / "meta.json", meta)

    _append_message(
        session_id,
        "user",
        text,
        attachment_names=[p.name for p in files],
    )

    out_dir = d / "outputs"
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    saved_files: list[str] = []

    # ─── возврат исходников: байты с диска, НЕ реконструкция моделью ───
    want_return = bool(files) and _wants_file_return(text)
    if want_return:
        saved_files.extend(_promote_attachments(session_id, files))

    try:
        # Чистый «верни файл» — GPT не вызываем (как карточка файла в веб-ChatGPT).
        if want_return and _pure_file_return(text):
            reply = _studio_return_reply(saved_files)
            reply_path = out_dir / f"reply_{ts}.txt"
            reply_path.write_text(reply, encoding="utf-8")
            _append_message(
                session_id,
                "assistant",
                reply,
                output_files=list(saved_files),
                studio_returned=True,
            )
            meta = _read_json(d / "meta.json", {})
            meta["status"] = "idle"
            meta["phase"] = "done"
            meta["phase_detail"] = "Готово (возврат файлов)"
            meta["updated_at"] = _now()
            _write_json(d / "meta.json", meta)
            logger.info(
                "gpt_workspace: session={} studio_return files={}",
                session_id,
                saved_files,
            )
            return get_session(session_id)

        gpt = get_gpt_client()
        has_xlsx = any(p.suffix.lower() in {".xlsx", ".xlsm"} for p in files)
        ask_text = text
        if want_return and saved_files:
            ask_text = (
                f"{text}\n\n"
                f"[Studio уже положила исходники в Результаты: "
                f"{', '.join(saved_files)}. Не реконструируй байты.]"
            )

        meta = _read_json(d / "meta.json", {})
        meta["phase"] = "thinking"
        meta["phase_detail"] = "GPT думает / генерирует ответ (vision может занять минуты)…"
        meta["updated_at"] = _now()
        _write_json(d / "meta.json", meta)

        reply = await gpt.ask_with_files(
            ask_text,
            files,
            timeout=float(settings.gpt_timeout_s or 600),
            expect_file_download=has_xlsx,
            history=history,
            treat_txt_as_prompt=False,
            system=_WORKSPACE_SYSTEM,
        )
        reply = (reply or "").strip()

        meta = _read_json(d / "meta.json", {})
        meta["phase"] = "saving"
        meta["phase_detail"] = "Сохраняю ответ и файлы…"
        meta["updated_at"] = _now()
        _write_json(d / "meta.json", meta)

        # Текст ответа на диск (для zip), в пузыре не дублируем reply_*.txt
        reply_path = out_dir / f"reply_{ts}.txt"
        reply_path.write_text(reply, encoding="utf-8")

        # URL / data-URI из ответа → только НОВЫЕ артефакты модели
        try:
            from app.services.gpt_api import ensure_correct_extension, materialize_reply_assets

            assets = await materialize_reply_assets(
                reply,
                out_dir,
                prefix=f"gpt_{ts}",
                timeout=90.0,
            )
            for p in assets:
                p = ensure_correct_extension(p)
                if p.is_file() and p.name not in saved_files:
                    saved_files.append(p.name)
            # Добить любые .bin/.download в outputs этой сессии
            for p in list(out_dir.iterdir()):
                if p.is_file() and p.suffix.lower() in {
                    ".bin",
                    ".dat",
                    ".download",
                    ".octet-stream",
                }:
                    from app.services.gpt_api import finalize_downloaded_file

                    fixed = finalize_downloaded_file(p)
                    if fixed.name not in saved_files and fixed.suffix.lower() not in {
                        ".bin",
                        ".download",
                    }:
                        if fixed.name not in saved_files:
                            saved_files.append(fixed.name)
        except Exception as e:  # noqa: BLE001
            logger.warning("gpt_workspace: materialize assets: {}", e)

        looks_like_xlsx = has_xlsx or ("# Лист:" in reply) or ("# Лист：" in reply)
        if looks_like_xlsx:
            try:
                xlsx_path = out_dir / f"result_{ts}.xlsx"
                await gpt.download_attachment_from_last_reply(
                    xlsx_path,
                    timeout=120 if has_xlsx else 60,
                    fallback_text=reply,
                    allow_reply_text_fallback=False,
                )
                if xlsx_path.exists() and xlsx_path.stat().st_size > 64:
                    if xlsx_path.name not in saved_files:
                        saved_files.append(xlsx_path.name)
                elif xlsx_path.exists():
                    try:
                        xlsx_path.unlink()
                    except OSError:
                        pass
            except Exception:  # noqa: BLE001
                pass

        if want_return and saved_files:
            reply = (
                f"{reply.rstrip()}\n\n"
                f"—\n"
                f"Исходники (Studio, не модель): {', '.join(saved_files)}"
            )

        _append_message(
            session_id,
            "assistant",
            reply,
            output_files=list(saved_files),
            studio_returned=want_return,
        )
        meta = _read_json(d / "meta.json", {})
        meta["status"] = "idle"
        meta["phase"] = "done"
        meta["phase_detail"] = "Готово"
        meta["updated_at"] = _now()
        _write_json(d / "meta.json", meta)
        logger.info(
            "gpt_workspace: session={} reply_len={} files={} history={} returned={}",
            session_id,
            len(reply),
            saved_files,
            len(history),
            want_return,
        )
        return get_session(session_id)
    except Exception as e:
        meta = _read_json(d / "meta.json", {})
        meta["status"] = "error"
        meta["phase"] = "error"
        meta["phase_detail"] = str(e)[:200]
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
