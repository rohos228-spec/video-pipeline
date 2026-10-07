"""Единая лента проекта (B05, W3-2): только чтение поверх существующих таблиц основной БД.

Контракт зафиксирован интегратором (docs/REGISTRAR_FULL_PLAN.md, W3):
  элемент ленты: id "<source>:<rowid>", ts (ISO UTC), kind, source, severity (info|warn|error),
  title_ru, detail_ru, project_id, run_id, request_id, ref, amount_usd.
Порядок: ts по убыванию, при равенстве (source, rowid) по убыванию. Курсор - ключ последней отданной
строки, поэтому он стабилен, пока в ленту добавляются только новые (более поздние) строки.

Побочных эффектов нет: только SELECT и PRAGMA table_info, ничего не создаётся и не меняется.
Отсутствующая таблица попадает в `sources_missing`, а не роняет запрос. Тексты проходят через `redact`.
Русские тексты: мягкий импорт `timeline_ru.describe` (W3-1); без него (или при любой его ошибке)
используются собственные запасные формулировки этого модуля.
"""
from __future__ import annotations

import base64
import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from sqlalchemy import text

from app.services.redact import redact, redact_text

SEVERITIES = ("info", "warn", "error")
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
EXPORT_MAX_ROWS = 50000
_BATCH = 500
_SCAN_CAP = 200000  # потолок просмотренных строк одного источника за запрос
_DETAIL_MAX = 400
EXPORT_COLUMNS = ("id", "ts", "kind", "source", "severity", "title_ru", "detail_ru",
                  "project_id", "run_id", "request_id", "ref", "amount_usd")


class TimelineError(ValueError):
    """Некорректный параметр запроса (роутер превращает в 422)."""


# --- время и курсор ---------------------------------------------------------------------------------

def to_dt(value) -> datetime | None:
    """Строка БД / epoch / datetime -> aware UTC. Наивное время считается UTC."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)):
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        else:
            s = str(value).strip()
            try:
                return datetime.fromtimestamp(float(s), tz=timezone.utc)
            except ValueError:
                pass
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_bound(value: str | None, name: str) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    dt = to_dt(str(value).strip())
    if dt is None:
        raise TimelineError(f"Параметр {name}: ожидается дата-время ISO")
    return dt


def encode_cursor(dt: datetime, source: str, rowid: int) -> str:
    raw = f"{iso(dt)}|{source}|{rowid}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str, int]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
        ts_s, source, rid = raw.split("|")
        dt = to_dt(ts_s)
        if dt is None:
            raise ValueError
        return dt, source, int(rid)
    except Exception as exc:  # noqa: BLE001
        raise TimelineError("Параметр cursor: неверное значение") from exc


# --- русские тексты ---------------------------------------------------------------------------------

def _clip(s: str, n: int = _DETAIL_MAX) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _safe(s: Any) -> str:
    return redact_text(str(s)) if s not in (None, "") else ""


def _payload(value) -> Any:
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _payload_text(value) -> str:
    v = _payload(value)
    if v in (None, "", {}, []):
        return ""
    if isinstance(v, str):
        return _clip(redact_text(v))
    return _clip(json.dumps(redact(v), ensure_ascii=False, sort_keys=True, default=str))


_NODE_STATUS_RU = {"pending": "ожидает", "queued": "в очереди", "running": "выполняется",
                   "waiting_hitl": "ждёт решения оператора", "done": "выполнен", "failed": "ошибка",
                   "skipped": "пропущен"}
_SPEND_KIND_RU = {"reserve": "резерв суммы", "settle": "уточнение суммы", "actual": "фактическая трата",
                  "event": "событие учёта"}

try:  # мягкий импорт W3-1: интегратор свяжет
    from app.services import timeline_ru as _ru  # type: ignore
except Exception:  # noqa: BLE001
    _ru = None


# Источники, для которых у timeline_ru.describe есть собственные фразы (остальные остаются на запасном тексте).
_RU_SOURCES = frozenset({
    "node_runs", "observation_events", "observation_incidents", "decision_records", "action_v1_records",
    "action_layer_history", "spend_ledger", "provider_task_ledger", "operator_audit", "publications",
    "notification_log"})


def _describe_row(source: str, row: dict) -> dict:
    """Строка источника в формат `timeline_ru.describe(row)`: словарь с `source_table` и колонками."""
    out = dict(row)
    out["source_table"] = source
    for key in ("proposal", "details", "payload"):
        v = out.get(key)
        if isinstance(v, str) and v.strip()[:1] in ("{", "["):
            try:
                out[key] = json.loads(v)
            except ValueError:
                pass
    if source == "node_runs":
        out["status"] = str(out.get("status") or "").replace("NodeRunStatus.", "")
    if source == "notification_log":
        if "ok" in out and "status" not in out:
            out["status"] = "sent" if out.get("ok") else "failed"
        if out.get("text") in (None, "") and out.get("text_redacted"):
            out["text"] = out["text_redacted"]
    return out


def _describe(source: str, kind: str, row: dict, title: str, detail: str,
              severity: str = "info") -> tuple[str, str, str]:
    """Спросить `timeline_ru.describe(row)` -> (title_ru, detail_ru, severity); при отсутствии/ошибке оставить запасное."""
    fn = getattr(_ru, "describe", None) if _ru is not None else None
    if not callable(fn) or source not in _RU_SOURCES:
        return title, detail, severity
    try:
        res = fn(_describe_row(source, redact(row)))
    except Exception:  # noqa: BLE001
        return title, detail, severity
    if isinstance(res, (tuple, list)) and res and isinstance(res[0], str) and res[0].strip():
        t = res[0]
        d = str(res[1]) if len(res) > 1 and res[1] else ""
        sv = res[2] if len(res) > 2 and res[2] in ("info", "warn", "error") else severity
        return t, d, sv
    return title, detail, severity


# --- описание источников ----------------------------------------------------------------------------

@dataclass
class _Plan:
    """Как читать один источник: фрагменты SQL и функция превращения строки в поля элемента."""
    from_sql: str
    ts_expr: str
    rid_expr: str
    select_sql: str
    where_sql: str
    ts_type: str  # "text" | "epoch"
    build: Callable[[dict], dict]


def _o(alias: str, cols: set[str], name: str) -> str:
    """Колонка, которой может не быть в старой базе: иначе NULL."""
    return f"{alias}.{name} AS {name}" if name in cols else f"NULL AS {name}"


def _sev(value, *, err=(), warn=()) -> str:
    v = str(value or "").lower()
    if v in err:
        return "error"
    if v in warn:
        return "warn"
    return "info"


def _plan_node_runs(cols, have):
    if "workflow_runs" not in have:
        return None
    return _Plan(
        "node_runs n JOIN workflow_runs w ON w.id = n.workflow_run_id",
        "COALESCE(n.finished_at, n.started_at, n.created_at)", "n.rowid",
        "n.node_key AS node_key, n.node_type AS node_type, n.status AS status, n.error AS error, "
        "n.attempts AS attempts, n.progress_text AS progress_text, n.workflow_run_id AS workflow_run_id, "
        "w.project_id AS project_id",
        "w.project_id = :p", "text", _b_node_run)


def _b_node_run(r):
    st = str(r.get("status") or "").replace("NodeRunStatus.", "")
    title = f"Шаг «{r.get('node_key')}» ({r.get('node_type')}): {_NODE_STATUS_RU.get(st, st)}"
    detail = " · ".join(x for x in (
        f"попытка {r['attempts']}" if r.get("attempts") else "", _safe(r.get("error")),
        _safe(r.get("progress_text"))) if x)
    return dict(kind="node_run", severity="error" if st == "failed" else "info", title_ru=title,
                detail_ru=detail, run_id=str(r["workflow_run_id"]), ref=r.get("node_key"))


def _plan_events(cols, have):
    return _Plan(
        "observation_events e", "e.created_at", "e.rowid",
        "e.kind AS kind, e.node_key AS node_key, e.execution_id AS execution_id, e.request_id AS request_id, "
        "e.payload AS payload", "e.project_id = :p", "text", _b_event)


def _b_event(r):
    kind = str(r.get("kind") or "")
    low = kind.lower()
    sev = "error" if any(k in low for k in ("fail", "error", "reject")) else (
        "warn" if any(k in low for k in ("retry", "timeout", "stall", "degrad", "uncertain")) else "info")
    node = f" (шаг «{r['node_key']}»)" if r.get("node_key") else ""
    return dict(kind=kind, severity=sev, title_ru=f"Событие «{kind}»{node}",
                detail_ru=_payload_text(r.get("payload")), run_id=r.get("execution_id"),
                request_id=r.get("request_id"), ref=r.get("node_key"))


def _plan_incidents(cols, have):
    return _Plan(
        "observation_incidents i", "i.detected_at", "i.rowid",
        "i.incident_id AS incident_id, i.execution_id AS execution_id, i.node_key AS node_key, "
        "i.type AS type, i.severity AS severity, i.status AS status, i.details AS details",
        "i.project_id = :p", "text", _b_incident)


def _b_incident(r):
    try:
        from app.services.diagnosis import TITLES
    except Exception:  # noqa: BLE001
        TITLES = {}
    typ = str(r.get("type") or "")
    title = TITLES.get(typ) or f"Инцидент «{typ}»"
    detail = " · ".join(x for x in (f"статус: {r.get('status')}" if r.get("status") else "",
                                    _payload_text(r.get("details"))) if x)
    return dict(kind="incident", severity=_sev(r.get("severity"), err=("error", "critical", "high"),
                                               warn=("warning", "warn", "medium")),
                title_ru=title, detail_ru=detail, run_id=r.get("execution_id"), ref=r.get("incident_id"))


def _plan_degradations(cols, have):
    if "project_id" not in cols:
        return None
    return _Plan(
        "observation_degradations g", "g.recorded_at", "g.rowid",
        "g.execution_id AS execution_id, g.stage AS stage, g.error_type AS error_type, "
        "g.recoverable AS recoverable, g.cleared_at AS cleared_at, g.gap_id AS gap_id",
        "g.project_id = :p", "text", _b_degradation)


def _b_degradation(r):
    closed = "закрыт" if r.get("cleared_at") else "открыт"
    return dict(kind="gap", severity="warn" if r.get("cleared_at") else "error",
                title_ru=f"Пробел журнала наблюдения на этапе «{r.get('stage')}» ({closed})",
                detail_ru=_safe(r.get("error_type")), run_id=r.get("execution_id") or None,
                ref=r.get("gap_id"))


def _plan_uncertainty(cols, have):
    return _Plan(
        "observation_commit_uncertainty u", "u.recorded_at", "u.rowid",
        "u.execution_id AS execution_id, u.reason AS reason, u.cleared_at AS cleared_at",
        "u.project_id = :p", "text", _b_uncertainty)


def _b_uncertainty(r):
    closed = "снята" if r.get("cleared_at") else "открыта"
    return dict(kind="gap", severity="warn" if r.get("cleared_at") else "error",
                title_ru=f"Неопределённость записи данных ({closed})",
                detail_ru=_safe(r.get("reason")), run_id=r.get("execution_id"))


def _plan_decisions(cols, have):
    return _Plan(
        "decision_records d", "d.created_at", "d.rowid",
        "d.decision_id AS decision_id, d.execution_id AS execution_id, d.incident_id AS incident_id, "
        "d.node_key AS node_key, d.status AS status, d.proposal AS proposal",
        "d.project_id = :p", "text", _b_decision)


def _b_decision(r):
    return dict(kind="decision", severity=_sev(r.get("status"), err=("failed", "rejected", "error"),
                                               warn=("held", "needs_human")),
                title_ru=f"Решение по инциденту (шаг «{r.get('node_key')}»): {r.get('status')}",
                detail_ru=_payload_text(r.get("proposal")), run_id=r.get("execution_id"),
                ref=r.get("decision_id"))


def _plan_actions_v1(cols, have):
    return _Plan(
        "action_v1_records a", "a.created_at", "a.rowid",
        "a.action_id AS action_id, a.execution_id AS execution_id, a.action_kind AS action_kind, "
        "a.node_key AS node_key, a.initiator AS initiator, a.rationale AS rationale, a.status AS status",
        "a.project_id = :p", "text", _b_action_v1)


def _b_action_v1(r):
    node = f" (шаг «{r['node_key']}»)" if r.get("node_key") else ""
    detail = " · ".join(x for x in (f"инициатор: {r.get('initiator')}" if r.get("initiator") else "",
                                    _safe(r.get("rationale"))) if x)
    return dict(kind="action", severity=_sev(r.get("status"), err=("failed", "rejected"),
                                             warn=("held", "needs_human")),
                title_ru=f"Действие оператора «{r.get('action_kind')}»{node}: {r.get('status')}",
                detail_ru=_clip(detail), run_id=r.get("execution_id"), ref=r.get("action_id"))


def _plan_layer(cols, have):
    scoped = []
    if "project_id" in cols:
        scoped.append("h.project_id = :p")
    if "action_layer_plan_context" in have:
        scoped.append(("(h.plan_id IN (SELECT plan_id FROM action_layer_plan_context WHERE project_id = :p))"
                       if "project_id" not in cols else
                       "(h.project_id IS NULL AND h.plan_id IN "
                       "(SELECT plan_id FROM action_layer_plan_context WHERE project_id = :p))"))
    where = "(" + " OR ".join(scoped) + ")" if scoped else "0"
    sel = ("h.plan_id AS plan_id, h.step_index AS step_index, h.event AS event, h.actor AS actor, "
           "h.request_text AS request_text, h.action_id AS action_id, h.outcome AS outcome, h.error AS error, "
           "h.cost_recorded AS cost_recorded, " + _o("h", cols, "execution_id") + ", "
           + _o("h", cols, "request_id"))
    return _Plan("action_layer_history h", "h.created_at", "h.rowid", sel, where, "text", _b_layer)


def _b_layer(r):
    out = str(r.get("outcome") or "")
    step = f", шаг {r['step_index']}" if r.get("step_index") is not None else ""
    title = f"Слой действий: {r.get('event')} (план {r.get('plan_id')}{step})"
    if r.get("action_id"):
        title += f" · {r['action_id']}"
    detail = " · ".join(x for x in (f"исход: {out}" if out else "", _safe(r.get("error")),
                                    _clip(_safe(r.get("request_text")), 200)) if x)
    return dict(kind="action", severity=_sev(out, err=("error", "failed", "rejected"),
                                             warn=("denied",)) if out else ("error" if r.get("error") else "info"),
                title_ru=title, detail_ru=_clip(detail), run_id=r.get("execution_id") or None,
                request_id=r.get("request_id") or None, ref=r.get("plan_id"),
                amount_usd=r.get("cost_recorded"))


def _plan_spend(cols, have):
    sel = ("s.kind AS kind, s.provider AS provider, s.source AS source, s.usd AS usd, s.credits AS credits, "
           "s.note AS note, s.run_id AS run_id, " + _o("s", cols, "request_id") + ", " + _o("s", cols, "ref"))
    return _Plan("spend_ledger s", "s.created_at", "s.rowid", sel, "s.project_id = :p", "text", _b_spend)


def _b_spend(r):
    k = str(r.get("kind") or "")
    usd = float(r.get("usd") or 0.0)
    who = "/".join(x for x in (r.get("provider"), r.get("source")) if x)
    note = _safe(r.get("note"))
    title = f"Деньги: {_SPEND_KIND_RU.get(k, k)}" + (f" · {who}" if who else "") + f" · {usd:+.4f} $"
    detail = " · ".join(x for x in (note, f"кредиты: {r['credits']}" if r.get("credits") else "") if x)
    return dict(kind="spend", severity="warn" if "write_failed" in note else "info", title_ru=title,
                detail_ru=detail, run_id=r.get("run_id") or None, request_id=r.get("request_id") or None,
                ref=r.get("ref") or None, amount_usd=usd)


def _plan_tasks(cols, have):
    sel = ("t.task_id AS task_id, t.provider AS provider, t.api AS api, t.model AS model, t.event AS event, "
           "t.status AS status, t.est_usd AS est_usd, t.note AS note, " + _o("t", cols, "run_id") + ", "
           + _o("t", cols, "request_id"))
    return _Plan("provider_task_ledger t", "t.created_at", "t.rowid", sel, "t.project_id = :p", "text", _b_task)


def _b_task(r):
    st = str(r.get("status") or "")
    title = f"Задача провайдера {r.get('provider')}: {r.get('event') or st}" + (
        f" ({r['model']})" if r.get("model") else "")
    detail = " · ".join(x for x in (f"статус: {st}" if st else "", _safe(r.get("note"))) if x)
    return dict(kind="task", severity=_sev(st, err=("failed", "error", "fail"), warn=("timeout", "stalled")),
                title_ru=title, detail_ru=detail, run_id=r.get("run_id") or None,
                request_id=r.get("request_id") or None, ref=r.get("task_id"), amount_usd=r.get("est_usd"))


def _plan_operator(cols, have):
    return _Plan(
        "operator_audit o", "o.ts", "o.rowid",
        "o.request_id AS request_id, o.method AS method, o.path AS path, o.route_template AS route_template, "
        "o.actor AS actor, o.status_code AS status_code, o.outcome AS outcome",
        "o.project_id = :p", "text", _b_operator)


def _b_operator(r):
    code = int(r.get("status_code") or 0)
    sev = "error" if code >= 500 else ("warn" if code >= 400 else "info")
    return dict(kind="operator", severity=sev,
                title_ru=f"Оператор: {r.get('method')} {_safe(r.get('route_template') or r.get('path'))} → {code}",
                detail_ru=" · ".join(x for x in (f"кто: {r.get('actor')}" if r.get("actor") else "",
                                                 f"исход: {r.get('outcome')}" if r.get("outcome") else "") if x),
                request_id=r.get("request_id"), ref=r.get("route_template"))


def _plan_publications(cols, have):
    return _Plan(
        "publications p", "p.created_at", "p.rowid",
        "p.platform AS platform, p.status AS status, p.post_url AS post_url, p.error_code AS error_code, "
        "p.attempts AS attempts, p.sha256 AS sha256",
        "p.project_id = :p", "epoch", _b_publication)


def _b_publication(r):
    st = str(r.get("status") or "")
    detail = " · ".join(x for x in (f"ошибка: {r['error_code']}" if r.get("error_code") else "",
                                    _safe(r.get("post_url")),
                                    f"попыток: {r['attempts']}" if r.get("attempts") else "") if x)
    return dict(kind="publication", severity=_sev(st, err=("failed", "error", "needs_human"),
                                                  warn=("skipped", "uncertain")),
                title_ru=f"Публикация на «{r.get('platform')}»: {st}", detail_ru=detail,
                ref=(r.get("sha256") or "")[:12] or None)


def _plan_notifications(cols, have):
    ts = next((c for c in ("ts", "created_at", "sent_at") if c in cols), None)
    if ts is None or "project_id" not in cols:
        return None
    pick = [c for c in ("kind", "status", "title", "text", "text_ru", "message", "error", "request_id", "run_id",
                        "ref", "channel", "ok", "text_redacted") if c in cols]
    sel = ", ".join(f"n.{c} AS {c}" for c in pick) or "NULL AS kind"
    return _Plan("notification_log n", f"n.{ts}", "n.rowid", sel, "n.project_id = :p", "text", _b_notification)


def _b_notification(r):
    st = str(r.get("status") or "")
    body = (r.get("text_ru") or r.get("text") or r.get("text_redacted") or r.get("message")
            or r.get("title") or "")
    bad = st in ("failed", "error") or bool(r.get("error")) or r.get("ok") in (0, False)
    return dict(kind="notification", severity="error" if bad else "info",
                title_ru=_clip(_safe(r.get("title") or body or f"Уведомление «{r.get('kind') or ''}»"), 160),
                detail_ru=_clip(" · ".join(x for x in (_safe(body) if body != r.get("title") else "",
                                                       _safe(r.get("error"))) if x)),
                run_id=r.get("run_id"), request_id=r.get("request_id"), ref=r.get("ref"))


# (имя источника в ленте, таблица-основа для проверки наличия, построитель плана)
_SOURCES: tuple[tuple[str, str, Callable], ...] = (
    ("node_runs", "node_runs", _plan_node_runs),
    ("observation_events", "observation_events", _plan_events),
    ("observation_incidents", "observation_incidents", _plan_incidents),
    ("observation_degradations", "observation_degradations", _plan_degradations),
    ("observation_commit_uncertainty", "observation_commit_uncertainty", _plan_uncertainty),
    ("decision_records", "decision_records", _plan_decisions),
    ("action_v1_records", "action_v1_records", _plan_actions_v1),
    ("action_layer_history", "action_layer_history", _plan_layer),
    ("spend_ledger", "spend_ledger", _plan_spend),
    ("provider_task_ledger", "provider_task_ledger", _plan_tasks),
    ("operator_audit", "operator_audit", _plan_operator),
    ("publications", "publications", _plan_publications),
    ("notification_log", "notification_log", _plan_notifications),
)
SOURCE_NAMES = tuple(s[0] for s in _SOURCES)


# --- чтение источника -------------------------------------------------------------------------------

def _bound(dt: datetime, ts_type: str):
    return dt.timestamp() if ts_type == "epoch" else dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


class _Reader:
    """Читает один источник пачками (keyset по сырому (ts, rowid)) и отдаёт отфильтрованные элементы по убыванию."""

    def __init__(self, session, name: str, plan: _Plan, project_id: int, flt: "_Filter"):
        self.session, self.name, self.plan, self.project_id, self.flt = session, name, plan, project_id, flt
        self.buf: list[dict] = []
        self.done = False
        self.scanned = 0
        self._last: tuple[Any, int] | None = None

    async def _fetch(self) -> None:
        p, f = self.plan, self.flt
        where = [p.where_sql, f"{p.ts_expr} IS NOT NULL"]
        params: dict[str, Any] = {"p": self.project_id, "n": _BATCH}
        if f.since:
            where.append(f"{p.ts_expr} >= :since")
            params["since"] = _bound(f.since, p.ts_type)
        hi = f.until
        if f.cursor and (hi is None or f.cursor[0] < hi):
            hi = f.cursor[0]
        if hi:
            where.append(f"{p.ts_expr} <= :hi")
            params["hi"] = _bound(hi, p.ts_type)
        if self._last is not None:
            where.append(f"({p.ts_expr} < :lt OR ({p.ts_expr} = :lt AND {p.rid_expr} < :lr))")
            params["lt"], params["lr"] = self._last
        sql = (f"SELECT {p.rid_expr} AS _rid, {p.ts_expr} AS _ts, {p.select_sql} FROM {p.from_sql} "
               f"WHERE {' AND '.join(where)} ORDER BY {p.ts_expr} DESC, {p.rid_expr} DESC LIMIT :n")
        rows = (await self.session.execute(text(sql), params)).mappings().all()
        if len(rows) < _BATCH:
            self.done = True
        if rows:
            self._last = (rows[-1]["_ts"], rows[-1]["_rid"])
        self.scanned += len(rows)
        if self.scanned >= _SCAN_CAP:
            self.done = True
        for row in rows:
            item = self._item(dict(row))
            if item is not None and self.flt.accept(item):
                self.buf.append(item)

    def _item(self, row: dict) -> dict | None:
        dt = to_dt(row.pop("_ts"))
        rid = int(row.pop("_rid"))
        if dt is None:
            return None
        try:
            fields = self.plan.build(row)
        except Exception:  # noqa: BLE001 - одна плохая строка не должна ронять ленту
            fields = dict(kind=self.name, severity="info", title_ru=f"Запись источника «{self.name}»",
                          detail_ru="")
        title, detail, severity = _describe(self.name, fields["kind"], row, fields["title_ru"],
                                            fields.get("detail_ru") or "", fields.get("severity", "info"))
        amount = fields.get("amount_usd")
        return {
            "id": f"{self.name}:{rid}", "ts": iso(dt), "kind": fields["kind"], "source": self.name,
            "severity": severity, "title_ru": _clip(_safe(title), 300),
            "detail_ru": _clip(_safe(detail)), "project_id": self.project_id,
            "run_id": fields.get("run_id") or None, "request_id": fields.get("request_id") or None,
            "ref": str(fields["ref"]) if fields.get("ref") not in (None, "") else None,
            "amount_usd": float(amount) if amount is not None else None,
            "_key": (dt, self.name, rid),
        }

    async def head(self) -> dict | None:
        while not self.buf and not self.done:
            await self._fetch()
        return self.buf[0] if self.buf else None

    def pop(self) -> dict:
        return self.buf.pop(0)


@dataclass
class _Filter:
    since: datetime | None = None
    until: datetime | None = None
    kinds: frozenset[str] | None = None
    q: str = ""
    severity: str | None = None
    cursor: tuple[datetime, str, int] | None = None

    def accept(self, it: dict) -> bool:
        key = it["_key"]
        if self.cursor is not None and not key < self.cursor_key:
            return False
        if self.since and key[0] < self.since:
            return False
        if self.until and key[0] > self.until:
            return False
        if self.severity and it["severity"] != self.severity:
            return False
        if self.kinds is not None and it["kind"] not in self.kinds and it["source"] not in self.kinds:
            return False
        if self.q:
            hay = " ".join(str(it.get(k) or "") for k in
                           ("title_ru", "detail_ru", "kind", "source", "ref", "run_id", "request_id")).lower()
            if self.q not in hay:
                return False
        return True

    @property
    def cursor_key(self):
        return self.cursor


def make_filter(*, since=None, until=None, kinds=None, q=None, severity=None, cursor=None) -> _Filter:
    if severity not in (None, "") and severity not in SEVERITIES:
        raise TimelineError("Параметр severity: info, warn или error")
    ks = frozenset(k.strip() for k in str(kinds).split(",") if k.strip()) if kinds else None
    return _Filter(since=parse_bound(since, "since"), until=parse_bound(until, "until"), kinds=ks or None,
                   q=(q or "").strip().lower(), severity=severity or None,
                   cursor=decode_cursor(cursor) if cursor else None)


async def _schema(session) -> tuple[set[str], dict[str, set[str]]]:
    rows = (await session.execute(text("SELECT name FROM sqlite_master WHERE type IN ('table','view')"))).all()
    have = {r[0] for r in rows}
    cols: dict[str, set[str]] = {}
    for _, table, _fn in _SOURCES:
        if table in have:
            info = (await session.execute(text(f'PRAGMA table_info("{table}")'))).all()
            cols[table] = {r[1] for r in info}
    return have, cols


async def collect(session, project_id: int, flt: _Filter, limit: int) -> dict:
    """Собрать до `limit` элементов ленты и курсор следующей страницы."""
    have, cols = await _schema(session)
    missing: list[str] = []
    readers: list[_Reader] = []
    for name, table, builder in _SOURCES:
        if table not in have:
            missing.append(name)
            continue
        plan = builder(cols[table], have)
        if plan is None:
            missing.append(name)
            continue
        readers.append(_Reader(session, name, plan, project_id, flt))
    out: list[dict] = []
    more = False
    while True:
        heads = []
        for rd in readers:
            h = await rd.head()
            if h is not None:
                heads.append((h["_key"], rd))
        if not heads:
            break
        if len(out) >= limit:
            more = True
            break
        _, best = max(heads, key=lambda x: x[0])
        out.append(best.pop())
    next_cursor = None
    if more and out:
        k = out[-1]["_key"]
        next_cursor = encode_cursor(k[0], k[1], k[2])
    for it in out:
        it.pop("_key", None)
    return {"items": out, "next_cursor": next_cursor, "total_hint": None, "sources_missing": missing}


async def timeline(session, project_id: int, *, since=None, until=None, kinds=None, q=None, severity=None,
                   limit: int = DEFAULT_LIMIT, cursor=None) -> dict:
    if not 1 <= int(limit) <= MAX_LIMIT:
        raise TimelineError(f"Параметр limit: от 1 до {MAX_LIMIT}")
    flt = make_filter(since=since, until=until, kinds=kinds, q=q, severity=severity, cursor=cursor)
    return await collect(session, project_id, flt, int(limit))


# --- экспорт ----------------------------------------------------------------------------------------

def _csv_cell(v):
    if v is None:
        return ""
    s = str(v)
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s  # защита от формул в Excel


def render_csv(items: list[dict]) -> bytes:
    buf = io.StringIO(newline="")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(EXPORT_COLUMNS)
    for it in items:
        w.writerow([_csv_cell(it.get(c)) for c in EXPORT_COLUMNS])
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def render_json(result: dict, truncated: bool) -> bytes:
    body = {"items": result["items"], "sources_missing": result["sources_missing"], "truncated": truncated}
    return json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")


async def export(session, project_id: int, fmt: str, *, since=None, until=None, kinds=None, q=None,
                 severity=None) -> tuple[bytes, str, str]:
    """-> (тело, media_type, расширение файла). Не больше EXPORT_MAX_ROWS строк."""
    if fmt not in ("csv", "json"):
        raise TimelineError("Параметр format: csv или json")
    flt = make_filter(since=since, until=until, kinds=kinds, q=q, severity=severity)
    res = await collect(session, project_id, flt, EXPORT_MAX_ROWS)
    truncated = res["next_cursor"] is not None
    if fmt == "csv":
        return render_csv(res["items"]), "text/csv; charset=utf-8", "csv"
    return render_json(res, truncated), "application/json; charset=utf-8", "json"
