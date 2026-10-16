from __future__ import annotations

import base64
import binascii
import codecs
import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

API_PREFIX = "/api"
_BASE_URL = "http://action-layer.invalid"


class ActionCallError(RuntimeError):

    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(f"HTTP {status}: {detail}")


class WriteNotAllowed(RuntimeError):
    pass



MIB = 1024 * 1024
MAX_BASE64_BYTES = 512 * 1024
MAX_TOTAL_UPLOAD_BYTES = 512 * MIB


@dataclass(frozen=True)
class UploadPolicy:
    kind: str
    extensions: frozenset
    max_bytes: int
    content_types: dict


_AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4",
                ".ogg": "audio/ogg", ".flac": "audio/flac", ".aac": "audio/aac"}
_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp"}
_NODE_TYPES = {**_IMAGE_TYPES, ".xlsx": "application/vnd.openxmlformats-officedocument."
               "spreadsheetml.sheet", ".txt": "text/plain", ".md": "text/markdown",
               ".json": "application/json", ".csv": "text/csv"}

UPLOAD_POLICIES: dict[str, UploadPolicy] = {
    "voice": UploadPolicy("voice", frozenset(_AUDIO_TYPES), 100 * MIB, _AUDIO_TYPES),
    "music": UploadPolicy("music", frozenset(set(_AUDIO_TYPES) - {".aac"}), 100 * MIB,
                          {k: v for k, v in _AUDIO_TYPES.items() if k != ".aac"}),
    "scene_image": UploadPolicy("scene_image", frozenset({".png"}), 30 * MIB,
                                {".png": "image/png"}),
    "scene_video": UploadPolicy("scene_video", frozenset({".mp4"}), 300 * MIB,
                                {".mp4": "video/mp4"}),
    "ref_image": UploadPolicy("ref_image", frozenset(_IMAGE_TYPES), 30 * MIB, _IMAGE_TYPES),
    "node_file": UploadPolicy("node_file", frozenset(_NODE_TYPES), 25 * MIB, _NODE_TYPES),
    "check_agent": UploadPolicy("check_agent", frozenset({".txt", ".md"}), 1 * MIB,
                                {".txt": "text/plain", ".md": "text/markdown"}),
    "xlsx": UploadPolicy("xlsx", frozenset({".xlsx"}), 25 * MIB, {".xlsx": _NODE_TYPES[".xlsx"]}),
}

_TEXT_EXT = frozenset({".txt", ".md", ".json", ".csv"})
_SAFE_NAME = re.compile(r"[^0-9A-Za-zА-Яа-яЁё._ -]")


class UploadRejected(ValueError):
    pass


def _magic_ok(ext: str, head: bytes, content: bytes) -> bool:
    if ext == ".png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext == ".wav":
        return head[:4] == b"RIFF" and head[8:12] == b"WAVE"
    if ext == ".mp3":
        return head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF
                                           and head[1] & 0xE0 == 0xE0)
    if ext == ".ogg":
        return head.startswith(b"OggS")
    if ext == ".flac":
        return head.startswith(b"fLaC")
    if ext == ".aac":
        return len(head) > 1 and head[0] == 0xFF and head[1] & 0xF0 == 0xF0
    if ext in (".m4a", ".mp4"):
        return head[4:8] == b"ftyp"
    if ext == ".xlsx":
        return head.startswith(b"PK\x03\x04")
    if ext in (".txt", ".md", ".json", ".csv"):
        if b"\x00" in content[:4096]:
            return False
        try:
            content.decode("utf-8")
        except UnicodeDecodeError:
            return False
        return True
    return False


def _attachment_row(real: Path) -> tuple[bool, str, int]:
    from app.services.action_layer.attachments import ATTACH_DIR
    from app.settings import settings

    root = Path(settings.data_dir).resolve()
    att_root = root / ATTACH_DIR
    if att_root not in real.parents:
        return False, "", 0
    rel = real.relative_to(root).as_posix()
    try:
        uri = Path(settings.sqlite_path).resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            row = con.execute("SELECT sha256, size FROM action_layer_attachments WHERE rel_path = ?",
                              (rel,)).fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        row = None
    if not row:
        raise UploadRejected("вложение не найдено в реестре: загрузите файл заново через вложения")
    return True, str(row[0] or ""), int(row[1] or 0)


def verify_attachment_binding(real: Path, *, content: bytes | None = None, size: int | None = None) -> None:
    is_att, want_sha, want_size = _attachment_row(real)
    if not is_att:
        return
    if size is not None and want_size and size != want_size:
        raise UploadRejected("файл вложения изменён после загрузки (размер): загрузите его заново")
    if content is not None:
        if hashlib.sha256(content).hexdigest() != want_sha:
            raise UploadRejected("файл вложения изменён после загрузки (sha256): загрузите его заново")


@dataclass(frozen=True)
class FileUpload:
    form_field: str
    filename: str
    content: bytes = field(repr=False)
    content_type: str
    policy: str

    @staticmethod
    def _policy(kind: str) -> UploadPolicy:
        try:
            return UPLOAD_POLICIES[kind]
        except KeyError:
            raise UploadRejected(f"неизвестная политика загрузки {kind!r}") from None

    @classmethod
    def build(cls, kind: str, filename: str, content: bytes, *,
              field_name: str = "file") -> "FileUpload":
        policy = cls._policy(kind)
        base = Path(str(filename or "").replace("\\", "/")).name
        base = _SAFE_NAME.sub("_", base).strip(" .")[:100]
        ext = Path(base).suffix.lower()
        if not base or ext not in policy.extensions:
            raise UploadRejected(
                f"тип файла {ext or '(нет)'} не разрешён для {kind}: "
                f"{sorted(policy.extensions)}")
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise UploadRejected("пустой файл")
        if len(content) > policy.max_bytes:
            raise UploadRejected(f"файл {len(content)} байт больше лимита {policy.max_bytes}")
        if not _magic_ok(ext, bytes(content[:16]), bytes(content)):
            raise UploadRejected(f"содержимое не похоже на {ext}")
        return cls(field_name, base, bytes(content), policy.content_types[ext], kind)

    @classmethod
    def from_base64(cls, kind: str, filename: str, data_b64: str) -> "FileUpload":
        if not isinstance(data_b64, str) or len(data_b64) > MAX_BASE64_BYTES * 4 // 3 + 8:
            raise UploadRejected(f"base64 больше {MAX_BASE64_BYTES} байт после декодирования")
        try:
            raw = base64.b64decode(data_b64, validate=True)
        except (binascii.Error, ValueError):
            raise UploadRejected("некорректный base64") from None
        if len(raw) > MAX_BASE64_BYTES:
            raise UploadRejected(f"base64 больше {MAX_BASE64_BYTES} байт после декодирования")
        return cls.build(kind, filename, raw)

    @classmethod
    def _resolve_data_path(cls, kind: str, path: str) -> tuple["UploadPolicy", Path, int]:
        from app.settings import settings

        policy = cls._policy(kind)
        raw = str(path or "").strip()
        if not raw or "\x00" in raw:
            raise UploadRejected("пустой путь")
        root = Path(settings.data_dir).resolve()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = root / candidate
        if ".." in candidate.parts:
            raise UploadRejected("в пути нельзя использовать ..")
        try:
            real = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise UploadRejected("файл не найден") from None
        if root != real and root not in real.parents:
            raise UploadRejected("путь вне DATA_DIR")
        if not real.is_file():
            raise UploadRejected("это не файл")
        if any(part.startswith(".") for part in real.relative_to(root).parts):
            raise UploadRejected("скрытые файлы и папки не загружаются")
        if real.suffix.lower() not in policy.extensions:
            raise UploadRejected(f"тип файла {real.suffix.lower() or '(нет)'} не разрешён "
                                 f"для {kind}: {sorted(policy.extensions)}")
        size = real.stat().st_size
        if size > policy.max_bytes:
            raise UploadRejected(f"файл {size} байт больше лимита {policy.max_bytes}")
        return policy, real, size

    @classmethod
    def from_data_path(cls, kind: str, path: str) -> "FileUpload":
        policy, real, _size = cls._resolve_data_path(kind, path)
        with real.open("rb") as fh:
            content = fh.read(policy.max_bytes + 1)
        verify_attachment_binding(real, content=content)
        return cls.build(kind, real.name, content)

    @classmethod
    def check_data_path(cls, kind: str, path: str) -> int:
        policy, real, size = cls._resolve_data_path(kind, path)
        verify_attachment_binding(real, size=size)
        ext = real.suffix.lower()
        with real.open("rb") as fh:
            head = fh.read(65536)
        if size == 0:
            raise UploadRejected("пустой файл")
        if ext in _TEXT_EXT:
            text = codecs.getincrementaldecoder("utf-8")()
            try:
                text.decode(head, final=size <= len(head))
            except UnicodeDecodeError:
                raise UploadRejected(f"содержимое не похоже на {ext}") from None
            ok = b"\x00" not in head
        else:
            ok = _magic_ok(ext, head[:16], head)
        if not ok:
            raise UploadRejected(f"содержимое не похоже на {ext}")
        return size


def source_to_upload(kind: str, params: dict) -> FileUpload:
    path, b64 = params.get("source_path"), params.get("data_base64")
    if bool(path) == bool(b64):
        raise UploadRejected("укажите ровно один источник: source_path или data_base64")
    if path:
        return FileUpload.from_data_path(kind, path)
    return FileUpload.from_base64(kind, params.get("filename") or "", b64)


def validate_source_params(params: dict, kind: str) -> str | None:
    path, b64 = params.get("source_path"), params.get("data_base64")
    if bool(path) == bool(b64):
        return "укажите ровно один источник: source_path или data_base64"
    try:
        if path:
            FileUpload.check_data_path(kind, path)
        else:
            FileUpload.from_base64(kind, params.get("filename") or "", b64)
    except UploadRejected as exc:
        return str(exc)
    return None


_OPERATOR_BAD = re.compile(r"[^0-9A-Za-z_.:@-]")


def normalize_operator(value: Any) -> str:
    text = _OPERATOR_BAD.sub("_", str(value or "").strip())[:120]
    return text or "unknown"


@dataclass(frozen=True)
class _Replay:
    body: Any


ProgressSink = Callable[[float | None, str], Awaitable[None]]


@dataclass
class ActionContext:
    app: Any
    operator: str = "system"
    plan_id: str | None = None
    step_index: int | None = None
    idempotency_key: str | None = None
    read_only: bool = False
    progress_sink: ProgressSink | None = None
    _client: httpx.AsyncClient | None = field(default=None, repr=False)

    async def call(self, method: str, path: str, *, params: dict | None = None,
                   json: Any = None, files: dict | None = None,
                   timeout: float = 120.0) -> Any:
        method = method.upper()
        if self.read_only and method != "GET":
            raise WriteNotAllowed(f"{method} {path} is not allowed in read-only context")
        multipart = None
        if files is not None:
            if method not in ("POST", "PUT", "PATCH") or json is not None:
                raise UploadRejected("files допустимы только для POST/PUT/PATCH без json")
            if not files or not all(isinstance(f, FileUpload) for f in files.values()):
                raise UploadRejected("files принимает только FileUpload")
            checked = [FileUpload.build(f.policy, f.filename, f.content, field_name=f.form_field)
                       for f in files.values()]
            if sum(len(f.content) for f in checked) > MAX_TOTAL_UPLOAD_BYTES:
                raise UploadRejected("суммарный размер файлов больше лимита")
            multipart = {name: (f.filename, f.content, f.content_type)
                         for name, f in zip(files.keys(), checked)}
        from app.services import access_guard
        headers = {"X-Action-Layer-Operator": normalize_operator(self.operator),
                   access_guard.OPERATOR_HEADER: access_guard.get_operator_token()}
        if self.idempotency_key:
            headers["Idempotency-Key"] = self.idempotency_key
        fingerprint = None
        if self.idempotency_key and method != "GET":
            fingerprint = await self._claim(method, path, params, json, multipart)
            if isinstance(fingerprint, _Replay):
                return fingerprint.body
        transport = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transport, base_url=_BASE_URL,
                                     timeout=timeout) as client:
            resp = await client.request(method, API_PREFIX + path, params=params,
                                        json=json, files=multipart, headers=headers)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        if resp.status_code >= 400:
            await self._finish(fingerprint, ok=False, status=resp.status_code, body=None)
            detail = body.get("detail") if isinstance(body, dict) else body
            raise ActionCallError(resp.status_code, detail)
        refused = isinstance(body, dict) and (body.get("rejected") or body.get("ok") is False)
        await self._finish(fingerprint, ok=not refused, status=resp.status_code, body=body)
        return body

    async def _claim(self, method: str, path: str, params: Any, json: Any, multipart: Any):
        from sqlalchemy.exc import SQLAlchemyError

        from app.services.action_layer import store

        files_fp = None
        if multipart:
            files_fp = {k: [v[0], hashlib.sha256(v[1]).hexdigest()] for k, v in multipart.items()}
        fp = store.call_fingerprint(method, path, params=params, body=json, files=files_fp)
        try:
            state, row = await store.claim_call(
                self.idempotency_key, fp, method=method, path=path,
                operator=normalize_operator(self.operator), plan_id=self.plan_id or "")
        except SQLAlchemyError:
            return None
        if state == "done":
            if row.response_omitted:
                return _Replay({"idempotent_replay": True, "response_omitted": True})
            return _Replay(row.response)
        if state == "unknown" and row is not None and await self._step_is_idempotent():
            if await store.reclaim_call(row.id):
                return fp
        if state == "unknown":
            raise ActionCallError(409, (
                "предыдущая попытка этого шага с тем же запросом началась и не завершилась "
                "записью исхода: результат неизвестен, повтор запрещён (проверьте состояние "
                "проекта вручную)"))
        return fp

    async def _step_is_idempotent(self) -> bool:
        if not self.plan_id or self.step_index is None:
            return False
        from sqlalchemy import select
        from sqlalchemy.exc import SQLAlchemyError

        from app.services.action_layer import store
        from app.services.action_layer.spec import registry
        try:
            async with store.sessions()() as s:
                action_id = await s.scalar(select(store.ActionPlanStep.action_id).where(
                    store.ActionPlanStep.plan_id == self.plan_id,
                    store.ActionPlanStep.idx == self.step_index))
        except SQLAlchemyError:
            return False
        spec = registry.find(action_id) if action_id else None
        return bool(spec and spec.idempotent)

    async def _finish(self, fingerprint: Any, *, ok: bool, status: int, body: Any) -> None:
        if not fingerprint or isinstance(fingerprint, _Replay):
            return
        from sqlalchemy.exc import SQLAlchemyError

        from app.services.action_layer import store
        try:
            await store.finish_call(self.idempotency_key, fingerprint, ok=ok,
                                    http_status=status, response=body)
        except SQLAlchemyError:
            return

    async def progress(self, fraction: float | None, note: str = "") -> None:
        if self.progress_sink is not None:
            await self.progress_sink(fraction, note)

    def readonly_copy(self) -> "ActionContext":
        return ActionContext(app=self.app, operator=self.operator, plan_id=self.plan_id,
                             step_index=self.step_index, read_only=True)
