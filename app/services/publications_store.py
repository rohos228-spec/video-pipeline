"""Журнал публикаций: идемпотентность, намерение до клика, lease на проект x платформа (A14).

Таблица `publications` создаётся здесь же (`CREATE TABLE IF NOT EXISTS`); регистрация в
`schema_migrations` делается отдельно (волна W2). Ключ идемпотентности: (project_id, platform, sha256).

Порядок для одной платформы (каждый шаг фиксируется commit-ом сразу, не ждёт конца шага):
  1. claim()         — запись НАМЕРЕНИЯ (status=planned) и lease; второй воркер получает busy;
  2. mark_clicked()  — status=clicked ДО реального клика; упали между кликом и записью итога
                       -> в журнале остаётся clicked, автоповтора нет (ждёт человека);
  3. record_result() — итог: confirmed / unconfirmed / failed / rejected.
Автоповтор разрешён ТОЛЬКО при сбое до клика (failed без clicked_at, planned с истёкшим lease,
skipped) и не более MAX_PRE_CLICK_ATTEMPTS раз.

W6-1 (E01/E02/E11): к журналу добавлены ОТДЕЛЬНЫЕ таблицы (`_DDL_W6`, DDL `publications` не менялся,
отпечаток миграции 0012 прежний): `publish_approvals` (одноразовое подтверждение человека на пару
платформа x sha256 файла x sha256 подписи, тратится ПЕРЕД кликом) и `publication_manual_actions`
(append-only журнал ручных подтверждений и пометок). Отображаемые состояния: publish_skipped, intent,
unconfirmed, confirmed, failed_before_click, failed_after_click (`display_state`).

Живой платформой не проверялось: только юнит и сухой прогон на SQLite.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

LEASE_SECONDS = 900.0
MAX_PRE_CLICK_ATTEMPTS = 3

S_PLANNED = "planned"
S_CLICKED = "clicked"
S_CONFIRMED = "confirmed"
S_UNCONFIRMED = "unconfirmed"
S_FAILED = "failed"
S_REJECTED = "rejected"
S_SKIPPED = "skipped"
ALL_STATUSES = (S_PLANNED, S_CLICKED, S_CONFIRMED, S_UNCONFIRMED, S_FAILED, S_REJECTED, S_SKIPPED)

# claim-исходы
ACQUIRED = "acquired"
BUSY = "busy"  # другой воркер держит живой lease
DONE = "done"  # уже confirmed
NEEDS_HUMAN = "needs_human"  # клик мог состояться или попытки исчерпаны: автоповтор запрещён

_DDL = (
    """CREATE TABLE IF NOT EXISTS publications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        artifact_uuid TEXT,
        sha256 TEXT NOT NULL,
        account_id TEXT,
        status TEXT NOT NULL,
        post_url TEXT,
        post_id TEXT,
        attempts INTEGER NOT NULL DEFAULT 0,
        screenshot TEXT,
        error_code TEXT,
        lease_owner TEXT,
        lease_until REAL,
        clicked_at REAL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_publications_idem ON publications(project_id, platform, sha256)",
    "CREATE INDEX IF NOT EXISTS ix_publications_project ON publications(project_id)",
)

_COLS = ("id, project_id, platform, artifact_uuid, sha256, account_id, status, post_url, post_id, "
         "attempts, screenshot, error_code, lease_owner, lease_until, clicked_at, updated_at")

# --- W6-1: подтверждение человека и ручные действия (отдельные таблицы, миграция регистрируется интегратором) ---
MANUAL_FAILED_CODE = "manual_not_published"
MANUAL_CONFIRMED_ID = "manual-confirmed"
MIN_REASON_CHARS = 5
MAX_REASON_CHARS = 500

D_SKIPPED = "publish_skipped"
D_INTENT = "intent"
D_UNCONFIRMED = "unconfirmed"
D_CONFIRMED = "confirmed"
D_FAILED_BEFORE = "failed_before_click"
D_FAILED_AFTER = "failed_after_click"
DISPLAY_LABELS_RU = {
    D_SKIPPED: "пропущено (автопубликация выключена)",
    D_INTENT: "намерение записано, клика не было",
    D_UNCONFIRMED: "не подтверждено, ждёт проверки человека",
    D_CONFIRMED: "опубликовано",
    D_FAILED_BEFORE: "сбой до клика, ничего не опубликовано",
    D_FAILED_AFTER: "сбой после клика, автоповтора не будет",
}

_DDL_W6 = (
    """CREATE TABLE IF NOT EXISTS publish_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        caption_sha256 TEXT NOT NULL,
        approved_by TEXT NOT NULL,
        approved_at REAL NOT NULL,
        consumed_at REAL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_publish_approvals_key ON publish_approvals(project_id, platform, sha256)",
    """CREATE TABLE IF NOT EXISTS publication_manual_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        publication_id INTEGER NOT NULL,
        project_id INTEGER NOT NULL,
        platform TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        action TEXT NOT NULL,
        prev_status TEXT,
        prev_post_url TEXT,
        post_url TEXT,
        reason TEXT,
        actor TEXT NOT NULL,
        created_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS ix_publication_manual_project ON publication_manual_actions(project_id)",
    "CREATE TRIGGER IF NOT EXISTS publication_manual_no_update BEFORE UPDATE ON publication_manual_actions "
    "BEGIN SELECT RAISE(ABORT, 'publication manual actions are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS publication_manual_no_delete BEFORE DELETE ON publication_manual_actions "
    "BEGIN SELECT RAISE(ABORT, 'publication manual actions are append-only'); END",
)

_schema_ready_for: set[int] = set()


@dataclass
class Claim:
    outcome: str
    pub_id: int | None = None
    owner: str = ""
    status: str = ""
    attempts: int = 0


def new_owner() -> str:
    return uuid.uuid4().hex


def idempotency_key(project_id: int, platform: str, sha256: str) -> str:
    return f"{project_id}:{platform}:{sha256}"


def sha256_of_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def sha256_of_file_async(path: str | Path) -> str:
    return await asyncio.to_thread(sha256_of_file, path)


async def ensure_schema(session: AsyncSession) -> None:
    key = id(session.get_bind()) if session.bind is not None else 0
    if key in _schema_ready_for:
        # В памяти (:memory:) таблица живёт с соединением, кэш по bind безопасен: bind тот же.
        try:
            await session.execute(text("SELECT 1 FROM publications LIMIT 0"))
            await session.execute(text("SELECT 1 FROM publish_approvals LIMIT 0"))
            await session.execute(text("SELECT 1 FROM publication_manual_actions LIMIT 0"))
            return
        except Exception:  # noqa: BLE001
            _schema_ready_for.discard(key)
    for ddl in (*_DDL, *_DDL_W6):
        await session.execute(text(ddl))
    await session.commit()
    _schema_ready_for.add(key)


async def _commit(session: AsyncSession) -> None:
    try:
        from app.db import commit_with_retry
        await commit_with_retry(session)
    except ImportError:  # pragma: no cover
        await session.commit()


async def _get(session: AsyncSession, project_id: int, platform: str, sha256: str) -> dict | None:
    row = (await session.execute(
        text(f"SELECT {_COLS} FROM publications WHERE project_id=:p AND platform=:pl AND sha256=:s"),
        {"p": project_id, "pl": platform, "s": sha256})).mappings().first()
    return dict(row) if row else None


# W6 интеграция: отказы ДО клика, после которых нужен человек (вход, капча, 2FA, лимит частоты, отклонение,
# нет профиля или конфиг неверен). Автоповтор запрещён, выход - «Не опубликовано» с allow_republish в панели.
# Совпадает с publish_accounts.NO_AUTO_RETRY_CODES (проверяет тест).
HUMAN_HOLD_CODES = frozenset({"not_logged_in", "captcha", "two_factor", "rate_limited", "rejected",
                              "profile_missing", "accounts_config_invalid"})


def is_auto_retryable(row: dict, now: float) -> bool:
    """Можно ли автоматически взять платформу снова (клик гарантированно не был сделан)."""
    st = row["status"]
    if row.get("clicked_at"):
        return False
    if st == S_FAILED and row.get("error_code") in HUMAN_HOLD_CODES:
        return False
    if st == S_SKIPPED:
        return True
    if st == S_FAILED:
        return int(row.get("attempts") or 0) < MAX_PRE_CLICK_ATTEMPTS
    if st == S_PLANNED:
        return (row.get("lease_until") or 0) < now
    return False


async def claim(session: AsyncSession, *, project_id: int, platform: str, sha256: str,
                artifact_uuid: str | None, owner: str, account_id: str | None = None,
                lease_s: float = LEASE_SECONDS, now: float | None = None) -> Claim:
    """Записать намерение публикации и взять lease. Атомарно через UNIQUE + условный UPDATE."""
    await ensure_schema(session)
    now = time.time() if now is None else now
    ins = await session.execute(text(
        "INSERT OR IGNORE INTO publications (project_id, platform, artifact_uuid, sha256, account_id, "
        "status, attempts, lease_owner, lease_until, created_at, updated_at) "
        "VALUES (:p, :pl, :au, :s, :acc, 'planned', 1, :o, :lu, :now, :now)"),
        {"p": project_id, "pl": platform, "au": artifact_uuid, "s": sha256, "acc": account_id,
         "o": owner, "lu": now + lease_s, "now": now})
    if ins.rowcount == 1:
        await _commit(session)
        row = await _get(session, project_id, platform, sha256)
        return Claim(ACQUIRED, row["id"], owner, S_PLANNED, 1)

    row = await _get(session, project_id, platform, sha256)
    await _commit(session)  # закрыть читающую транзакцию
    if row is None:  # pragma: no cover
        return Claim(BUSY)
    if row["status"] == S_CONFIRMED:
        return Claim(DONE, row["id"], status=row["status"], attempts=row["attempts"])
    if not is_auto_retryable(row, now):
        if row["status"] == S_PLANNED and not row.get("clicked_at"):
            return Claim(BUSY, row["id"], status=row["status"], attempts=row["attempts"])
        return Claim(NEEDS_HUMAN, row["id"], status=row["status"], attempts=row["attempts"])

    # условный UPDATE: выигрывает ровно один из параллельных воркеров
    upd = await session.execute(text(
        "UPDATE publications SET status='planned', attempts=attempts+1, lease_owner=:o, lease_until=:lu, "
        "error_code=NULL, updated_at=:now "
        "WHERE id=:id AND clicked_at IS NULL AND ("
        "  status='skipped' OR (status='failed' AND attempts<:mx) OR (status='planned' AND lease_until<:now))"),
        {"o": owner, "lu": now + lease_s, "now": now, "id": row["id"], "mx": MAX_PRE_CLICK_ATTEMPTS})
    await _commit(session)
    if upd.rowcount == 1:
        return Claim(ACQUIRED, row["id"], owner, S_PLANNED, int(row["attempts"]) + 1)
    return Claim(BUSY, row["id"], status=row["status"], attempts=row["attempts"])


async def mark_clicked(session: AsyncSession, pub_id: int, owner: str, *, now: float | None = None) -> bool:
    """Зафиксировать «сейчас кликнем» ДО клика. False — клик делать нельзя (lease потерян)."""
    now = time.time() if now is None else now
    upd = await session.execute(text(
        "UPDATE publications SET status='clicked', clicked_at=:now, updated_at=:now "
        "WHERE id=:id AND lease_owner=:o AND status='planned'"),
        {"now": now, "id": pub_id, "o": owner})
    await _commit(session)
    return upd.rowcount == 1


async def record_result(session: AsyncSession, pub_id: int, owner: str, *, status: str,
                        post_url: str | None = None, post_id: str | None = None,
                        error_code: str | None = None, screenshot: str | None = None,
                        account_id: str | None = None, now: float | None = None) -> str:
    """Записать итог платформы сразу. Возвращает фактически записанный статус.

    confirmed без post_url и без post_id невозможен: понижается до unconfirmed.
    """
    if status not in ALL_STATUSES:
        raise ValueError(f"unknown publication status {status!r}")
    now = time.time() if now is None else now
    if status == S_CONFIRMED and not (post_url or post_id):
        status, error_code = S_UNCONFIRMED, error_code or "confirmed_without_url"
    await session.execute(text(
        "UPDATE publications SET status=:st, post_url=:u, post_id=:pid, error_code=:e, screenshot=:sc, "
        "account_id=COALESCE(:acc, account_id), "
        "lease_owner=NULL, lease_until=NULL, updated_at=:now WHERE id=:id AND lease_owner=:o"),
        {"st": status, "u": post_url, "pid": post_id, "e": error_code, "sc": screenshot, "acc": account_id,
         "now": now, "id": pub_id, "o": owner})
    await _commit(session)
    row = (await session.execute(text("SELECT status FROM publications WHERE id=:id"),
                                 {"id": pub_id})).first()
    await _commit(session)
    return row[0] if row else status


async def record_unclaimed(session: AsyncSession, *, project_id: int, platform: str, sha256: str,
                           artifact_uuid: str | None, status: str, clicked: bool,
                           post_url: str | None = None, post_id: str | None = None,
                           error_code: str | None = None, screenshot: str | None = None,
                           account_id: str | None = None, now: float | None = None) -> str:
    """Записать итог, пришедший без claim (например из старого вызова publish_everywhere).

    Не затирает confirmed. Если клик был, ставим clicked_at, чтобы автоповтора не было.
    """
    await ensure_schema(session)
    now = time.time() if now is None else now
    if status == S_CONFIRMED and not (post_url or post_id):
        status, error_code = S_UNCONFIRMED, error_code or "confirmed_without_url"
    await session.execute(text(
        "INSERT OR IGNORE INTO publications (project_id, platform, artifact_uuid, sha256, status, attempts, "
        "clicked_at, created_at, updated_at) VALUES (:p, :pl, :au, :s, 'planned', 1, :ca, :now, :now)"),
        {"p": project_id, "pl": platform, "au": artifact_uuid, "s": sha256,
         "ca": now if clicked else None, "now": now})
    await session.execute(text(
        "UPDATE publications SET status=:st, post_url=:u, post_id=:pid, error_code=:e, "
        "screenshot=COALESCE(:sc, screenshot), account_id=COALESCE(:acc, account_id), "
        "clicked_at=CASE WHEN :cl=1 AND clicked_at IS NULL THEN :now ELSE clicked_at END, "
        "lease_owner=NULL, lease_until=NULL, updated_at=:now "
        "WHERE project_id=:p AND platform=:pl AND sha256=:s AND status<>'confirmed'"),
        {"st": status, "u": post_url, "pid": post_id, "e": error_code, "sc": screenshot, "acc": account_id,
         "cl": 1 if clicked else 0,
         "now": now, "p": project_id, "pl": platform, "s": sha256})
    await _commit(session)
    row = await _get(session, project_id, platform, sha256)
    await _commit(session)
    return row["status"] if row else status


async def mark_skipped(session: AsyncSession, *, project_id: int, platform: str, sha256: str,
                       artifact_uuid: str | None, reason: str, now: float | None = None) -> None:
    """Публикация выключена флагом: пишем skipped (не published). Подтверждённое не трогаем."""
    await ensure_schema(session)
    now = time.time() if now is None else now
    await session.execute(text(
        "INSERT OR IGNORE INTO publications (project_id, platform, artifact_uuid, sha256, status, attempts, "
        "error_code, created_at, updated_at) VALUES (:p, :pl, :au, :s, 'skipped', 0, :e, :now, :now)"),
        {"p": project_id, "pl": platform, "au": artifact_uuid, "s": sha256, "e": reason, "now": now})
    await session.execute(text(
        "UPDATE publications SET status='skipped', error_code=:e, updated_at=:now "
        "WHERE project_id=:p AND platform=:pl AND sha256=:s AND status='planned' AND clicked_at IS NULL "
        "AND (lease_until IS NULL OR lease_until<:now)"),
        {"e": reason, "now": now, "p": project_id, "pl": platform, "s": sha256})
    await _commit(session)


async def list_for(session: AsyncSession, project_id: int, sha256: str | None = None) -> list[dict]:
    await ensure_schema(session)
    sql = f"SELECT {_COLS} FROM publications WHERE project_id=:p"
    params: dict = {"p": project_id}
    if sha256 is not None:
        sql += " AND sha256=:s"
        params["s"] = sha256
    rows = (await session.execute(text(sql + " ORDER BY id"), params)).mappings().all()
    await _commit(session)
    return [dict(r) for r in rows]


def summarize(rows: list[dict], platforms: list[str], now: float | None = None) -> dict:
    """Итог по проекту: published разрешён только если все платформы confirmed с URL/ID."""
    now = time.time() if now is None else now
    by = {r["platform"]: r for r in rows}
    confirmed = [p for p in platforms
                 if p in by and by[p]["status"] == S_CONFIRMED and (by[p]["post_url"] or by[p]["post_id"])]
    retryable, busy, human, skipped, manual_failed = [], [], [], [], []
    for p in platforms:
        r = by.get(p)
        if r is None:
            retryable.append(p)
        elif p in confirmed:
            continue
        elif r["status"] == S_PLANNED and not r.get("clicked_at") and (r.get("lease_until") or 0) >= now:
            busy.append(p)
        elif r["status"] == S_SKIPPED:
            skipped.append(p)
        elif is_auto_retryable(r, now):
            retryable.append(p)
        elif r["status"] == S_FAILED and r.get("error_code") == MANUAL_FAILED_CODE:
            manual_failed.append(p)  # человек сам закрыл платформу как «не опубликовано»: ждать нечего
        else:
            human.append(p)
    return {"all_confirmed": len(confirmed) == len(platforms), "confirmed": confirmed,
            "retryable": retryable, "busy": busy, "needs_human": human, "skipped": skipped,
            "manual_failed": manual_failed}


# ── W6-1: состояния, подтверждение человека (E01), ручные действия (E02) ─────────────────────────


def display_state(row: dict) -> str:
    """Одно из шести состояний записи: publish_skipped, intent, unconfirmed, confirmed,
    failed_before_click, failed_after_click. `confirmed` только с post_url или post_id."""
    st = row["status"]
    clicked = bool(row.get("clicked_at"))
    if st == S_CONFIRMED:
        return D_CONFIRMED if (row.get("post_url") or row.get("post_id")) else D_UNCONFIRMED
    if st == S_SKIPPED:
        return D_SKIPPED
    if st == S_PLANNED:
        return D_UNCONFIRMED if clicked else D_INTENT
    if st in (S_CLICKED, S_UNCONFIRMED):
        return D_UNCONFIRMED
    return D_FAILED_AFTER if clicked else D_FAILED_BEFORE  # failed / rejected


class PublicationRefused(Exception):
    """Действие отклонено по правилам безопасности; code идёт в ответ API, message - человеку."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _clean_reason(reason: str | None) -> str:
    return " ".join((reason or "").split())[:MAX_REASON_CHARS]


def _clean_url(url: str | None) -> str | None:
    u = (url or "").strip()
    if not u:
        return None
    if len(u) > 1000 or not u.lower().startswith(("http://", "https://")):
        raise PublicationRefused("bad_post_url", "Ссылка на пост должна начинаться с http:// или https://.")
    return u


async def grant_approval(session: AsyncSession, *, project_id: int, platform: str, sha256: str,
                         caption_sha256: str, approved_by: str, now: float | None = None) -> None:
    """Подтверждение человека на ОДНУ публикацию (платформа x файл x подпись). Одноразовое.

    Повторная выдача на ту же пару сбрасывает «потрачено» (нужна, если человек сам разрешил повтор).
    Не выдаётся на уже опубликованное и на запись, где клик мог состояться: там сначала решает человек
    через ручное подтверждение или пометку «не опубликовано»."""
    await ensure_schema(session)
    now = time.time() if now is None else now
    row = await _get(session, project_id, platform, sha256)
    await _commit(session)
    if row is not None and row["status"] == S_CONFIRMED:
        raise PublicationRefused("already_confirmed", "Эта площадка уже подтверждена: повторная публикация не нужна.")
    if row is not None and not is_auto_retryable(row, now) and not (
            row["status"] == S_PLANNED and not row.get("clicked_at")):
        raise PublicationRefused(
            "needs_human_first",
            "По этой площадке клик мог состояться или попытки исчерпаны: сначала подтвердите вручную "
            "или пометьте «не опубликовано».")
    await session.execute(text(
        "INSERT INTO publish_approvals (project_id, platform, sha256, caption_sha256, approved_by, approved_at) "
        "VALUES (:p, :pl, :s, :c, :by, :now) "
        "ON CONFLICT(project_id, platform, sha256) DO UPDATE SET caption_sha256=:c, approved_by=:by, "
        "approved_at=:now, consumed_at=NULL"),
        {"p": project_id, "pl": platform, "s": sha256, "c": caption_sha256, "by": approved_by[:120], "now": now})
    await _commit(session)


async def list_approvals(session: AsyncSession, project_id: int, sha256: str,
                         caption_sha256: str | None = None) -> dict[str, dict]:
    """Подтверждения для файла: платформа -> запись с полем valid (не потрачено и подпись та же).

    Если подпись изменилась после подтверждения (caption_sha256 передан и не совпал), оно недействительно."""
    await ensure_schema(session)
    rows = (await session.execute(text(
        "SELECT platform, caption_sha256, approved_by, approved_at, consumed_at FROM publish_approvals "
        "WHERE project_id=:p AND sha256=:s"), {"p": project_id, "s": sha256})).mappings().all()
    await _commit(session)
    out: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        d["valid"] = d["consumed_at"] is None and (caption_sha256 is None or d["caption_sha256"] == caption_sha256)
        out[d["platform"]] = d
    return out


async def consume_approval(session: AsyncSession, *, project_id: int, platform: str, sha256: str,
                           caption_sha256: str, now: float | None = None) -> bool:
    """Потратить подтверждение ПЕРЕД кликом. Атомарно: два воркера не потратят одно подтверждение дважды."""
    await ensure_schema(session)
    now = time.time() if now is None else now
    upd = await session.execute(text(
        "UPDATE publish_approvals SET consumed_at=:now WHERE project_id=:p AND platform=:pl AND sha256=:s "
        "AND caption_sha256=:c AND consumed_at IS NULL"),
        {"now": now, "p": project_id, "pl": platform, "s": sha256, "c": caption_sha256})
    await _commit(session)
    return upd.rowcount == 1


async def revoke_approval(session: AsyncSession, *, project_id: int, platform: str, sha256: str,
                          now: float | None = None) -> dict:
    """Отозвать ещё не потраченное «да» (U-7). Атомарно: если шаг публикации уже потратил его, отзыв отклоняется.

    Запись не удаляется, а помечается потраченной (consumed_at): шаг публикации видит её как недействительную
    и не кликает. Новое «да» (grant_approval) сбрасывает пометку."""
    await ensure_schema(session)
    now = time.time() if now is None else now
    upd = await session.execute(text(
        "UPDATE publish_approvals SET consumed_at=:now WHERE project_id=:p AND platform=:pl AND sha256=:s "
        "AND consumed_at IS NULL"), {"now": now, "p": project_id, "pl": platform, "s": sha256})
    await _commit(session)
    if upd.rowcount != 1:
        raise PublicationRefused(
            "no_active_approval",
            "Действующего разрешения на эту площадку нет: оно не выдавалось, уже отозвано или уже использовано.")
    return {"platform": platform, "sha256": sha256, "revoked_at": now}


async def _manual_target(session: AsyncSession, project_id: int, platform: str, sha256: str, now: float) -> dict:
    await ensure_schema(session)
    row = await _get(session, project_id, platform, sha256)
    await _commit(session)
    if row is None:
        raise PublicationRefused("no_publication_record",
                                 "Для этого файла и площадки нет записи журнала: нечего подтверждать.")
    if row["status"] == S_PLANNED and not row.get("clicked_at") and (row.get("lease_until") or 0) >= now:
        raise PublicationRefused("busy", "Площадку сейчас обрабатывает воркер: подождите окончания шага.")
    return row


async def _log_manual(session: AsyncSession, row: dict, *, action: str, post_url: str | None, reason: str,
                      actor: str, now: float) -> None:
    await session.execute(text(
        "INSERT INTO publication_manual_actions (publication_id, project_id, platform, sha256, action, "
        "prev_status, prev_post_url, post_url, reason, actor, created_at) "
        "VALUES (:i, :p, :pl, :s, :a, :ps, :pu, :u, :r, :by, :now)"),
        {"i": row["id"], "p": row["project_id"], "pl": row["platform"], "s": row["sha256"], "a": action,
         "ps": row["status"], "pu": row.get("post_url"), "u": post_url, "r": reason, "by": actor[:120], "now": now})


async def confirm_manual(session: AsyncSession, *, project_id: int, platform: str, sha256: str, actor: str,
                         post_url: str | None = None, reason: str | None = None,
                         now: float | None = None) -> dict:
    """Человек подтверждает «опубликовано». Нужны ссылка на пост ИЛИ причина. Клик не повторяется.

    Уже подтверждённое не меняется (повтор запроса безопасен, changed=False)."""
    now = time.time() if now is None else now
    url, why = _clean_url(post_url), _clean_reason(reason)
    if url is None and len(why) < MIN_REASON_CHARS:
        raise PublicationRefused("evidence_required",
                                 f"Нужна ссылка на пост или причина не короче {MIN_REASON_CHARS} символов.")
    row = await _manual_target(session, project_id, platform, sha256, now)
    if row["status"] == S_CONFIRMED:
        return {"changed": False, "status": S_CONFIRMED, "state": display_state(row), "pub_id": row["id"]}
    # Ссылки нет: честная пометка вместо старого post_url (он мог быть страницей загрузки, а не постом).
    post_id = None if url else MANUAL_CONFIRMED_ID
    upd = await session.execute(text(
        "UPDATE publications SET status='confirmed', post_url=:u, post_id=:pid, error_code='manual_confirmed', "
        "clicked_at=COALESCE(clicked_at, :now), lease_owner=NULL, lease_until=NULL, updated_at=:now "
        "WHERE id=:id AND status=:prev"),
        {"u": url, "pid": post_id, "now": now, "id": row["id"], "prev": row["status"]})
    if upd.rowcount != 1:
        await session.rollback()
        raise PublicationRefused("state_changed", "Запись изменилась во время действия: обновите список и повторите.")
    await _log_manual(session, row, action="confirm", post_url=url, reason=why, actor=actor, now=now)
    await _commit(session)
    return {"changed": True, "status": S_CONFIRMED, "state": D_CONFIRMED, "pub_id": row["id"],
            "prev_status": row["status"]}


async def mark_failed_manual(session: AsyncSession, *, project_id: int, platform: str, sha256: str, actor: str,
                             reason: str | None, allow_republish: bool = False, now: float | None = None) -> dict:
    """Человек проверил площадку и пометил «не опубликовано». Причина обязательна.

    По умолчанию повторной публикации нет (попытки исчерпываются, отметка клика остаётся в записи).
    allow_republish=True снимает отметку клика и обнуляет попытки: следующая публикация возможна, но только
    с НОВЫМ подтверждением человека (подтверждение тратится перед кликом)."""
    now = time.time() if now is None else now
    why = _clean_reason(reason)
    if len(why) < MIN_REASON_CHARS:
        raise PublicationRefused("reason_required", f"Нужна причина не короче {MIN_REASON_CHARS} символов.")
    row = await _manual_target(session, project_id, platform, sha256, now)
    if row["status"] == S_CONFIRMED:
        raise PublicationRefused("already_confirmed", "Площадка подтверждена: пометить её «не опубликовано» нельзя.")
    params = {"code": MANUAL_FAILED_CODE, "now": now, "id": row["id"], "prev": row["status"]}
    if allow_republish:
        sql = ("UPDATE publications SET status='failed', error_code=:code, clicked_at=NULL, attempts=0, "
               "lease_owner=NULL, lease_until=NULL, updated_at=:now WHERE id=:id AND status=:prev")
    else:
        sql = ("UPDATE publications SET status='failed', error_code=:code, attempts=MAX(attempts, :mx), "
               "lease_owner=NULL, lease_until=NULL, updated_at=:now WHERE id=:id AND status=:prev")
        params["mx"] = MAX_PRE_CLICK_ATTEMPTS
    upd = await session.execute(text(sql), params)
    if upd.rowcount != 1:
        await session.rollback()
        raise PublicationRefused("state_changed", "Запись изменилась во время действия: обновите список и повторите.")
    await _log_manual(session, row, action="mark_failed_republish" if allow_republish else "mark_failed",
                      post_url=None, reason=why, actor=actor, now=now)
    await _commit(session)
    return {"changed": True, "status": S_FAILED, "pub_id": row["id"], "allow_republish": bool(allow_republish),
            "prev_status": row["status"]}


async def list_manual_actions(session: AsyncSession, project_id: int) -> list[dict]:
    await ensure_schema(session)
    rows = (await session.execute(text(
        "SELECT id, publication_id, platform, sha256, action, prev_status, post_url, reason, actor, created_at "
        "FROM publication_manual_actions WHERE project_id=:p ORDER BY id"), {"p": project_id})).mappings().all()
    await _commit(session)
    return [dict(r) for r in rows]


# Состояния проекта по записям журнала (после обхода всех площадок).
PS_PUBLISHED = "published"
PS_WORKING = "publishing"
PS_NEEDS_HUMAN = "needs_human"
PS_AWAITING_APPROVAL = "awaiting_approval"
PS_CLOSED = "resolved_unpublished"


def derive_project_state(rows: list[dict], platforms: list[str], approved: set[str], *,
                         legacy: dict[str, bool] | None = None, now: float | None = None,
                         publish_enabled: bool | None = None) -> dict:
    """Статус проекта считается ТОЛЬКО из записей журнала (и «старых» отметок meta до появления журнала).

    published: все площадки confirmed с URL/ID. publishing: есть работа, которую воркер доделает сам
    (занято или повтор после сбоя ДО клика с действующим подтверждением). needs_human: есть
    unconfirmed/сбой после клика/исчерпанные попытки: выход только через ручное подтверждение или пометку.
    awaiting_approval: клика не было, но нет действующего подтверждения человека. resolved_unpublished:
    остальное человек закрыл как «не опубликовано».

    publish_enabled: настройка автопубликации. None (по умолчанию) - как раньше, настройка не учитывается.
    False - шаг публикации не пойдёт, поэтому разрешённая человеком площадка НЕ «в работе»: состояние остаётся
    awaiting_approval (публикация не начата), а такие площадки перечислены в `approved_publish_off`
    (подтверждение есть, ждут включения настройки). Занятая площадка (busy) по-прежнему «в работе»."""
    summ = summarize(rows, platforms, now)
    confirmed = list(summ["confirmed"])
    human = list(summ["needs_human"])
    retry = list(summ["retryable"]) + list(summ["skipped"])
    for p in list(retry):
        if legacy and p in legacy:  # итог из старого meta без строки журнала: автоповтор запрещён
            retry.remove(p)
            (confirmed if legacy[p] else human).append(p)
    publish_off = publish_enabled is False
    approved_off = [p for p in retry if p in approved] if publish_off else []
    working_retry = [p for p in retry if p in approved] if not publish_off else []
    awaiting = [p for p in retry if p not in approved or publish_off]
    if len(set(confirmed)) == len(platforms):
        state = PS_PUBLISHED
    elif summ["busy"] or working_retry:
        state = PS_WORKING
    elif human:
        state = PS_NEEDS_HUMAN
    elif awaiting:  # при выключенной настройке сюда попадают и разрешённые площадки: шаг публикации их не возьмёт
        state = PS_AWAITING_APPROVAL
    else:
        state = PS_CLOSED
    return {"state": state, "confirmed": confirmed, "needs_human": human,
            "awaiting_approval": [p for p in awaiting if p not in approved_off],
            "approved_publish_off": approved_off, "retry_approved": working_retry, "busy": list(summ["busy"]),
            "manual_failed": list(summ["manual_failed"])}
