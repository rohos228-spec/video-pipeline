"""Файлы, прикреплённые к командной строке слоя действий.

Файл загружается отдельной ручкой (POST /attachments) и лежит в DATA_DIR/attachments/<проект>/
<uuid>_<безопасное имя>. Планировщик (модель) видит только {id, name, kind, size}: путь никогда не
попадает в промпт и не берётся из ответа модели. Сервер сам подставляет путь в шаг загрузки по
id вложения (``planner_uploads``) после проверки: вид файла подходит действию, вложение принадлежит
этому проекту и ещё не использовано в другом плане.

Вложение «использовано» (consumed), когда по нему создан план: так один файл не уходит в два
плана. Неиспользованные вложения старше срока хранения удаляются (``cleanup_unused``).
Таблица создаётся миграцией 0006 (schema_migrations).
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, delete, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base
from app.services.action_layer import store
from app.services.action_layer.context import _magic_ok

MIB = 1024 * 1024
CHUNK = MIB
MAX_PER_REQUEST = 5          # вложений в одном запросе к планировщику
MAX_NAME_LEN = 100
HEADROOM = 256 * 1024        # служебные байты multipart сверх файла
ATTACH_DIR = "attachments"
_TMP_MAX_AGE_S = 3600
_CLEANUP_EVERY_S = 3600


class AttachmentError(Exception):
    """Отказ с кодом и русским текстом; ``status`` - код HTTP для ручек."""

    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.status = code, status
        super().__init__(message)


# --- виды файлов ------------------------------------------------------------------------------

@dataclass(frozen=True)
class KindRule:
    kind: str
    extensions: tuple[str, ...]
    max_bytes: int
    mimes: frozenset
    title_ru: str


KINDS: dict[str, KindRule] = {r.kind: r for r in (
    KindRule("image", (".png", ".jpg", ".jpeg", ".webp"), 30 * MIB,
             frozenset({"image/png", "image/jpeg", "image/pjpeg", "image/webp"}), "картинка"),
    KindRule("video", (".mp4", ".mov", ".webm"), 300 * MIB,
             frozenset({"video/mp4", "video/quicktime", "video/webm"}), "видео"),
    KindRule("audio", (".mp3", ".wav", ".m4a", ".ogg"), 100 * MIB,
             frozenset({"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/wave",
                        "audio/vnd.wave", "audio/mp4", "audio/x-m4a", "audio/m4a", "audio/ogg",
                        "application/ogg", "video/ogg"}), "аудио"),
    KindRule("excel", (".xlsx", ".xls"), 25 * MIB,
             frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "application/vnd.ms-excel"}), "таблица Excel"),
    KindRule("json", (".json",), 5 * MIB, frozenset({"application/json", "text/json"}), "JSON"),
    KindRule("text", (".txt", ".md"), 1 * MIB,
             frozenset({"text/plain", "text/markdown", "text/x-markdown"}), "текст (txt, md)"),
)}
EXT_KIND: dict[str, str] = {e: r.kind for r in KINDS.values() for e in r.extensions}
_GENERIC_MIMES = frozenset({"", "application/octet-stream", "binary/octet-stream"})

_SAFE_NAME = re.compile(r"[^0-9A-Za-zА-Яа-яЁё._ -]")
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                       *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))})
_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def max_bytes_for(kind: str) -> int:
    """Потолок размера: меньшее из лимита вида и общей настройки (МБ)."""
    from app.settings import settings
    return min(KINDS[kind].max_bytes, int(settings.action_layer_attachment_max_mb) * MIB)


def rules_view() -> dict:
    """Что принимает ручка загрузки (для кнопки выбора файла)."""
    return {"kinds": {k: {"title_ru": r.title_ru, "extensions": list(r.extensions),
                          "max_bytes": max_bytes_for(k)} for k, r in KINDS.items()}}


def max_request_bytes() -> int:
    return max(max_bytes_for(k) for k in KINDS) + HEADROOM


# --- таблица ------------------------------------------------------------------------------------

class ActionAttachment(Base):
    __tablename__ = "action_layer_attachments"
    __table_args__ = (Index("ix_action_layer_attachments_project", "project_id", "consumed_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer)
    operator: Mapped[str] = mapped_column(String(120), default="")
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12))
    ext: Mapped[str] = mapped_column(String(8))
    content_type: Mapped[str] = mapped_column(String(80), default="")
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    rel_path: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    consumed_plan_id: Mapped[str] = mapped_column(String(32), default="")


_TABLES = [ActionAttachment.__table__]


async def ensure_attachments_schema(conn) -> None:
    """Идемпотентно создаёт таблицу вложений (миграция 0006 и тестовые базы)."""
    await conn.run_sync(lambda sync_conn: Base.metadata.create_all(
        sync_conn, tables=_TABLES, checkfirst=True))


@dataclass(frozen=True)
class AttachmentRef:
    """Проверенное вложение для планировщика. Путь только серверный (относительно DATA_DIR)."""
    id: str
    project_id: int
    name: str
    kind: str
    ext: str
    size: int
    sha256: str
    rel_path: str

    def prompt_view(self) -> dict:
        """Единственное, что видит модель."""
        return {"id": self.id, "name": self.name, "kind": self.kind, "size": self.size}


def _ref(row: ActionAttachment) -> AttachmentRef:
    return AttachmentRef(row.id, row.project_id, row.name, row.kind, row.ext, int(row.size),
                         row.sha256, row.rel_path)


def synthetic_ref(project_id: int, kind: str, name: str, size: int = 1024, *,
                  att_id: str | None = None) -> AttachmentRef:
    """Вложение без файла (оценка планировщика и тесты): та же форма, что у настоящего."""
    clean = sanitize_name(name)
    ext = Path(clean).suffix.lower()
    aid = att_id or uuid.uuid4().hex
    return AttachmentRef(aid, project_id, clean, kind, ext, int(size), "0" * 64,
                         f"{ATTACH_DIR}/{project_id}/{aid}_{clean}")


# --- имена и пути -------------------------------------------------------------------------------

def sanitize_name(raw: Any) -> str:
    """Безопасное имя: без путей, управляющих символов, зарезервированных имён Windows."""
    text = unicodedata.normalize("NFC", str(raw or ""))
    if "\x00" in text:
        raise AttachmentError("bad_name", "В имени файла недопустимый символ.")
    text = text.replace("\\", "/").split("/")[-1]
    text = _SAFE_NAME.sub("_", text)
    text = re.sub(r"\.{2,}", ".", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        raise AttachmentError("bad_name", "Не удалось получить имя файла.")
    stem, dot, ext = text.rpartition(".")
    if len(text) > MAX_NAME_LEN:
        keep = MAX_NAME_LEN - (len(ext) + 1 if dot else 0)
        text = (stem[:keep].rstrip(" .") + "." + ext) if dot else text[:MAX_NAME_LEN]
    if text.split(".")[0].strip().upper() in _RESERVED:
        text = "_" + text
    return text


def attachments_root() -> Path:
    from app.settings import settings
    return Path(settings.data_dir).resolve() / ATTACH_DIR


def abs_path(rel_path: str) -> Path:
    """Абсолютный путь вложения; строго внутри DATA_DIR/attachments."""
    from app.settings import settings
    root = attachments_root()
    real = (Path(settings.data_dir).resolve() / rel_path).resolve()
    if root not in real.parents:
        raise AttachmentError("bad_path", "Путь вложения вне папки вложений.", 500)
    return real


def _mime_ok(kind: str, content_type: str) -> bool:
    base = (content_type or "").split(";")[0].strip().lower()
    return base in _GENERIC_MIMES or base in KINDS[kind].mimes


def _content_ok(ext: str, head: bytes, path: Path) -> bool:
    """Содержимое соответствует расширению (по сигнатуре; текст и JSON читаются целиком, они малы)."""
    if ext in (".txt", ".md", ".json"):
        data = path.read_bytes()
        if not _magic_ok(".txt", head, data):
            return False
        if ext == ".json":
            try:
                json.loads(data.decode("utf-8").lstrip("﻿"))
            except ValueError:
                return False
        return True
    if ext == ".mov":
        return head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip")
    if ext == ".webm":
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if ext == ".xls":
        return head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    return _magic_ok(ext, head, b"")


# --- загрузка ----------------------------------------------------------------------------------

async def save_upload(upload, *, project_id: int, operator: str) -> dict:
    """Сохраняет загруженный файл (потоком, с потолком) и пишет строку в таблицу.

    ``upload``: starlette UploadFile. Размер, расширение, MIME и сигнатура проверяются до записи
    строки; при любом отказе файл удаляется."""
    name = sanitize_name(getattr(upload, "filename", ""))
    ext = Path(name).suffix.lower()
    kind = EXT_KIND.get(ext)
    if kind is None:
        allowed = ", ".join(sorted(EXT_KIND))
        raise AttachmentError("type_not_allowed",
                              f"Тип файла «{ext or 'без расширения'}» не разрешён. Можно: {allowed}.", 415)
    content_type = (getattr(upload, "content_type", "") or "")[:80]
    if not _mime_ok(kind, content_type):
        raise AttachmentError("mime_not_allowed",
                              f"Тип содержимого «{content_type}» не подходит для файла {ext}.", 415)
    limit = max_bytes_for(kind)
    att_id = uuid.uuid4().hex
    folder = attachments_root() / str(int(project_id))
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".{att_id}.part"
    digest, size, head = hashlib.sha256(), 0, b""
    final: Path | None = None
    try:
        with tmp.open("wb") as fh:
            while True:
                chunk = await upload.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise AttachmentError(
                        "too_large", f"Файл больше лимита {limit // MIB} МБ для вида «{KINDS[kind].title_ru}».", 413)
                if len(head) < 16:
                    head = (head + chunk)[:16]
                digest.update(chunk)
                fh.write(chunk)
        if size == 0:
            raise AttachmentError("empty_file", "Файл пустой.")
        if not _content_ok(ext, head, tmp):
            raise AttachmentError("content_mismatch", f"Содержимое файла не похоже на {ext}.", 415)
        final = folder / f"{att_id}_{name}"
        tmp.replace(final)
        rel = f"{ATTACH_DIR}/{int(project_id)}/{final.name}"
        async with store.sessions()() as s:
            s.add(ActionAttachment(
                id=att_id, project_id=int(project_id), operator=operator[:120], name=name, kind=kind,
                ext=ext, content_type=content_type, size=size, sha256=digest.hexdigest(), rel_path=rel))
            await s.commit()
    except BaseException:
        for p in (tmp, final):
            if p is not None:
                p.unlink(missing_ok=True)
        raise
    return {"attachment_id": att_id, "name": name, "size": size, "kind": kind,
            "sha256": digest.hexdigest()}


# --- чтение, использование -------------------------------------------------------------------------

async def resolve(ids: list[str] | None, project_id: int | None) -> list[AttachmentRef]:
    """Вложения запроса к планировщику: существуют, этого проекта, не использованы, файл на месте."""
    ids = list(dict.fromkeys(ids or []))
    if not ids:
        return []
    if len(ids) > MAX_PER_REQUEST:
        raise AttachmentError("too_many_attachments", f"Можно прикрепить не больше {MAX_PER_REQUEST} файлов.")
    if project_id is None:
        raise AttachmentError("attachments_need_project", "Файлы прикрепляются к открытому проекту.")
    async with store.sessions()() as s:
        rows = {r.id: r for r in (await s.scalars(
            select(ActionAttachment).where(ActionAttachment.id.in_([i for i in ids if _ID_RE.match(i)])))).all()}
    out: list[AttachmentRef] = []
    for aid in ids:
        row = rows.get(aid)
        if row is None:
            raise AttachmentError("attachment_not_found", "Прикреплённый файл не найден. Прикрепите его заново.", 404)
        if row.project_id != project_id:
            raise AttachmentError("attachment_wrong_project",
                                  "Файл прикреплён к другому проекту. Прикрепите его в этом проекте.", 403)
        if row.consumed_at is not None:
            raise AttachmentError("attachment_consumed",
                                  f"Файл «{row.name}» уже использован в другом плане. Прикрепите его заново.", 409)
        try:
            exists = abs_path(row.rel_path).is_file()
        except AttachmentError:
            exists = False
        if not exists:
            raise AttachmentError("attachment_missing",
                                  f"Файл «{row.name}» удалён с диска. Прикрепите его заново.", 410)
        out.append(_ref(row))
    return out


async def claim(ids: list[str], project_id: int) -> None:
    """Атомарно помечает вложения использованными; если хоть одно уже занято - ничего не меняет."""
    if not ids:
        return
    async with store.sessions()() as s:
        res = await s.execute(update(ActionAttachment).where(
            ActionAttachment.id.in_(ids), ActionAttachment.project_id == project_id,
            ActionAttachment.consumed_at.is_(None)).values(consumed_at=datetime.utcnow()))
        if res.rowcount != len(ids):
            await s.rollback()
            raise AttachmentError("attachment_consumed",
                                  "Файл уже использован в другом плане. Прикрепите его заново.", 409)
        await s.commit()


async def release(ids: list[str]) -> None:
    """Снимает пометку, если план не создался (файл можно использовать снова)."""
    if not ids:
        return
    async with store.sessions()() as s:
        await s.execute(update(ActionAttachment).where(
            ActionAttachment.id.in_(ids), ActionAttachment.consumed_plan_id == "").values(consumed_at=None))
        await s.commit()


async def bind_plan(ids: list[str], plan_id: str) -> None:
    if not ids:
        return
    async with store.sessions()() as s:
        await s.execute(update(ActionAttachment).where(ActionAttachment.id.in_(ids)).values(
            consumed_plan_id=plan_id))
        await s.commit()


async def delete_unused(att_id: str, project_id: int | None = None) -> bool:
    """Убирает неиспользованное вложение (кнопка «убрать»). False, если такого нет."""
    async with store.sessions()() as s:
        row = await s.get(ActionAttachment, att_id) if _ID_RE.match(att_id or "") else None
        if row is None or (project_id is not None and row.project_id != project_id):
            return False
        if row.consumed_at is not None:
            raise AttachmentError("attachment_consumed", "Файл уже использован в плане, удалить его нельзя.", 409)
        path = row.rel_path
        await s.delete(row)
        await s.commit()
    try:
        abs_path(path).unlink(missing_ok=True)
    except AttachmentError:
        pass
    return True


# --- уборка ------------------------------------------------------------------------------------

_last_cleanup = 0.0


async def cleanup_unused(*, max_age_days: float | None = None, now: datetime | None = None) -> dict:
    """Удаляет неиспользованные вложения старше срока (настройка или ``max_age_days``) и их файлы.

    Использованные (по ним есть план) не трогаются. Заодно удаляются брошенные ``.part``-файлы.
    0 дней в настройке = не удалять. Возвращает {removed, bytes, orphans}."""
    from app.settings import settings
    days = float(settings.action_layer_attachment_retention_days if max_age_days is None else max_age_days)
    out = {"removed": 0, "bytes": 0, "orphans": 0}
    if days <= 0 and max_age_days is None:
        return out
    now = now or datetime.utcnow()
    cutoff = now - timedelta(days=days)
    async with store.sessions()() as s:
        rows = (await s.scalars(select(ActionAttachment).where(
            ActionAttachment.consumed_at.is_(None), ActionAttachment.created_at < cutoff))).all()
        for row in rows:
            try:
                path = abs_path(row.rel_path)
                if path.is_file():
                    out["bytes"] += path.stat().st_size
                    path.unlink()
            except (AttachmentError, OSError):
                pass
            await s.execute(delete(ActionAttachment).where(ActionAttachment.id == row.id))
            out["removed"] += 1
        await s.commit()
    root = attachments_root()
    if root.is_dir():
        for part in root.glob("*/.*.part"):
            try:
                if time.time() - part.stat().st_mtime > _TMP_MAX_AGE_S:
                    part.unlink()
                    out["orphans"] += 1
            except OSError:
                pass
    return out


async def cleanup_throttled() -> None:
    """Уборка не чаще раза в час (вызывается при загрузке); ошибки не мешают загрузке."""
    global _last_cleanup
    if time.monotonic() - _last_cleanup < _CLEANUP_EVERY_S and _last_cleanup:
        return
    _last_cleanup = time.monotonic()
    try:
        await cleanup_unused()
    except Exception:  # noqa: BLE001
        pass
