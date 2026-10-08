"""Аудит действий оператора (B01): каждая изменяющая ручка /api оставляет строку в `operator_audit`.

Что пишется: id, ts, request_id, method, path (без query, секреты вырезаны), route_template,
project_id, actor (имя, не токен), status_code, duration_ms, body_sha256, outcome.
Тела запросов НЕ хранятся. Для JSON дайджест считается по РЕДАКТИРОВАННОМУ телу (пароли и ключи
заменены), для остальных - по сырым байтам; для /api/auth/ дайджест не пишется вовсе.

Таблица append-only (триггеры как у spend_ledger): UPDATE и DELETE запрещены.
Запись не блокирует запрос: сбой записи логируется, счётчик `skipped_writes()` растёт.
GET/HEAD/OPTIONS и WebSocket не пишутся.

Слой - чистый ASGI (не BaseHTTPMiddleware), ставится внешним, чтобы отказы защитного слоя
(403 по Host/Origin/токену) тоже попадали в журнал. Запись идёт в мастер-БД по
`settings.sqlite_path` на момент записи (отдельное sqlite3-соединение в потоке).

Живой Studio не проверялось: только юнит и ASGI-клиент.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import CreateIndex, CreateTable

from app.models import Base

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
API_PREFIX = "/api"
REQUEST_ID_HEADER = "X-Request-ID"
BODY_JSON_LIMIT = 256 * 1024  # больше - дайджест по сырым байтам, тело не разбираем
NO_DIGEST_PREFIXES = ("/api/auth/",)  # логин и пр.: даже дайджест тела не нужен
SESSION_COOKIE = "vp_session"

OUTCOME_OK = "ok"
OUTCOME_DENIED = "denied"
OUTCOME_REJECTED = "rejected"
OUTCOME_ERROR = "error"


class OperatorAudit(Base):
    """Append-only: одна строка на одно изменяющее обращение к /api."""
    __tablename__ = "operator_audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(500))
    route_template: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(120), default="local")
    status_code: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    body_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    outcome: Mapped[str] = mapped_column(String(12))


_COLUMNS = ("ts", "request_id", "method", "path", "route_template", "project_id", "actor",
            "status_code", "duration_ms", "body_sha256", "outcome")

_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS operator_audit_no_update BEFORE UPDATE ON "
    "operator_audit BEGIN SELECT RAISE(ABORT, 'operator audit is append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS operator_audit_no_delete BEFORE DELETE ON "
    "operator_audit BEGIN SELECT RAISE(ABORT, 'operator audit is append-only'); END",
)

# --- счётчики --------------------------------------------------------------------------------------

_LOCK = threading.Lock()
_STATS = {"written": 0, "skipped": 0, "last_error": ""}


def skipped_writes() -> int:
    return _STATS["skipped"]


def stats() -> dict:
    with _LOCK:
        return dict(_STATS)


def reset_stats() -> None:
    with _LOCK:
        _STATS.update(written=0, skipped=0, last_error="")


def enabled() -> bool:
    return (os.environ.get("VP_OPERATOR_AUDIT") or "1").strip().lower() not in {"0", "false", "no", "off"}


# --- редакция --------------------------------------------------------------------------------------

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_HEX = re.compile(r"^[0-9a-fA-F]{32,}$")
_TOKENISH = re.compile(r"^[A-Za-z0-9_\-]{32,}$")
_REQ_ID = re.compile(r"^[A-Za-z0-9._\-]{8,64}$")
_PROJECT_IN_PATH = re.compile(r"/projects?/(\d+)(?:/|$)")


def redact_path(path: str) -> str:
    """Путь без query; сегменты, похожие на токен/ключ, заменены на [redacted]."""
    from app.services.observation import _CREDENTIAL_TEXT
    out = []
    for seg in (path or "").split("?", 1)[0].split("/"):
        if _TOKENISH.match(seg) and not _UUID.match(seg) and not _HEX.match(seg):
            seg = "[redacted]"
        else:
            seg = _CREDENTIAL_TEXT.sub("[redacted]", seg)
        out.append(seg)
    return "/".join(out)[:500]


def body_digest(raw: bytes | None, *, content_type: str = "", complete: bool = True,
                total: int = 0, raw_sha256: str | None = None) -> str | None:
    """Дайджест тела без хранения тела. JSON - по редактированному виду, остальное - по сырым байтам."""
    if total == 0:
        return None
    if raw is not None and complete and "json" in (content_type or "").lower():
        try:
            from app.services.observation import digest
            return digest(json.loads(raw.decode("utf-8")))
        except Exception:  # noqa: BLE001 - не JSON: падаем на сырой дайджест
            pass
    return raw_sha256


def _project_from_json(raw: bytes | None, content_type: str, complete: bool) -> int | None:
    if raw is None or not complete or "json" not in (content_type or "").lower():
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, dict):
        v = data.get("project_id")
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    return None


def _project_id(scope: dict, path: str, raw: bytes | None, content_type: str, complete: bool) -> int | None:
    params = scope.get("path_params") or {}
    for key in ("project_id", "pid"):
        v = params.get(key)
        if v is not None and str(v).isdigit():
            return int(v)
    m = _PROJECT_IN_PATH.search(path)
    if m:
        return int(m.group(1))
    return _project_from_json(raw, content_type, complete)


def route_template(scope: dict, path: str) -> str | None:
    """Шаблон маршрута с префиксом: '/api/projects/{project_id}/go'. None, если маршрут не нашёлся (404).

    Собирается из фактического пути и path_params (а не из внутренностей FastAPI: у вложенных роутеров
    `route.path` бывает без префикса и зависит от версии)."""
    if scope.get("route") is None and "endpoint" not in scope:
        return None
    segs = path.split("/")
    pos = 0
    for name, value in (scope.get("path_params") or {}).items():
        want = str(value).split("/")
        for i in range(pos, len(segs) - len(want) + 1):
            if segs[i:i + len(want)] == want:
                segs[i:i + len(want)] = ["{" + name + "}"]
                pos = i + 1
                break
    return "/".join(segs)[:300]


def outcome_for(status: int, crashed: bool = False) -> str:
    if crashed or status >= 500:
        return OUTCOME_ERROR
    if status in (401, 403):
        return OUTCOME_DENIED
    if status >= 400:
        return OUTCOME_REJECTED
    return OUTCOME_OK


def _hdr(scope: dict, name: bytes) -> str | None:
    for k, v in scope.get("headers") or ():
        if k == name:
            return v.decode("latin-1")
    return None


def _cookie(scope: dict, name: str) -> str | None:
    raw = _hdr(scope, b"cookie")
    if not raw:
        return None
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == name:
            return v
    return None


def resolve_actor(scope: dict) -> str:
    """Имя действующего: пользователь веб-входа, иначе 'operator' (верный токен оператора), иначе 'local'.
    Сами токены и сессии в журнал не попадают."""
    try:
        from app.settings import settings
        if settings.web_auth_enabled:
            from app.web import auth_sessions as sess
            auth = _hdr(scope, b"authorization") or ""
            token = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else _cookie(scope, SESSION_COOKIE)
            if token:
                key = sess._hash_token(token)
                if sess._lookup(key) is not None:
                    hit = sess._MEM.get(key)
                    if hit and hit[1]:
                        return str(hit[1])[:120]
    except Exception:  # noqa: BLE001 - имя не критично
        logger.debug("operator_audit: actor lookup failed", exc_info=True)
    try:
        from app.services.access_guard import OPERATOR_HEADER, check_operator_token
        if check_operator_token(_hdr(scope, OPERATOR_HEADER.lower().encode())):
            # D12: the action layer names the person behind its internal call; the name is trusted only together
            # with a valid operator token (an outside caller cannot forge it) and is reduced to safe characters.
            named = _hdr(scope, b"x-action-layer-operator")
            if named:
                from app.services.action_layer.context import normalize_operator
                return ("operator:" + normalize_operator(named))[:120]
            return "operator"
    except Exception:  # noqa: BLE001
        logger.debug("operator_audit: operator token check failed", exc_info=True)
    return "local"


# --- хранилище -------------------------------------------------------------------------------------

_ensured: set[str] = set()


def _db_path() -> Path:
    from app.settings import settings
    p = Path(settings.sqlite_path)
    return p if p.is_absolute() else Path.cwd() / p


def _ddl() -> list[str]:
    from sqlalchemy.dialects import sqlite
    d = sqlite.dialect()
    tbl = OperatorAudit.__table__
    out = [str(CreateTable(tbl, if_not_exists=True).compile(dialect=d))]
    out += [str(CreateIndex(ix, if_not_exists=True).compile(dialect=d)) for ix in sorted(tbl.indexes, key=lambda i: i.name)]
    out += list(_TRIGGERS)
    return out


def _ensure_schema(con: sqlite3.Connection, key: str) -> None:
    if key in _ensured:
        return
    for stmt in _ddl():
        con.execute(stmt)
    con.commit()
    _ensured.add(key)


def insert_row(row: dict) -> None:
    """Синхронная вставка одной строки (вызывается в потоке). Бросает исключение при сбое."""
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path), timeout=5)
    try:
        con.execute("PRAGMA busy_timeout=5000")
        _ensure_schema(con, str(path))
        con.execute(f"INSERT INTO operator_audit ({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                    tuple(row[c] for c in _COLUMNS))
        con.commit()
    finally:
        con.close()


async def record(row: dict) -> bool:
    """Записать строку; сбой не пробрасывается (запрос не блокируется), считается и логируется."""
    try:
        await asyncio.to_thread(insert_row, row)
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            _STATS["skipped"] += 1
            _STATS["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
        logger.error("operator_audit: запись аудита пропущена ({} {}): {}: {}", row.get("method"),
                     row.get("path"), type(exc).__name__, exc)
        return False
    with _LOCK:
        _STATS["written"] += 1
    return True


def list_rows(limit: int = 100, project_id: int | None = None) -> list[dict]:
    """Последние строки (для ленты/диагностики); читает по тому же пути."""
    path = _db_path()
    if not path.exists():
        return []
    con = sqlite3.connect(str(path), timeout=5)
    try:
        con.row_factory = sqlite3.Row
        sql = "SELECT id, " + ", ".join(_COLUMNS) + " FROM operator_audit"
        args: list = []
        if project_id is not None:
            sql += " WHERE project_id = ?"
            args.append(project_id)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        try:
            return [dict(r) for r in con.execute(sql, args)]
        except sqlite3.OperationalError:
            return []
    finally:
        con.close()


# --- ASGI-слой -------------------------------------------------------------------------------------

def new_request_id(incoming: str | None = None) -> str:
    if incoming and _REQ_ID.match(incoming):
        return incoming
    return uuid.uuid4().hex


class OperatorAuditMiddleware:
    """Чистый ASGI: request_id для любого http-запроса /api, строка аудита для изменяющих методов."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope.get("type") != "http" or not path.startswith(API_PREFIX):
            await self.app(scope, receive, send)
            return
        rid = new_request_id(_hdr(scope, b"x-request-id"))
        scope.setdefault("state", {})["request_id"] = rid
        method = (scope.get("method") or "GET").upper()
        audited = method in MUTATING_METHODS and enabled()

        status = {"code": 0}
        body = {"total": 0, "buf": bytearray(), "complete": True, "sha": hashlib.sha256()}

        async def _send(message):
            if message["type"] == "http.response.start":
                status["code"] = int(message.get("status", 0))
                message = dict(message)
                message["headers"] = [*message.get("headers", []), (REQUEST_ID_HEADER.lower().encode(), rid.encode())]
            await send(message)

        async def _receive():
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"") or b""
                if chunk:
                    body["total"] += len(chunk)
                    body["sha"].update(chunk)
                    if len(body["buf"]) + len(chunk) <= BODY_JSON_LIMIT:
                        body["buf"].extend(chunk)
                    else:
                        body["complete"] = False
            return message

        started = time.perf_counter()
        crashed = False
        with logger.contextualize(request_id=rid):
            try:
                await self.app(scope, _receive if audited else receive, _send)
            except BaseException:
                crashed = True
                raise
            finally:
                if audited:
                    row = self._row(scope, path, method, rid, status["code"], crashed, started, body)
                    # shield: отмена запроса (разрыв клиента) не должна терять строку аудита
                    await asyncio.shield(asyncio.ensure_future(record(row)))

    @staticmethod
    def _row(scope, path, method, rid, code, crashed, started, body) -> dict:
        ctype = _hdr(scope, b"content-type") or ""
        raw = bytes(body["buf"]) if body["complete"] else None
        template = route_template(scope, path)
        code = code or (500 if crashed else 0)
        digest = None
        if not any(path.startswith(p) for p in NO_DIGEST_PREFIXES):
            digest = body_digest(raw, content_type=ctype, complete=body["complete"], total=body["total"],
                                 raw_sha256=body["sha"].hexdigest() if body["total"] else None)
        return {
            "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" "),
            "request_id": rid, "method": method, "path": redact_path(path),
            "route_template": template,
            "project_id": _project_id(scope, path, raw, ctype, body["complete"]),
            "actor": resolve_actor(scope),
            "status_code": code, "duration_ms": int((time.perf_counter() - started) * 1000),
            "body_sha256": digest, "outcome": outcome_for(code, crashed),
        }
